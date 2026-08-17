from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import update

from app.extensions import db, limiter
from app.models import TurnoAtivo, Usuario

auth_bp = Blueprint("auth", __name__)


def _deve_ir_direto_para_turno(usuario):
    """Seção 10.19: perfis puramente operacionais (atendente/executor - setores
    recepção/coleta) caem direto na tela 'Iniciar Turno' após o login, em vez do
    dashboard operacional (core.dashboard, adendo 10.12.2) - fluxo Login -> Iniciar
    Turno -> Atendimento pedido explicitamente. Quem também tem perfil admin/
    gerencial, ou é super admin, continua caindo no dashboard como antes - eles
    usam o sistema para mais do que atendimento, então o dashboard continua sendo
    o ponto de partida mais útil."""
    if usuario.is_super_admin or usuario.tem_perfil("admin", "gerencial"):
        return False
    return usuario.tem_perfil("atendente", "executor", "atendente_executor")


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def login():
    if current_user.is_authenticated:
        return redirect(url_for("core.dashboard"))

    if request.method == "POST":
        login_informado = request.form.get("login", "").strip()
        senha_informada = request.form.get("senha", "")

        usuario = Usuario.query.filter_by(login=login_informado).first()

        if usuario and usuario.ativo and usuario.checar_senha(senha_informada):
            # Single-session enforcement: incrementa ANTES de gravar o cookie,
            # derrubando qualquer sessão anterior deste MESMO usuário (em
            # qualquer dispositivo/aba) - reaproveita o mesmo mecanismo de
            # logout remoto que admin/locais.py::desconectar_usuario_local já
            # usa (mesma ordem: incrementa e persiste antes de qualquer outra
            # coisa). Sem isto, duas sessões do mesmo usuário conviviam
            # normalmente (ambas batiam com o número do banco em load_user()),
            # o que permitia dois logins simultâneos "brigarem" em silêncio
            # pelo mesmo Local (local_atual_id, seção 10.15).
            #
            # UPDATE real (não `usuario.sessao_versao = usuario.sessao_versao +
            # 1` em Python) pelo mesmo motivo já documentado em app/totem/
            # routes.py::_emitir_senha: um read-then-write em Python não é
            # atômico - dois POST /login do MESMO usuário genuinamente
            # simultâneos poderiam ambos ler o valor antigo antes de qualquer
            # um commitar, calcular o mesmo próximo valor e as duas sessões
            # nasceriam válidas ao mesmo tempo (a fresta que este mecanismo
            # existe pra fechar). `sessao_versao = sessao_versao + 1` direto no
            # SQL é serializado pelo lock de linha do MySQL/InnoDB (e força
            # escrita real também no SQLite dos testes, mesmo efeito).
            db.session.execute(
                update(Usuario).where(Usuario.id == usuario.id).values(sessao_versao=Usuario.sessao_versao + 1)
            )
            db.session.commit()
            # `usuario` foi expirado pelo commit acima (expire_on_commit padrão
            # do SQLAlchemy) - o próximo acesso a `.sessao_versao` já recarrega
            # do banco o valor recém-persistido, não um valor calculado em
            # memória.

            login_user(usuario, remember=request.form.get("lembrar") == "on")
            # grava a versão JÁ INCREMENTADA (pós-commit acima, recarregada do
            # banco) no cookie desta sessão nova - se gravasse o valor antigo,
            # ela nasceria já invalidada na primeira requisição seguinte
            # (load_user() compara contra o valor atual do banco, que já é o
            # novo).
            session["sessao_versao"] = usuario.sessao_versao
            destino = request.args.get("next")
            if destino is None and _deve_ir_direto_para_turno(usuario):
                destino = url_for("atendimento.iniciar_turno")
            return redirect(destino or url_for("core.dashboard"))

        flash("Login ou senha inválidos.", "error")

    return render_template("auth/login.html")


@auth_bp.route("/trocar-senha-obrigatoria", methods=["GET", "POST"])
@login_required
def trocar_senha_obrigatoria():
    """Bloqueia o acesso ao resto do sistema até o usuário trocar a senha
    (`Usuario.deve_trocar_senha` - checklist de segurança pra publicação no
    GitHub: o superadmin semeado nasce com esse flag, pra uma senha temporária
    gerada aleatoriamente nunca virar permanente por esquecimento). O
    middleware (app/middleware/forcar_troca_senha.py) já garante que nenhuma
    outra tela é alcançável enquanto o flag for True - aqui só sai se ele já
    não se aplica mais (evita a tela ficar acessível "à toa" depois de trocada).
    """
    if not current_user.deve_trocar_senha:
        return redirect(url_for("core.dashboard"))

    if request.method == "POST":
        nova_senha = request.form.get("nova_senha", "")
        confirmar_senha = request.form.get("confirmar_senha", "")

        if len(nova_senha) < 8:
            flash("A nova senha precisa ter pelo menos 8 caracteres.", "error")
        elif nova_senha != confirmar_senha:
            flash("As senhas não coincidem.", "error")
        else:
            current_user.set_senha(nova_senha)
            current_user.deve_trocar_senha = False
            db.session.commit()
            flash("Senha alterada com sucesso.", "info")
            return redirect(url_for("core.dashboard"))

    return render_template("auth/trocar_senha_obrigatoria.html")


@auth_bp.route("/logout")
@login_required
def logout():
    # limpa o Local persistido (diagnóstico item 1) antes de derrubar a sessão -
    # current_user vira AnonymousUserMixin depois de logout_user().
    current_user.local_atual_id = None
    current_user.local_desde = None

    # Bug corrigido (seção 10.23): deslogar não encerrava o turno (TurnoAtivo.ativo
    # continuava True) - no próximo login, 'Iniciar Turno' pré-populava com as
    # filas/Local do turno anterior, mesmo o operador tendo saído de propósito.
    # Um logout explícito é o mesmo sinal de "terminei o turno" que o botão
    # 'Encerrar turno' (turno.py::encerrar_turno) já tratava - mesmo efeito aqui:
    # marca o turno como inativo, o que libera o Local (não pré-popula mais
    # sozinho no próximo login - continua exigido do zero) e some da prioridade
    # do motor de rodízio (turno_ativo_do_usuario filtra ativo=True).
    #
    # REVERSÃO PARCIAL DELIBERADA (seção 10.57, BLUEPRINT.md - pedido do
    # cliente): "pra sempre logar com a tela 'Iniciar Turno' zerada" NÃO vale
    # mais pra FILAS - a pré-população de filas (turno.py::iniciar_turno, GET)
    # passou a usar `ultima_configuracao_filas_do_usuario`, que ignora `ativo`
    # de propósito e sempre mostra a última lista salva, mesmo pós-logout. Esta
    # linha abaixo continua fazendo exatamente o que sempre fez (só inativa o
    # turno, nunca apagou `filas_config`) - a mudança de comportamento está na
    # LEITURA (turno.py), não aqui.
    TurnoAtivo.query.filter_by(usuario_id=current_user.id, ativo=True).update({"ativo": False})
    db.session.commit()

    logout_user()
    flash("Você saiu do sistema.", "info")
    return redirect(url_for("auth.login"))
