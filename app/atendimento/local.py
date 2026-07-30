from datetime import datetime
from urllib.parse import urlparse

from flask import flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.models import Usuario

from . import atendimento_bp
from .helpers import CHAVE_SESSAO_LOCAL, PERFIS_POR_SETOR, locais_acessiveis


def _destino_seguro(bruto, padrao):
    """Só aceita `next` relativo (sem scheme/netloc) - evita open redirect."""
    if bruto and not urlparse(bruto).netloc and not urlparse(bruto).scheme:
        return bruto
    return padrao


def _setor_padrao_usuario():
    """Setor usado no redirecionamento automático pro rodízio (Fase 10.8-F) quando
    não há 'next' explícito - só entra em jogo nesse caso (ver escolher_local), já
    que aqui não temos nenhuma pista de qual tela o operador estava tentando
    acessar. Prioriza 'recepcao' (mesma ordem de exibição de listar_filas.html).

    Só é chamada depois de confirmar (_tem_acesso_operacional) que o usuário tem
    pelo menos um dos dois setores - nunca escolhe 'coleta' como fallback cego para
    quem não tem nenhum dos dois, o que cairia em 403 em exigir_acesso_setor."""
    if current_user.is_super_admin or current_user.tem_perfil(*PERFIS_POR_SETOR["recepcao"]):
        return "recepcao"
    return "coleta"


def _tem_acesso_operacional():
    """Só atendente/executor/atendente_executor/admin acessam alguma tela de
    atendimento (por-fila ou por-Local) - exigir_acesso_setor rejeita qualquer
    outro perfil (ex.: gerencial puro) nos dois setores. escolher_local() não tem
    nenhuma restrição de perfil (só @login_required) e locais_acessiveis() também
    não filtra por perfil, então um usuário gerencial pode chegar até aqui e
    escolher um Local com rodízio configurado - sem esta checagem, o
    redirecionamento automático da Fase 10.8-F mandaria esse usuário direto para
    um 403, em vez do fallback antigo (listar_filas) que ele já usaria
    normalmente."""
    return current_user.is_super_admin or current_user.tem_perfil(
        "atendente", "executor", "atendente_executor", "admin"
    )


@atendimento_bp.route("/local", methods=["GET", "POST"])
@login_required
def escolher_local():
    """Seção 10.3: o Local não é mais texto livre - o operador escolhe entre os
    cadastrados para as unidades que ele tem permissão de acessar, uma vez por
    turno/login (fica salvo na sessão), com um link "Trocar local" para refazer
    a escolha sem precisar deslogar.

    Fase 10.8-F: sem 'next' explícito, um Local com rodízio configurado
    (`local.rodizio_ordenado` não vazio) manda direto para a tela unificada
    `painel_local`, em vez do fallback antigo (`listar_filas`) - "sugestão de fluxo
    automático" (seção 3/5/6 do adendo 10.8), nunca bloqueio: um 'next' explícito
    (ex.: o link "Trocar local" de uma tela já aberta, ou o redirecionamento de
    `_exigir_local_ou_redirecionar` de volta para uma fila específica) sempre tem
    prioridade e nunca é sobrescrito por este redirecionamento automático. Local
    SEM rodízio configurado continua caindo no mesmo fallback de sempre
    (`listar_filas`), qualquer que seja o valor de 'next' - nenhuma mudança de
    comportamento para esses Locais (requisito explícito da seção 5 do adendo).

    Usuário sem acesso a nenhum dos dois setores (`_tem_acesso_operacional`) -
    ex.: perfil só `gerencial` - também cai sempre em `listar_filas`, mesmo que o
    Local escolhido tenha rodízio configurado: redirecionar esse usuário para
    `painel_local` só resultaria num 403 imediato em `exigir_acesso_setor`, pior
    experiência do que o fallback antigo (que ele já usaria normalmente).
    """
    locais = locais_acessiveis()
    # guardado "cru" (sem resolver fallback ainda) para sobreviver ao roundtrip
    # GET -> campo oculto do formulário -> POST sem perder a informação de que
    # NENHUM 'next' foi explicitamente informado (distinção necessária para saber
    # se o redirecionamento automático desta fase deve entrar em jogo ou não).
    next_bruto = request.values.get("next") or ""

    if request.method == "POST":
        local_id = request.form.get("local_id", type=int)
        escolhido = next((l for l in locais if l.id == local_id), None)
        if escolhido is None:
            flash("Selecione um local válido.", "error")
            return render_template("atendimento/escolher_local.html", locais=locais, next=next_bruto)

        # Um local = um operador por vez - antes dois usuários podiam escolher o
        # mesmo Local ao mesmo tempo sem nenhum aviso (ambos apareceriam chamando
        # da mesma "Coleta A 01", por exemplo). Se o ocupante atual for outra pessoa,
        # bloqueia aqui; só um admin pode liberar (tela de ocupação de locais).
        ocupante = Usuario.query.filter(
            Usuario.local_atual_id == escolhido.id, Usuario.id != current_user.id
        ).first()
        if ocupante is not None:
            flash(
                f"O local '{escolhido.identificador_admin}' já está em uso por {ocupante.nome}. Peça a um "
                "administrador para desconectá-lo (tela de ocupação de locais), ou escolha outro local.",
                "error",
            )
            return render_template("atendimento/escolher_local.html", locais=locais, next=next_bruto)

        session[CHAVE_SESSAO_LOCAL] = escolhido.id
        # persiste no servidor (diagnóstico item 1) - antes só vivia na sessão do
        # navegador, invisível a qualquer query/tela administrativa.
        current_user.local_atual_id = escolhido.id
        current_user.local_desde = datetime.utcnow()
        db.session.commit()
        flash(f"Local de atendimento: {escolhido.identificador_admin}.", "info")

        destino_padrao = url_for("atendimento.listar_filas")
        if escolhido.rodizio_ordenado and _tem_acesso_operacional():
            destino_padrao = url_for(
                "atendimento.painel_local", setor=_setor_padrao_usuario(), local_id=escolhido.id
            )
        return redirect(_destino_seguro(next_bruto, destino_padrao))

    return render_template("atendimento/escolher_local.html", locais=locais, next=next_bruto)
