"""Consultas dos relatórios (seção 7 do BLUEPRINT), baseadas em logs_atendimento.

Estilo deliberado: busca as linhas relevantes já filtradas por tenant/unidade/serviço/
data via SQLAlchemy, e agrega em Python (dicts/listas) em vez de SQL com funções de
data específicas de banco - mantém o código idêntico em SQLite (testes) e MySQL
(produção) e é simples de testar, num volume de dados que o próprio BLUEPRINT
classifica como não precisando de data warehouse.

IMPORTANTE: o evento 'inicio_atendimento' faz parte do enum de logs_atendimento mas
nunca é gravado - o "início" de `tempo_atendimento`/`ranking_operadores` é inferido a
partir de 'chamada_senha'/'chamada_nome' (ver EVENTOS_INICIO_ATENDIMENTO), sem
necessidade de um evento próprio. Já 'fim_atendimento' passou a ser gravado a partir
do botão Finalizar da tela unificada de Atendimento (adendo 10.2, ver
app/atendimento/helpers.py:registrar_fim_atendimento) - `tempo_atendimento` e o tempo
médio em `ranking_operadores` já retornam dados reais sempre que houver ao menos um
atendimento finalizado no período filtrado (voltam vazio/None só na ausência real de
dados, não por limitação estrutural). Os relatórios de tempo de espera (1) e volume
(3) nunca dependeram disso e sempre trouxeram dados reais.
"""

from collections import defaultdict

from sqlalchemy import func

from app.extensions import db
from app.models import Fila, LogAtendimento, Senha, Totem, Tv

EVENTOS_INICIO_ATENDIMENTO = ("chamada_senha", "chamada_nome")


def _senhas_filtradas(filtros):
    query = Senha.query.filter(
        Senha.empresa_id == filtros.empresa_id,
        Senha.criada_em >= filtros.data_inicio,
        Senha.criada_em < filtros.data_fim_exclusivo,
    )
    if filtros.unidade_id:
        query = query.filter(Senha.unidade_id == filtros.unidade_id)
    elif filtros.unidades_permitidas is not None:
        query = query.filter(Senha.unidade_id.in_(filtros.unidades_permitidas))
    if filtros.fila_id:
        query = query.filter(Senha.fila_id == filtros.fila_id)
    return query


def tempo_medio_espera(filtros):
    """Relatório 1: tempo médio entre 'gerada' e 'chamada_senha', por fila/unidade/dia."""
    mapa_senha = {
        row.id: (row.fila_id, row.unidade_id)
        for row in _senhas_filtradas(filtros).with_entities(Senha.id, Senha.fila_id, Senha.unidade_id)
    }
    if not mapa_senha:
        return []

    logs = LogAtendimento.query.filter(
        LogAtendimento.senha_id.in_(mapa_senha.keys()),
        LogAtendimento.evento.in_(("gerada", "chamada_senha")),
    ).all()

    eventos_por_senha = defaultdict(dict)
    for log in logs:
        eventos_por_senha[log.senha_id][log.evento] = log.timestamp

    grupos = defaultdict(list)
    for senha_id, eventos in eventos_por_senha.items():
        gerada_em = eventos.get("gerada")
        chamada_em = eventos.get("chamada_senha")
        if gerada_em is None or chamada_em is None:
            continue
        fila_id, unidade_id = mapa_senha[senha_id]
        grupos[(fila_id, unidade_id, gerada_em.date())].append((chamada_em - gerada_em).total_seconds())

    resultado = []
    for (fila_id, unidade_id, dia), tempos in grupos.items():
        resultado.append(
            {
                "fila_id": fila_id,
                "unidade_id": unidade_id,
                "data": dia,
                "tempo_medio_segundos": round(sum(tempos) / len(tempos), 1),
                "quantidade": len(tempos),
            }
        )
    resultado.sort(key=lambda r: (r["data"], r["fila_id"], r["unidade_id"]))
    return resultado


def volume_senhas(filtros):
    """Relatório 3: volume de senhas por fila/unidade/dia, prioridade vs. normal."""
    linhas = _senhas_filtradas(filtros).with_entities(
        Senha.fila_id, Senha.unidade_id, Senha.criada_em, Senha.prioridade
    )

    grupos = defaultdict(lambda: {"prioridade": 0, "normal": 0})
    for fila_id, unidade_id, criada_em, prioridade in linhas:
        grupos[(fila_id, unidade_id, criada_em.date())]["prioridade" if prioridade else "normal"] += 1

    resultado = []
    for (fila_id, unidade_id, dia), contagem in grupos.items():
        resultado.append(
            {
                "fila_id": fila_id,
                "unidade_id": unidade_id,
                "data": dia,
                "prioridade": contagem["prioridade"],
                "normal": contagem["normal"],
                "total": contagem["prioridade"] + contagem["normal"],
            }
        )
    resultado.sort(key=lambda r: (r["data"], r["fila_id"], r["unidade_id"]))
    return resultado


def _pares_inicio_fim(filtros):
    """Para cada senha com atendimento completo, retorna (log_inicio, log_fim, fila_id,
    local_id).

    'Início' é o evento mais recente entre chamada_senha/chamada_nome (cobre tanto o
    atendimento direto da recepção quanto o fluxo de 2 etapas da coleta - seção 5.3).
    Fica vazio enquanto não existir nenhum log 'fim_atendimento' (ver nota no topo do
    arquivo) - isso é esperado, não é um bug desta consulta.

    `local_id` usa o valor ATUAL de `Senha.local_id` (seção 10.3), não o local em que a
    chamada de início efetivamente ocorreu - mesma aproximação que `fila_id` já fazia
    aqui antes da seção 10.3 (uma senha realocada/re-chamada altera esses campos depois
    do log de início, sem um snapshot histórico por evento; tabela separada para isso
    não foi pedida e seria um exagero de schema para o relatório).
    """
    mapa_senha = {
        row.id: (row.fila_id, row.local_id)
        for row in _senhas_filtradas(filtros).with_entities(Senha.id, Senha.fila_id, Senha.local_id)
    }
    if not mapa_senha:
        return []

    logs = LogAtendimento.query.filter(
        LogAtendimento.senha_id.in_(mapa_senha.keys()),
        LogAtendimento.evento.in_((*EVENTOS_INICIO_ATENDIMENTO, "fim_atendimento")),
    ).all()

    eventos_por_senha = defaultdict(list)
    for log in logs:
        eventos_por_senha[log.senha_id].append(log)

    pares = []
    for senha_id, eventos in eventos_por_senha.items():
        inicio = max(
            (e for e in eventos if e.evento in EVENTOS_INICIO_ATENDIMENTO),
            key=lambda e: e.timestamp,
            default=None,
        )
        fim = next((e for e in eventos if e.evento == "fim_atendimento"), None)
        if inicio is not None and fim is not None:
            fila_id, local_id = mapa_senha[senha_id]
            pares.append((inicio, fim, fila_id, local_id))
    return pares


def _pares_inicio_realocado(filtros):
    """Para cada senha com ao menos um hand-off (evento 'realocado'), retorna
    (log_inicio, log_realocado, unidade_id) - mede o PRIMEIRO segmento de
    atendimento (ex.: Recepção, seção 5.3), da chamada até a primeira realocação.

    Só o primeiro 'realocado' de cada senha é considerado: se ela for realocada
    mais de uma vez (ex.: Recepção -> Fila A -> Fila B -> Coleta), os segmentos
    entre hand-offs intermediários não são medidos aqui - escopo assumido desta
    consulta (ver seção 7 do BLUEPRINT), não uma lacuna a corrigir depois. O último
    segmento (da última chamada até 'fim_atendimento') já é coberto por
    `_pares_inicio_fim`/`tempo_atendimento`, sem nenhuma mudança nesta função.

    'Início' é o evento chamada_senha/chamada_nome mais recente ANTES desse
    primeiro 'realocado' - mesmo princípio de `_pares_inicio_fim` (pega a última
    chamada antes do evento que fecha o segmento), só que aqui quem fecha o
    segmento é 'realocado' em vez de 'fim_atendimento'.

    Sem fila_id/local_id (decisão registrada ao aprovar esta métrica): diferente
    de `_pares_inicio_fim`, que aproxima fila/local pelo estado ATUAL da Senha (só
    correto porque nada os altera entre a última chamada e o fim), aqui o
    Senha.fila_id/local_id atuais já refletem o DESTINO da realocação, não a fila
    de origem onde essa chamada realmente ocorreu - LogAtendimento não guarda um
    snapshot de fila/local por evento. Usar o valor atual mostraria a fila errada
    (a de destino, rotulada como segmento de origem), então as linhas resultantes
    não têm fila/local - só `unidade_id` (que não muda com a realocação).
    """
    mapa_unidade = {
        row.id: row.unidade_id
        for row in _senhas_filtradas(filtros).with_entities(Senha.id, Senha.unidade_id)
    }
    if not mapa_unidade:
        return []

    logs = LogAtendimento.query.filter(
        LogAtendimento.senha_id.in_(mapa_unidade.keys()),
        LogAtendimento.evento.in_((*EVENTOS_INICIO_ATENDIMENTO, "realocado")),
    ).all()

    eventos_por_senha = defaultdict(list)
    for log in logs:
        eventos_por_senha[log.senha_id].append(log)

    pares = []
    for senha_id, eventos in eventos_por_senha.items():
        primeiro_realocado = min(
            (e for e in eventos if e.evento == "realocado"),
            key=lambda e: e.timestamp,
            default=None,
        )
        if primeiro_realocado is None:
            continue

        inicio = max(
            (
                e for e in eventos
                if e.evento in EVENTOS_INICIO_ATENDIMENTO and e.timestamp <= primeiro_realocado.timestamp
            ),
            key=lambda e: e.timestamp,
            default=None,
        )
        if inicio is not None:
            pares.append((inicio, primeiro_realocado, mapa_unidade[senha_id]))
    return pares


def tempo_atendimento(filtros):
    """Relatório 2: tempo de atendimento por operador, setor, fila e local (seção 10.3 -
    permite ver o tempo médio de um guichê específico, não só do setor genérico).

    Inclui também o tempo do PRIMEIRO segmento de atendimento (ex.: Recepção antes
    de realocar para a Coleta, seção 5.3) via `_pares_inicio_realocado` - essas
    linhas aparecem com `fila_id`/`local_id` = None (exibidos como "—" no template/
    export, mesmo tratamento já usado para IDs ausentes; ver docstring de
    `_pares_inicio_realocado` para o motivo). Sem overlap com as linhas de
    `_pares_inicio_fim`: são pares de eventos disjuntos - uma senha só-recepção
    (nunca realocada) não gera nenhuma linha nova aqui; uma senha Recepção->Coleta
    gera exatamente 2 linhas (uma por segmento), nunca a mesma medida duas vezes.

    Agrupamento por (operador, setor, unidade) para as linhas sem fila - `unidade_id`
    entra na chave interna só para não misturar, num cenário raro de operador
    atuando em mais de uma unidade, segmentos de unidades diferentes numa única
    linha (já que não há `fila_id` para servir de desambiguador, como acontece nas
    linhas de `_pares_inicio_fim`, onde uma fila já pertence a uma única unidade).
    `unidade_id` não vira uma coluna própria no relatório - só evita esse
    cruzamento indevido internamente.
    """
    grupos = defaultdict(list)
    for inicio, fim, fila_id, local_id in _pares_inicio_fim(filtros):
        if filtros.operador_id and inicio.usuario_id != filtros.operador_id:
            continue
        chave = (inicio.usuario_id, inicio.setor, fila_id, local_id, None)
        grupos[chave].append((fim.timestamp - inicio.timestamp).total_seconds())

    for inicio, realocado, unidade_id in _pares_inicio_realocado(filtros):
        if filtros.operador_id and inicio.usuario_id != filtros.operador_id:
            continue
        chave = (inicio.usuario_id, inicio.setor, None, None, unidade_id)
        grupos[chave].append((realocado.timestamp - inicio.timestamp).total_seconds())

    resultado = []
    for (operador_id, setor, fila_id, local_id, _unidade_id), tempos in grupos.items():
        resultado.append(
            {
                "operador_id": operador_id,
                "setor": setor,
                "fila_id": fila_id,
                "local_id": local_id,
                "tempo_medio_segundos": round(sum(tempos) / len(tempos), 1),
                "quantidade": len(tempos),
            }
        )
    resultado.sort(key=lambda r: (-r["quantidade"], r["operador_id"] or 0))
    return resultado


def ranking_operadores(filtros):
    """Relatório 4: ranking de operadores por volume (chamada_senha + chamada_nome) e
    tempo médio de atendimento (None enquanto não houver fim_atendimento gravado)."""
    mapa_senha_ids = [row.id for row in _senhas_filtradas(filtros).with_entities(Senha.id)]
    volumes = defaultdict(int)
    if mapa_senha_ids:
        contagens = (
            db.session.query(LogAtendimento.usuario_id, LogAtendimento.id)
            .filter(
                LogAtendimento.senha_id.in_(mapa_senha_ids),
                LogAtendimento.evento.in_(EVENTOS_INICIO_ATENDIMENTO),
                LogAtendimento.usuario_id.isnot(None),
            )
            .all()
        )
        for usuario_id, _ in contagens:
            volumes[usuario_id] += 1

    tempos_por_operador = defaultdict(list)
    for inicio, fim, _fila_id, _local_id in _pares_inicio_fim(filtros):
        if inicio.usuario_id is not None:
            tempos_por_operador[inicio.usuario_id].append((fim.timestamp - inicio.timestamp).total_seconds())

    operadores = set(volumes) | set(tempos_por_operador)
    resultado = []
    for operador_id in operadores:
        if filtros.operador_id and operador_id != filtros.operador_id:
            continue
        tempos = tempos_por_operador.get(operador_id, [])
        resultado.append(
            {
                "operador_id": operador_id,
                "volume": volumes.get(operador_id, 0),
                "tempo_medio_segundos": round(sum(tempos) / len(tempos), 1) if tempos else None,
            }
        )
    resultado.sort(key=lambda r: r["volume"], reverse=True)
    return resultado


def resumo_recursos(empresa, unidades_visiveis):
    """Dashboard (fora do BLUEPRINT original): contagens de filas/totens/TVs, já
    escopadas por unidade (unidades_visiveis vem de _unidades_ids_visiveis, que já
    resolve g.unidades_permitidas). Retorna também as listas cruas (filas/totens/tvs)
    para reaproveitar em coletar_alertas sem repetir as mesmas 3 queries."""
    filas = (
        Fila.query.filter(Fila.empresa_id == empresa.id, Fila.unidade_id.in_(unidades_visiveis))
        .order_by(Fila.nome)
        .all()
    )
    totens = (
        Totem.query.filter(Totem.empresa_id == empresa.id, Totem.unidade_id.in_(unidades_visiveis))
        .order_by(Totem.nome)
        .all()
    )
    tvs = (
        Tv.query.filter(Tv.empresa_id == empresa.id, Tv.unidade_id.in_(unidades_visiveis))
        .order_by(Tv.nome)
        .all()
    )

    return {
        "filas": filas,
        "totens": totens,
        "tvs": tvs,
        "filas_ativas": sum(1 for f in filas if f.ativo),
        "totens_ativos": sum(1 for t in totens if t.ativo),
        "totens_inativos": sum(1 for t in totens if not t.ativo),
        "tvs_ativas": sum(1 for t in tvs if t.ativo),
    }


def senhas_aguardando_por_fila(empresa, unidades_visiveis):
    """Dashboard: quantidade de senhas com status 'aguardando' agora, agrupada por
    fila_id - mesmo padrão de GROUP BY em Python de volume_senhas/tempo_medio_espera."""
    linhas = (
        db.session.query(Senha.fila_id, func.count(Senha.id))
        .filter(
            Senha.empresa_id == empresa.id,
            Senha.unidade_id.in_(unidades_visiveis),
            Senha.status == "aguardando",
        )
        .group_by(Senha.fila_id)
        .all()
    )
    return {fila_id: quantidade for fila_id, quantidade in linhas}


def ultimas_chamadas(empresa, unidades_visiveis, limite=15):
    """Dashboard: feed das últimas chamadas (evento chamada_senha/chamada_nome),
    mais recente primeiro. logs_atendimento não tem empresa_id próprio, mas
    unidades_visiveis já é escopado à empresa resolvida (via _unidades_ids_visiveis),
    então filtrar só por unidade_id já basta - mesma premissa de tempo_medio_espera."""
    logs = (
        LogAtendimento.query.filter(
            LogAtendimento.unidade_id.in_(unidades_visiveis),
            LogAtendimento.evento.in_(EVENTOS_INICIO_ATENDIMENTO),
        )
        .order_by(LogAtendimento.timestamp.desc())
        .limit(limite)
        .all()
    )
    if not logs:
        return []

    senha_ids = {log.senha_id for log in logs}
    senhas_por_id = {
        row.id: row
        for row in Senha.query.filter(Senha.id.in_(senha_ids)).with_entities(
            Senha.id, Senha.codigo, Senha.nome_paciente, Senha.fila_id, Senha.local_id
        )
    }

    resultado = []
    for log in logs:
        senha = senhas_por_id.get(log.senha_id)
        resultado.append(
            {
                "timestamp": log.timestamp,
                "evento": log.evento,
                "fila_id": senha.fila_id if senha else None,
                "local_id": senha.local_id if senha else None,
                "codigo": senha.codigo if senha else None,
                "nome_paciente": senha.nome_paciente if senha else None,
            }
        )
    return resultado


def coletar_alertas(filas, totens, tvs):
    """Dashboard: alertas de configuração incompleta, calculados em Python sobre as
    MESMAS listas já buscadas por resumo_recursos (evita 4 queries extras). Cada
    alerta é {'tipo', 'mensagem'} - lista plana, sem uma tabela/query por tipo."""
    alertas = []
    for fila in filas:
        if not fila.ativo:
            continue
        if not fila.tipos_senha:
            alertas.append(
                {"tipo": "fila_sem_tipo_senha", "mensagem": f"Fila '{fila.nome}' sem tipo de senha vinculado."}
            )
        if not fila.aberta_em():
            alertas.append(
                {
                    "tipo": "fila_fora_horario",
                    "mensagem": f"Fila '{fila.nome}' fora do horário de funcionamento agora.",
                }
            )
    # Alerta "totem sem impressora configurada" removido (adendo que substitui a
    # 10.6): a impressão passou a ser via window.print() do navegador, usando a
    # impressora padrão já configurada no Windows de cada totem - nome_impressora_
    # windows não é mais o que decide se a impressão funciona, então o alerta
    # baseado só nesse campo ficaria incorreto (um totem pode imprimir bem sem
    # nunca ter preenchido esse campo, que continua no banco só como legado).
    for tv in tvs:
        if tv.ativo and not tv.filas:
            alertas.append({"tipo": "tv_sem_filas", "mensagem": f"TV '{tv.nome}' sem filas vinculadas."})
    return alertas
