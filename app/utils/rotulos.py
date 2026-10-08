"""Rótulo de exibição de fila "[SIGLA] Nome" (adendos 10.69/10.70) - fonte
ÚNICA, usada por relatórios, dashboards, telas de atendimento e cadastros
admin. Nenhum template ou JS concatena sigla e nome por conta própria.

Nunca usado em telas de PACIENTE (painel de TV, cupom do totem): o prefixo de
unidade e o desempate por id são identificação administrativa/operacional."""
import unicodedata
from collections import defaultdict

from flask import g, has_request_context
from sqlalchemy.orm import joinedload


def _chave_colisao(texto):
    """Mesma noção de "igual" da collation `utf8mb4_0900_ai_ci` de `filas.nome`
    (case- E accent-insensitive): "COLETA", "Coleta" e "Cóleta" colidem."""
    sem_acento = "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))
    return sem_acento.strip().casefold()


def rotulos_filas(empresa_id):
    """{fila_id: rótulo de exibição} de todas as filas da empresa.

    Formato "[SIGLA] Nome" (Unidade.sigla_exibicao, adendo 10.22). Se, mesmo
    com o prefixo, sobrar colisão (nome repetido na MESMA unidade, ou duas
    unidades sem sigla cadastrada cujo fallback de 3 letras coincide - ex.:
    "Anápolis - Centro Médico"/"Anápolis - Laboratório" -> "[ANÁ]"), " (id)" é
    anexado a TODOS os membros do grupo colidente, nunca só aos "duplicados" -
    mesma regra do 10.32 item 3 para Local.identificador_admin.

    Calculado sobre as filas da EMPRESA INTEIRA - inclusive inativas e de
    unidades que o usuário não vê -, nunca sobre o subconjunto da página: o
    mesmo id gera sempre o mesmo rótulo em qualquer tela/perfil, e o rótulo
    não muda quando outra fila do grupo é ativada/desativada."""
    from app.models import Fila

    filas = Fila.query.filter_by(empresa_id=empresa_id).options(joinedload(Fila.unidade)).all()
    base = {f.id: f"[{f.unidade.sigla_exibicao}] {f.nome}" for f in filas}

    grupos = defaultdict(list)
    for fila_id, rotulo in base.items():
        grupos[_chave_colisao(rotulo)].append(fila_id)

    rotulos = {}
    for ids in grupos.values():
        for fila_id in ids:
            rotulos[fila_id] = base[fila_id] if len(ids) == 1 else f"{base[fila_id]} ({fila_id})"
    return rotulos


def rotulo_fila(fila):
    """Rótulo de UMA fila - atalho sobre rotulos_filas() para templates (global
    Jinja, registrado em create_app) e serializadores, sem cada rota precisar
    repassar o mapa. Memoizado por request em `g` (uma consulta por empresa por
    request, não uma por fila); uma fila criada depois do cache montado no
    mesmo request força o recálculo, em vez de um KeyError.

    Fora de request (flask shell, scripts, seeds) NÃO há cache: `g` pertence
    ao app context, que num shell/script pode durar a sessão inteira - um
    cache ali devolveria rótulo velho depois de renomear uma fila ou cadastrar
    uma sigla. Handlers de Socket.IO rodam com request context próprio por
    evento, então caem no caminho com cache, também por evento."""
    if not has_request_context():
        return rotulos_filas(fila.empresa_id)[fila.id]
    cache = g.setdefault("_rotulos_filas_por_empresa", {})
    rotulos = cache.get(fila.empresa_id)
    if rotulos is None or fila.id not in rotulos:
        rotulos = cache[fila.empresa_id] = rotulos_filas(fila.empresa_id)
    return rotulos[fila.id]
