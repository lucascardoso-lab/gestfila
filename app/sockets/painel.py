from flask import request
from flask_login import current_user
from flask_socketio import emit, join_room, rooms

from app.atendimento.helpers import historico_chamadas_tv
from app.extensions import socketio
from app.models import Fila, Tv

NAMESPACE = "/painel"


@socketio.on("connect", namespace=NAMESPACE)
def conectar():
    emit("conectado", {"sid": request.sid})


@socketio.on("entrar_tv", namespace=NAMESPACE)
def entrar_tv(dados):
    """O painel de uma TV pede para entrar na sua sala (`tv_<id>`) e, automaticamente,
    nas salas de todas as filas que ela exibe (`fila_<id>`, via `tv_filas`).

    Eventos futuros de chamada de senha (Fase 7) poderão ser emitidos só para a sala da
    fila, sem o backend precisar saber individualmente quais/quantas TVs estão exibindo
    aquela fila no momento - é exatamente esse o motivo de existirem as duas camadas de
    sala (por TV e por fila), conforme a seção 6 do BLUEPRINT.
    """
    tv_id = (dados or {}).get("tv_id")
    tv = Tv.query.get(tv_id) if tv_id is not None else None

    if tv is None or not tv.ativo:
        emit("erro", {"mensagem": "TV não encontrada ou inativa."})
        return

    sala_tv = f"tv_{tv.id}"
    join_room(sala_tv)
    salas = [sala_tv]

    for fila in tv.filas:
        sala_fila = f"fila_{fila.id}"
        join_room(sala_fila)
        salas.append(sala_fila)

    # histórico 'de hoje' (bug corrigido - seção 6/8 do BLUEPRINT): recalculado toda vez
    # que a TV (re)conecta, para sobreviver a reload e a quedas de rede sem perder o que
    # já foi chamado - ver historico_chamadas_tv() para os detalhes/limitações.
    historico, total_hoje = historico_chamadas_tv(tv)
    emit("entrou", {"tv_id": tv.id, "salas": salas, "historico": historico, "total_hoje": total_hoje})


@socketio.on("entrar_fila", namespace=NAMESPACE)
def entrar_fila(dados):
    """A tela de Atendimento (seção 10.2) entra direto na sala `fila_<id>` - a mesma
    usada pela TV - para receber o sinal 'fila_atualizada' e re-sincronizar as tabelas
    de espera/atendidas sem reload. Diferente de `entrar_tv`, exige login: aqui não
    passamos por `g.empresa_id` (populado só em requests HTTP via before_request, que
    não roda para eventos de socket), então a checagem de tenant usa `current_user`
    diretamente - mesma garantia, fonte de dados diferente.
    """
    if not current_user.is_authenticated:
        emit("erro", {"mensagem": "Não autenticado."})
        return

    fila_id = (dados or {}).get("fila_id")
    fila = Fila.query.get(fila_id) if fila_id is not None else None

    if fila is None:
        emit("erro", {"mensagem": "Fila não encontrada."})
        return

    if not current_user.is_super_admin:
        if fila.empresa_id != current_user.empresa_id or not current_user.tem_acesso_unidade(fila.unidade_id):
            emit("erro", {"mensagem": "Sem acesso a esta fila."})
            return

    sala_fila = f"fila_{fila.id}"
    join_room(sala_fila)
    emit("entrou", {"fila_id": fila.id, "salas": [sala_fila]})


@socketio.on("ping", namespace=NAMESPACE)
def ping(dados=None):
    """Teste simples de conectividade ponta a ponta - resposta direta ao remetente,
    sem depender de ter entrado em nenhuma sala antes."""
    emit("pong", {"recebido": dados or {}})


@socketio.on("ping_sala", namespace=NAMESPACE)
def ping_sala(dados):
    """Teste de broadcast por sala - confirma que múltiplos clientes na mesma sala
    recebem o mesmo evento. Só funciona em uma sala que o remetente já tenha entrado
    (evita que qualquer cliente publique em salas arbitrárias de outras TVs/filas)."""
    sala = (dados or {}).get("sala")

    if not sala or sala not in rooms(namespace=NAMESPACE):
        emit("erro", {"mensagem": "Você precisa entrar na sala antes de testá-la."})
        return

    emit("pong_sala", {"sala": sala}, room=sala, namespace=NAMESPACE)
