from datetime import datetime

from flask import flash, redirect, render_template, request, session, url_for
from flask_login import current_user

from app.extensions import db
from app.models import TurnoAtivo, Usuario
from app.utils.decorators import roles_required

from . import atendimento_bp
from .filas import PERFIS_OPERACIONAIS
from .helpers import CHAVE_SESSAO_LOCAL, filas_acessiveis, locais_acessiveis, turno_ativo_do_usuario
from .local import _setor_padrao_usuario


def _mesclar_filas_config(pares):
    """Junta entradas repetidas da mesma fila em uma só (soma de chamadas_seguidas) -
    o operador pode adicionar a mesma fila mais de uma vez na tela 'Iniciar Turno'
    só para ajustar o peso dela no rodízio (pedido explícito); mesclar aqui evita
    salvar `fila_id` duplicado em `filas_config`, o que deixaria ambíguo qual
    entrada é "a vez" no motor de rodízio (`_proximo_item_na_rotacao` identifica o
    item atual pelo `fila_id`, não pela posição)."""
    somas = {}
    ordem = []
    for fila_id, chamadas_seguidas in pares:
        if fila_id not in somas:
            somas[fila_id] = 0
            ordem.append(fila_id)
        somas[fila_id] += chamadas_seguidas
    return [
        {"fila_id": fila_id, "ordem": indice, "chamadas_seguidas": somas[fila_id]}
        for indice, fila_id in enumerate(ordem)
    ]


def _contexto_formulario(filas_selecionadas_iniciais=None, local_selecionado_id=None):
    filas = filas_acessiveis()
    locais = locais_acessiveis()
    return {
        # `unidade`/`unidade_sigla` alimentam o <optgroup>/prefixo "[SIGLA] Nome" do
        # select de FILAS (UX de contexto multi-unidade) - filas_acessiveis()
        # já pode abranger mais de uma unidade quando o operador tem acesso a
        # várias. O select de LOCAIS usa identificador_admin diretamente (já
        # vem com o prefixo "[SIGLA] nome" embutido - BLUEPRINT.md, seção
        # 10.32), sem precisar remontar o prefixo aqui.
        "filas_json": [
            {"id": f.id, "nome": f.nome, "unidade": f.unidade.nome, "unidade_sigla": f.unidade.sigla_exibicao}
            for f in filas
        ],
        "locais_json": [
            {"id": l.id, "identificador_admin": l.identificador_admin, "unidade": l.unidade.nome}
            for l in locais
        ],
        "filas_selecionadas_iniciais": filas_selecionadas_iniciais or [],
        "local_selecionado_id": local_selecionado_id,
    }


@atendimento_bp.route("/iniciar-turno", methods=["GET", "POST"])
@roles_required(*PERFIS_OPERACIONAIS)
def iniciar_turno():
    """Seção 10.19/10.23: tela padronizada como ponto de entrada SEMPRE que um
    operador (perfis atendente/executor/atendente_executor) faz login - o próprio
    login já redireciona pra cá (`auth/routes.py::_deve_ir_direto_para_turno`).

    GET sempre renderiza o formulário - com um turno ativo (`TurnoAtivo`), vem
    pré-populado com a configuração atual (filas + peso + Local), pra o operador
    conferir/ajustar antes de confirmar "Iniciar atendimento"; sem turno ativo,
    vem vazio. Não pula mais direto pra tela de atendimento (comportamento antigo,
    revertido a pedido do cliente: a tela precisa aparecer em TODO login, nunca
    ser contornada silenciosamente) - o operador sempre passa por aqui, mesmo
    reabrindo a sessão com um turno já configurado (recuperação de estado do
    adendo 10.11 continua valendo: é exatamente o pré-preenchimento que mostra o
    que já estava salvo, sem o operador precisar reconfigurar do zero)."""
    turno = turno_ativo_do_usuario(current_user)

    if request.method == "GET":
        filas_iniciais = list(turno.filas_config) if turno is not None else []
        local_inicial = turno.local_id if turno is not None else None
        return render_template("atendimento/iniciar_turno.html", **_contexto_formulario(filas_iniciais, local_inicial))

    fila_ids_brutos = request.form.getlist("fila_id")
    seguidas_brutas = request.form.getlist("chamadas_seguidas")
    local_id = request.form.get("local_id", type=int)

    filas_por_id = {f.id: f for f in filas_acessiveis()}
    locais_por_id = {l.id: l for l in locais_acessiveis()}

    pares = []
    if len(fila_ids_brutos) == len(seguidas_brutas):
        for bruto_fila, bruto_seguidas in zip(fila_ids_brutos, seguidas_brutas):
            try:
                fila_id = int(bruto_fila)
                seguidas = max(0, int(bruto_seguidas))
            except (TypeError, ValueError):
                continue
            if fila_id in filas_por_id:
                pares.append((fila_id, seguidas))

    filas_config_tentativa = _mesclar_filas_config(pares)

    if not pares:
        flash("Adicione ao menos uma fila ao turno.", "error")
        return render_template(
            "atendimento/iniciar_turno.html",
            **_contexto_formulario(filas_config_tentativa, local_id),
        )

    local = locais_por_id.get(local_id)
    if local is None:
        flash("Selecione um local válido para o turno.", "error")
        return render_template(
            "atendimento/iniciar_turno.html",
            **_contexto_formulario(filas_config_tentativa, local_id),
        )

    # Um local = um operador por vez (mesma regra de escolher_local) - só um admin
    # pode liberar via a tela de ocupação de locais.
    ocupante = Usuario.query.filter(Usuario.local_atual_id == local.id, Usuario.id != current_user.id).first()
    if ocupante is not None:
        flash(
            f"O local '{local.identificador_admin}' já está em uso por {ocupante.nome}. Peça a um "
            "administrador para desconectá-lo (tela de ocupação de locais), ou escolha outro local.",
            "error",
        )
        return render_template(
            "atendimento/iniciar_turno.html",
            **_contexto_formulario(filas_config_tentativa, local_id),
        )

    filas_config = filas_config_tentativa

    if turno is None:
        # reaproveita uma linha antiga desativada deste usuário, se existir (unique
        # constraint em usuario_id) - reiniciar o turno não deve acumular histórico.
        turno = TurnoAtivo.query.filter_by(usuario_id=current_user.id).first()
    if turno is None:
        turno = TurnoAtivo(usuario_id=current_user.id)
        db.session.add(turno)

    turno.local_id = local.id
    turno.filas_config = filas_config
    turno.iniciado_em = datetime.utcnow()
    turno.ativo = True

    # mesma persistência de sempre (escolher_local) - mantém compatível com a tela
    # por-fila legada (_exigir_local_ou_redirecionar/local_atual, seção 10.3) e com
    # a tela administrativa de ocupação de locais.
    session[CHAVE_SESSAO_LOCAL] = local.id
    current_user.local_atual_id = local.id
    current_user.local_desde = datetime.utcnow()
    db.session.commit()

    flash(f"Turno iniciado em '{local.identificador_admin}' com {len(filas_config)} fila(s).", "info")
    return redirect(url_for("atendimento.painel_local", setor=_setor_padrao_usuario(), local_id=local.id))


@atendimento_bp.route("/turno/encerrar", methods=["POST"])
@roles_required(*PERFIS_OPERACIONAIS)
def encerrar_turno():
    """Botão 'Encerrar turno' (seção 10.19, além do 'Sair' já existente) - limpa a
    configuração de filas/local (marca `TurnoAtivo.ativo = False`, mesmo campo usado
    pelo encerramento automático às 23h) e volta para 'Iniciar Turno', SEM deslogar:
    o operador pode iniciar um novo turno em outro Local/filas sem precisar logar de
    novo."""
    turno = turno_ativo_do_usuario(current_user)
    if turno is not None:
        turno.ativo = False

    current_user.local_atual_id = None
    current_user.local_desde = None
    session.pop(CHAVE_SESSAO_LOCAL, None)
    db.session.commit()

    flash("Turno encerrado.", "info")
    return redirect(url_for("atendimento.iniciar_turno"))
