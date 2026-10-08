from datetime import datetime

from flask import flash, redirect, render_template, request, session, url_for
from flask_login import current_user

from app.extensions import db
from app.models import TurnoAtivo, Usuario
from app.utils.decorators import roles_required
from app.utils.rotulos import rotulo_fila

from . import atendimento_bp
from .filas import PERFIS_OPERACIONAIS
from .helpers import (
    CHAVE_SESSAO_LOCAL,
    filas_acessiveis,
    locais_acessiveis,
    turno_ativo_do_usuario,
    ultima_configuracao_filas_do_usuario,
)
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
        # `rotulo` ("[SIGLA] Nome", app/utils/rotulos.py, adendo 10.70) é o que
        # a busca de FILAS e a tabela "Filas do turno" exibem - filas_acessiveis()
        # já pode abranger mais de uma unidade quando o operador tem acesso a
        # várias. A BUSCA continua por `nome` puro (digitar "coleta" acha todas
        # as Coletas). O select de LOCAIS usa identificador_admin diretamente
        # (já vem com o prefixo "[SIGLA] nome" embutido - BLUEPRINT.md, seção
        # 10.32), sem precisar remontar o prefixo aqui.
        "filas_json": [
            {
                "id": f.id, "nome": f.nome, "rotulo": rotulo_fila(f),
                # `aberta` (Fila.aberta_em(), seção 10.1/10.5) alimenta o mesmo marcador
                # "Atendendo agora"/"Fora do horário" do widget de busca da seção 10.10 -
                # reaproveitado aqui, adendo da busca com autocomplete em "Iniciar turno".
                "aberta": f.aberta_em(),
            }
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
    """Seção 10.19/10.23/10.57: tela padronizada como ponto de entrada SEMPRE que
    um operador (perfis atendente/executor/atendente_executor) faz login - o
    próprio login já redireciona pra cá (`auth/routes.py::_deve_ir_direto_para_turno`).

    GET sempre renderiza o formulário. Não pula mais direto pra tela de
    atendimento (comportamento antigo, revertido a pedido do cliente na 10.23:
    a tela precisa aparecer em TODO login, nunca ser contornada silenciosamente)
    - o operador sempre passa por aqui.

    Pré-população, DUAS fontes independentes (seção 10.57 - reversão deliberada
    de parte da 10.23, ver BLUEPRINT.md):
    - FILAS: sempre pré-populadas com a ÚLTIMA configuração salva deste
      operador (`ultima_configuracao_filas_do_usuario`), MESMO se o turno já
      foi encerrado por logout/'Encerrar turno'/23h - o operador não precisa
      remontar a lista de filas toda vez que loga de novo.
    - LOCAL: só pré-populado se houver um turno ATIVO agora
      (`turno_ativo_do_usuario`) - continua exigido do zero a cada login
      quando não há turno em andamento, SEM mudança nesse ponto (decisão
      explícita: só as filas persistem, não o Local)."""
    turno = turno_ativo_do_usuario(current_user)

    if request.method == "GET":
        filas_iniciais = ultima_configuracao_filas_do_usuario(current_user)
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
