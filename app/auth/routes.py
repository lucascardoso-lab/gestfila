from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user

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
            login_user(usuario, remember=request.form.get("lembrar") == "on")
            # grava a versão atual no cookie de sessão - é contra esse número que
            # load_user() (app/__init__.py) compara a cada request, pra detectar
            # um logout forçado remotamente (Usuario.sessao_versao).
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
    # 'Encerrar turno' (turno.py::encerrar_turno) já tratava - mesmo efeito aqui,
    # pra sempre logar com a tela 'Iniciar Turno' zerada.
    TurnoAtivo.query.filter_by(usuario_id=current_user.id, ativo=True).update({"ativo": False})
    db.session.commit()

    logout_user()
    flash("Você saiu do sistema.", "info")
    return redirect(url_for("auth.login"))
