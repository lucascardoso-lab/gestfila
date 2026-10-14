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
from app.models.senha import criada_hoje_na_clinica, senhas_abertas_de_dias_anteriores
from app.utils.tempo import para_horario_clinica

EVENTOS_INICIO_ATENDIMENTO = ("chamada_senha", "chamada_nome")

# Eventos que ABREM/FECHAM um intervalo de ESPERA (tempo_medio_espera, adendo
# 10.66 - ver docstring de _segmentos_espera_por_senha): uma senha começa a
# esperar quando é gerada, quando é realocada pra outra fila, ou quando volta
# a aguardar (operador usa "Aguardar") - e para de esperar na próxima chamada
# (chamada_senha/chamada_nome, = EVENTOS_INICIO_ATENDIMENTO acima).
EVENTOS_ABERTURA_ESPERA = ("gerada", "realocado", "voltou_aguardar")

# Rótulo do que FECHA um segmento em tempo_atendimento() (adendo 10.63 - achado
# em revisão do adendo 10.62, não decisão original): sem isso, um segmento que
# termina em 'fim_atendimento' (operador concluiu o atendimento ali mesmo) e um
# que termina em 'realocado' (operador só repassou adiante) cairiam na MESMA
# chave de agrupamento sempre que operador/setor/fila/local coincidissem -
# misturando "tempo até concluir" com "tempo até repassar" numa única média,
# sem forma de diferenciar no relatório. Constantes centralizadas aqui (não
# strings soltas em queries.py/routes.py/templates) - mesmo padrão de
# enum/constante já usado em EVENTO_LOG/SETOR_LOG (app/models/log_atendimento.py).
TIPO_FECHAMENTO_CONCLUSAO = "conclusao"
TIPO_FECHAMENTO_REPASSE = "repasse"
TIPO_FECHAMENTO_ROTULOS = {
    TIPO_FECHAMENTO_CONCLUSAO: "Conclusão",
    TIPO_FECHAMENTO_REPASSE: "Repasse",
}


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
    if filtros.area_fila_id:
        # Área (adendo 10.76): senhas cuja fila ATUAL está na área - mesma
        # semântica do filtro de Fila (a senha entra inteira; no agrupamento,
        # cada segmento vai para a área da SUA fila).
        query = query.filter(Senha.fila_id.in_(db.session.query(Fila.id).filter(Fila.area_fila_id == filtros.area_fila_id)))
    return query


def _area_por_fila(empresa_id):
    """{fila_id: área ATUAL da fila} (None = "Sem área") - adendo 10.76. Sem
    snapshot por evento: mudar a fila de área muda os relatórios do passado
    (limitação documentada, igual à da "fila atual")."""
    return {fila_id: area_id for fila_id, area_id in
            Fila.query.filter_by(empresa_id=empresa_id).with_entities(Fila.id, Fila.area_fila_id)}


def _grupo(fila_id, area_de, por_area):
    """Chave de agrupamento: a fila, ou ("area", área da fila) com "Agrupar por:
    Área" - MESMOS segmentos e MESMO cálculo, só a chave muda (a soma das áreas,
    incluindo "Sem área", é igual ao total)."""
    return ("area", area_de.get(fila_id)) if por_area else fila_id


def _separar_grupo(grupo):
    """Chave -> (fila_id, area_id) para a linha de saída."""
    return (None, grupo[1]) if isinstance(grupo, tuple) else (grupo, None)


def _segmentos_espera_por_senha(filtros):
    """Pra cada senha, retorna UM SEGMENTO DE ESPERA POR ESTÁGIO da jornada -
    (log_abertura, log_fechamento, fila_id, unidade_id) - não só 'gerada' até a
    1ª chamada (achado/corrigido: mesma classe de bug já corrigida em
    tempo_atendimento pelo 10.62/10.63, agora neste relatório irmão - nunca
    tinha sido tocada porque não era o escopo daquela correção).

    Bugs confirmados na versão anterior desta consulta (código removido):
    1. `eventos_por_senha[senha_id][evento] = timestamp` era um dict simples -
       múltiplos 'chamada_senha' pra mesma senha (ex.: 'Tocar' não gera log,
       mas uma RE-chamada depois de 'voltou_aguardar' gera) se SOBRESCREVIAM
       na iteração, sem `.order_by()` garantindo qual sobrevivia - resultado
       não-determinístico, dependente da ordem de retorno do banco.
    2. Buscava só os eventos `("gerada", "chamada_senha")` - nunca
       'chamada_nome' (2º estágio, ex. Coleta - `chamada_nome` é o evento de
       chamada REAL lá, `chamada_senha` só existe no 1º estágio/Recepção),
       nunca 'realocado', nunca 'voltou_aguardar'. Uma senha Recepção->Coleta
       nunca tinha o segundo intervalo de espera capturado.
    3. Consequência de 1+2: só existia 1 intervalo por senha, ignorando todo o
       resto da jornada depois de qualquer realocação/re-chamada.

    Pareamento (MESMA técnica de `_segmentos_intermediarios_realocacao`,
    ponteiro sequencial sobre eventos ordenados por timestamp - eventos.sort(),
    não `.order_by()` no SQL, mesma convenção já usada nesta função irmã):
    eventos que ABREM um intervalo de espera são `EVENTOS_ABERTURA_ESPERA`
    ('gerada'/'realocado'/'voltou_aguardar'); eventos que FECHAM são
    `EVENTOS_INICIO_ATENDIMENTO` ('chamada_senha'/'chamada_nome') - nomeado
    assim porque são os MESMOS eventos que abrem um segmento de ATENDIMENTO em
    `_pares_inicio_fim`/`_segmentos_intermediarios_realocacao` (fechar espera =
    abrir atendimento, é o mesmo instante).

    **fila_id vem do snapshot do evento de FECHAMENTO (chamada_senha/
    chamada_nome), e isso dá DESTINO, não origem - INVERSO da convenção de
    `_segmentos_intermediarios_realocacao` (que usa o snapshot de 'realocado',
    ORIGEM, capturado ANTES da mutação em `registrar_realocacao`). Não é uma
    inconsistência: as duas funções seguem a MESMA regra ("usa o snapshot do
    evento que fecha o segmento") - a diferença de origem/destino é
    consequência de QUAL evento fecha cada tipo de segmento. O snapshot de
    'realocado' é capturado ANTES da senha mudar de fila (por isso é origem);
    o snapshot de 'chamada_senha'/'chamada_nome' é gravado DEPOIS de qualquer
    realocação anterior já ter movido a senha (por isso já reflete a fila onde
    o paciente estava esperando quando foi chamado = destino). Decisão de
    produto confirmada com o cliente: cada intervalo de espera pertence à fila
    onde o paciente estava esperando, não à fila de onde ele veio.

    Sem `tipo_fechamento` (diferente do 10.63): aqui não há risco de colisão -
    a fila de DESTINO já separa os estágios naturalmente (Recepção/Triagem/
    Coleta têm fila_id diferentes, viram linhas diferentes por construção),
    não existe o cenário do 10.63 (mesmo operador/fila/local em dois tipos de
    fechamento).

    **Dado histórico cai no comportamento ANTIGO, por senha (mesma decisão do
    10.62):** se QUALQUER evento relevante desta senha não tiver o snapshot
    (`fila_id IS NULL` - log gravado antes do 10.62), a senha INTEIRA usa a
    regra antiga - só 'gerada' até a 1ª 'chamada_senha' (nunca 'chamada_nome'/
    'realocado'/'voltou_aguardar', igual ao escopo original desta consulta),
    com fila_id/unidade_id do estado ATUAL da `Senha` (mesma fonte que o
    código antigo já usava) - só a NÃO-determinismo do bug 1 é corrigida
    (agora sempre pega a 'chamada_senha' cronologicamente mais cedo, já que
    `eventos` vem ordenado).
    """
    mapa_senha = {
        row.id: (row.fila_id, row.unidade_id)
        for row in _senhas_filtradas(filtros).with_entities(Senha.id, Senha.fila_id, Senha.unidade_id)
    }
    if not mapa_senha:
        return []

    eventos_relevantes = EVENTOS_ABERTURA_ESPERA + EVENTOS_INICIO_ATENDIMENTO
    logs = LogAtendimento.query.filter(
        LogAtendimento.senha_id.in_(mapa_senha.keys()),
        LogAtendimento.evento.in_(eventos_relevantes),
    ).all()

    eventos_por_senha = defaultdict(list)
    for log in logs:
        eventos_por_senha[log.senha_id].append(log)

    segmentos = []
    for senha_id, eventos in eventos_por_senha.items():
        eventos.sort(key=lambda e: e.timestamp)
        fila_atual, unidade_atual = mapa_senha[senha_id]
        dado_novo = all(e.fila_id is not None for e in eventos)

        if not dado_novo:
            gerada_evt = next((e for e in eventos if e.evento == "gerada"), None)
            # `eventos` já está ordenado por timestamp - o 1º "chamada_senha"
            # encontrado é sempre o cronologicamente mais cedo (determinístico).
            chamada_evt = next((e for e in eventos if e.evento == "chamada_senha"), None)
            if gerada_evt is not None and chamada_evt is not None:
                segmentos.append((gerada_evt, chamada_evt, fila_atual, unidade_atual))
            continue

        abertura_pendente = None
        for evento in eventos:
            if evento.evento in EVENTOS_ABERTURA_ESPERA:
                abertura_pendente = evento
            elif evento.evento in EVENTOS_INICIO_ATENDIMENTO:
                if abertura_pendente is not None:
                    segmentos.append((abertura_pendente, evento, evento.fila_id, evento.unidade_id))
                    abertura_pendente = None

    return segmentos


def tempo_medio_espera(filtros):
    """Relatório 1: tempo médio de espera por fila/unidade/dia - CADA intervalo
    de espera da jornada de uma senha (não só 'gerada' até a 1ª chamada), via
    `_segmentos_espera_por_senha` (ver docstring lá para o histórico do bug
    corrigido e a convenção de fila de DESTINO).

    `data` usa o dia do evento que ABRE cada segmento (quando aquela espera
    específica começou) - não mais sempre `gerada_em.date()`: um segmento
    aberto por 'realocado'/'voltou_aguardar' pode cair em outro dia civil da
    'gerada' original, e é o dia em que ELE começou que importa pro relatório
    "por dia".

    O dia é o dia CIVIL DA CLÍNICA (`para_horario_clinica`, adendo 10.74) - um
    `timestamp.date()` direto daria o dia em UTC, e uma espera aberta às 22h de
    Brasília cairia no dia seguinte.
    """
    por_area = filtros.agrupar_por_area
    area_de = _area_por_fila(filtros.empresa_id) if por_area else {}
    grupos = defaultdict(list)
    for abertura, fechamento, fila_id, unidade_id in _segmentos_espera_por_senha(filtros):
        chave = (_grupo(fila_id, area_de, por_area), unidade_id, para_horario_clinica(abertura.timestamp).date())
        grupos[chave].append((fechamento.timestamp - abertura.timestamp).total_seconds())

    resultado = []
    for (grupo, unidade_id, dia), tempos in grupos.items():
        fila_id, area_id = _separar_grupo(grupo)
        resultado.append(
            {
                "fila_id": fila_id,
                "area_id": area_id,
                "unidade_id": unidade_id,
                "data": dia,
                "tempo_medio_segundos": round(sum(tempos) / len(tempos), 1),
                "quantidade": len(tempos),
            }
        )
    resultado.sort(key=lambda r: (r["data"], r["fila_id"] or 0, r["area_id"] or 0, r["unidade_id"] or 0))
    return resultado


def volume_senhas(filtros):
    """Relatório 3: volume de senhas por fila/unidade/dia, prioridade vs. normal.
    Dia civil da clínica, não de UTC (adendo 10.74 - ver tempo_medio_espera)."""
    linhas = _senhas_filtradas(filtros).with_entities(
        Senha.fila_id, Senha.unidade_id, Senha.criada_em, Senha.prioridade
    )

    por_area = filtros.agrupar_por_area
    area_de = _area_por_fila(filtros.empresa_id) if por_area else {}
    grupos = defaultdict(lambda: {"prioridade": 0, "normal": 0})
    for fila_id, unidade_id, criada_em, prioridade in linhas:
        chave = (_grupo(fila_id, area_de, por_area), unidade_id, para_horario_clinica(criada_em).date())
        grupos[chave]["prioridade" if prioridade else "normal"] += 1

    resultado = []
    for (grupo, unidade_id, dia), contagem in grupos.items():
        fila_id, area_id = _separar_grupo(grupo)
        resultado.append(
            {
                "fila_id": fila_id,
                "area_id": area_id,
                "unidade_id": unidade_id,
                "data": dia,
                "prioridade": contagem["prioridade"],
                "normal": contagem["normal"],
                "total": contagem["prioridade"] + contagem["normal"],
            }
        )
    resultado.sort(key=lambda r: (r["data"], r["fila_id"] or 0, r["area_id"] or 0, r["unidade_id"] or 0))
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


def _segmentos_intermediarios_realocacao(filtros):
    """Para cada senha com ao menos um hand-off (evento 'realocado'), retorna UM
    SEGMENTO POR REALOCAÇÃO - (log_inicio, log_realocado, fila_id, local_id,
    unidade_id) - não só o primeiro (adendo 10.62, generaliza a versão anterior
    desta função, `_pares_inicio_realocado`, que media só o hand-off inicial).

    Uma senha realocada 3 vezes (ex.: Recepção -> Fila A -> Fila B -> Finalizado)
    gera 3 segmentos aqui - um por 'realocado' -, mais o último (última chamada
    -> 'fim_atendimento') já coberto por `_pares_inicio_fim`/`tempo_atendimento`,
    sem mudança nesta função.

    Pareamento (percorre os eventos de fronteira em ordem cronológica, um único
    ponteiro `inicio_pendente`): toda realocação exige a senha estar em status
    chamada/em_atendimento primeiro - ou seja, sempre existe uma chamada nova
    (chamada_senha/chamada_nome) na fila de destino antes da PRÓXIMA realocação -,
    então este pareamento sequencial nunca "pula" ou perde um segmento.

    fila_id/local_id vêm do SNAPSHOT gravado no próprio evento 'realocado'
    (`LogAtendimento.fila_id`/`local_id`, adendo 10.62 Parte 1 - já é a fila/local
    de ORIGEM, capturados antes da mutação em `registrar_realocacao`). Só existe
    pra logs gravados a partir desta mudança.

    **Dado histórico cai no comportamento ANTIGO, por senha (decisão de produto
    deliberada, não lacuna):** se QUALQUER 'realocado' dessa senha não tiver o
    snapshot (`fila_id IS NULL` - log gravado antes desta mudança), a senha
    INTEIRA usa a regra antiga - só o primeiro segmento, sem fila/local (só
    `unidade_id`), exatamente como `_pares_inicio_realocado` fazia. Decisão por
    senha (não por evento isolado): cobre o caso normal (senha 100% antes ou
    100% depois do deploy) com uma regra simples; o caso raro de uma senha em
    atendimento NO MOMENTO exato do deploy (alguns logs de cada lado) cai no
    fallback antigo também - mais seguro que misturar segmentos parciais.
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

    segmentos = []
    for senha_id, eventos in eventos_por_senha.items():
        eventos.sort(key=lambda e: e.timestamp)
        realocacoes = [e for e in eventos if e.evento == "realocado"]
        if not realocacoes:
            continue

        unidade_id = mapa_unidade[senha_id]
        dado_novo = all(r.fila_id is not None for r in realocacoes)

        if not dado_novo:
            primeiro_realocado = realocacoes[0]
            inicio = max(
                (
                    e for e in eventos
                    if e.evento in EVENTOS_INICIO_ATENDIMENTO and e.timestamp <= primeiro_realocado.timestamp
                ),
                key=lambda e: e.timestamp,
                default=None,
            )
            if inicio is not None:
                segmentos.append((inicio, primeiro_realocado, None, None, unidade_id))
            continue

        inicio_pendente = None
        for evento in eventos:
            if evento.evento in EVENTOS_INICIO_ATENDIMENTO:
                inicio_pendente = evento
            elif evento.evento == "realocado":
                if inicio_pendente is not None:
                    segmentos.append((inicio_pendente, evento, evento.fila_id, evento.local_id, unidade_id))
                    inicio_pendente = None

    return segmentos


def tempo_atendimento(filtros):
    """Relatório 2: tempo de atendimento por operador, setor, fila e local (seção 10.3 -
    permite ver o tempo médio de um guichê específico, não só do setor genérico).

    Inclui também os segmentos INTERMEDIÁRIOS de atendimento - uma senha realocada
    mais de uma vez (ex.: Recepção -> Fila A -> Fila B -> Finalizado) gera um
    segmento por hand-off, não só o primeiro (adendo 10.62; antes, adendo/seção 7
    documentava a limitação de só medir o primeiro e o último) - via
    `_segmentos_intermediarios_realocacao`. Essas linhas trazem `fila_id`/
    `local_id` reais quando o log tem o snapshot novo (ver docstring da função);
    dado histórico sem esse snapshot cai no comportamento antigo (`fila_id`/
    `local_id` = None, exibidos como "—" no template/export, mesmo tratamento já
    usado para IDs ausentes). Sem overlap com as linhas de `_pares_inicio_fim`:
    são pares de eventos disjuntos - uma senha só-recepção (nunca realocada) não
    gera nenhuma linha nova aqui; uma senha realocada N vezes gera exatamente
    N+1 linhas (N segmentos intermediários + 1 final), nunca a mesma medida
    duas vezes.

    Agrupamento por (operador, setor, unidade) só para as linhas SEM fila (dado
    histórico, sem snapshot) - `unidade_id` entra na chave interna só para não
    misturar, num cenário raro de operador atuando em mais de uma unidade,
    segmentos de unidades diferentes numa única linha (já que não há `fila_id`
    para servir de desambiguador nesse caso, diferente das linhas com fila real,
    onde uma fila já pertence a uma única unidade). `unidade_id` não vira uma
    coluna própria no relatório - só evita esse cruzamento indevido internamente.

    `tipo_fechamento` (`TIPO_FECHAMENTO_CONCLUSAO`/`TIPO_FECHAMENTO_REPASSE`,
    adendo 10.63 - achado em revisão do 10.62, não decisão original) SEMPRE
    entra na chave de agrupamento, mesmo quando operador/setor/fila/local
    coincidem: sem isso, um segmento que termina em `fim_atendimento`
    (operador concluiu ali) e um que termina em `realocado` (só repassou
    adiante) cairiam na mesma linha/média, sem forma de diferenciar "tempo até
    concluir" de "tempo até repassar" no relatório.
    """
    # "Agrupar por: Área" (adendo 10.76): fila/local viram a área da fila (o
    # local sai do agrupamento - um local atende várias filas); segmento
    # histórico sem fila (10.62) cai em "Sem área".
    por_area = filtros.agrupar_por_area
    area_de = _area_por_fila(filtros.empresa_id) if por_area else {}

    def fila_e_local(fila_id, local_id):
        return (_grupo(fila_id, area_de, True), None) if por_area else (fila_id, local_id)

    grupos = defaultdict(list)
    for inicio, fim, fila_id, local_id in _pares_inicio_fim(filtros):
        if filtros.operador_id and inicio.usuario_id != filtros.operador_id:
            continue
        chave = (inicio.usuario_id, inicio.setor, *fila_e_local(fila_id, local_id), None, TIPO_FECHAMENTO_CONCLUSAO)
        grupos[chave].append((fim.timestamp - inicio.timestamp).total_seconds())

    for inicio, realocado, fila_id, local_id, unidade_id in _segmentos_intermediarios_realocacao(filtros):
        if filtros.operador_id and inicio.usuario_id != filtros.operador_id:
            continue
        if fila_id is not None:
            chave = (inicio.usuario_id, inicio.setor, *fila_e_local(fila_id, local_id), None, TIPO_FECHAMENTO_REPASSE)
        elif por_area:
            chave = (inicio.usuario_id, inicio.setor, ("area", None), None, unidade_id, TIPO_FECHAMENTO_REPASSE)
        else:
            chave = (inicio.usuario_id, inicio.setor, None, None, unidade_id, TIPO_FECHAMENTO_REPASSE)
        grupos[chave].append((realocado.timestamp - inicio.timestamp).total_seconds())

    resultado = []
    for (operador_id, setor, grupo, local_id, _unidade_id, tipo_fechamento), tempos in grupos.items():
        fila_id, area_id = _separar_grupo(grupo) if grupo is not None else (None, None)
        resultado.append(
            {
                "operador_id": operador_id,
                "setor": setor,
                "fila_id": fila_id,
                "area_id": area_id,
                "local_id": local_id,
                # Só preenchido no Repasse histórico sem snapshot de fila (chave
                # acima) - exposto pra coluna "unidade" do CSV/Excel (adendo
                # 10.69), não entra em nenhum cálculo.
                "unidade_id": _unidade_id,
                "tipo_fechamento": tipo_fechamento,
                "tempo_medio_segundos": round(sum(tempos) / len(tempos), 1),
                "quantidade": len(tempos),
            }
        )
    resultado.sort(key=lambda r: (-r["quantidade"], r["operador_id"] or 0))
    return resultado


# Relatório "Tempos por senha" (adendo 10.73) - situação de cada senha na
# visão por senha. Constantes centralizadas (mesmo padrão de
# TIPO_FECHAMENTO_*), usadas pela consulta, pela rota/exportação e pelos testes.
SITUACAO_CONCLUIDA = "Concluída"
SITUACAO_HISTORICO_PARCIAL = "Histórico parcial"
SITUACAO_NUNCA_CHAMADA = "Aguardando (nunca chamada)"
SITUACAO_CANCELADA = "Cancelada (encerramento automático)"
SITUACAO_NAO_FINALIZADA = "Chamada, não finalizada"
# adendo 10.77 - 'aguardando' de dia anterior cancelada pelo sistema (evento
# 'expirada_aguardando'), separada pelo histórico: nunca chamada, ou chamada
# antes e devolvida à espera (realocação / "voltar a aguardar").
SITUACAO_EXPIRADA_NUNCA_CHAMADA = "Expirada (nunca chamada)"
SITUACAO_EXPIRADA_APOS_ESPERA = "Expirada (após realocação/volta à espera)"
DICAS_SITUACAO = {
    SITUACAO_CONCLUIDA: "Atendimento finalizado, com todas as etapas registradas.",
    SITUACAO_HISTORICO_PARCIAL: (
        "Senha registrada antes do adendo 10.62: as etapas depois da realocação não foram "
        "gravadas com detalhe. Só a espera inicial é exata; os totais ficam em branco."
    ),
    SITUACAO_NUNCA_CHAMADA: "A senha nunca foi chamada - não há tempo de espera nem de atendimento.",
    SITUACAO_CANCELADA: (
        "Cancelada pelo encerramento automático do fim do dia (adendo 10.18) sem ter sido "
        "finalizada - não há tempo de atendimento."
    ),
    SITUACAO_NAO_FINALIZADA: "Foi chamada, mas o atendimento ainda não foi finalizado.",
    SITUACAO_EXPIRADA_NUNCA_CHAMADA: (
        "Ficou aguardando até o fim do dia sem ser chamada e foi encerrada automaticamente "
        "(adendo 10.77) - não há tempo de espera nem de atendimento."
    ),
    SITUACAO_EXPIRADA_APOS_ESPERA: (
        "Foi chamada, voltou à espera (realocação ou \"voltar a aguardar\") e ficou aguardando "
        "até o fim do dia - encerrada automaticamente (adendo 10.77). A espera final, que não "
        "terminou em chamada, não entra nos tempos."
    ),
}
TIPO_ETAPA_ESPERA = "espera"
ROTULOS_TIPO_ETAPA = {
    TIPO_ETAPA_ESPERA: "Espera",
    TIPO_FECHAMENTO_CONCLUSAO: "Atendimento - Conclusão",
    TIPO_FECHAMENTO_REPASSE: "Atendimento - Repasse",
}
EVENTO_EXPIRADA = "expirada_aguardando"  # adendo 10.77
EVENTOS_JORNADA = (
    "gerada", "realocado", "voltou_aguardar", *EVENTOS_INICIO_ATENDIMENTO, "fim_atendimento", EVENTO_EXPIRADA,
)


def tempos_por_senha(filtros):
    """Relatório 5 (adendo 10.73): tempos POR SENHA - retorna
    `(linhas_por_senha, linhas_por_etapa)`, com segundos crus (a rota converte
    para minutos e nomes).

    **Nenhum pareamento novo:** os segmentos vêm exatamente das mesmas três
    funções dos agregados - `_segmentos_espera_por_senha` (esperas, 10.66),
    `_pares_inicio_fim` (atendimento final) e
    `_segmentos_intermediarios_realocacao` (repasses, 10.62) -, com o mesmo
    fallback de dado histórico; aqui só são AGRUPADOS por `senha_id` (cada
    item já traz o log, e o log traz a senha). Por isso a visão por etapa fecha
    com os agregados: mesmos segmentos, mesma contagem, mesma média.

    Por senha:
    - `espera_inicial`: o 1º segmento de espera quando ele abre na emissão
      ('gerada' -> 1ª chamada) - literalmente o tempo pedido.
    - `espera_total`/`atendimento_total`: soma dos segmentos da senha.
      `atendimento_total` só com o atendimento FINALIZADO (sem
      'fim_atendimento' fica vazio, mesmo que haja repasses).
    - Vazio (None), nunca 0, quando não há o dado (`situacao` explica).
    - **Histórico parcial:** senha com log sem snapshot de fila (antes do
      10.62) E com realocação/volta a aguardar - o fallback dos agregados só
      enxerga parte da jornada, então os totais ficam vazios e só a espera
      inicial (exata também no fallback) é mostrada. Histórico SEM realocação
      é exato e fica "Concluída".

    Operador (decisão do cliente): na visão por senha, o filtro mantém as
    senhas em que o operador atendeu ALGUMA etapa (totais da senha inteira);
    na visão por etapa, só as etapas dele - atendimento pelo operador que
    chamou (`inicio.usuario_id`, igual a `tempo_atendimento`), espera pelo
    operador que a encerrou chamando a senha.

    Filtro de Fila: usa a fila ATUAL da senha (`_senhas_filtradas`), igual aos
    agregados - limitação registrada no 10.73/seção 11.
    """
    senhas = _senhas_filtradas(filtros).order_by(Senha.criada_em, Senha.id).all()
    if not senhas:
        return [], []

    esperas = defaultdict(list)
    for abertura, fechamento, fila_id, _unidade_id in _segmentos_espera_por_senha(filtros):
        esperas[abertura.senha_id].append((TIPO_ETAPA_ESPERA, abertura, fechamento, fila_id, None))
    atendimentos = defaultdict(list)
    for inicio, fim, fila_id, local_id in _pares_inicio_fim(filtros):
        atendimentos[inicio.senha_id].append((TIPO_FECHAMENTO_CONCLUSAO, inicio, fim, fila_id, local_id))
    for inicio, realocado, fila_id, local_id, _unidade_id in _segmentos_intermediarios_realocacao(filtros):
        atendimentos[inicio.senha_id].append((TIPO_FECHAMENTO_REPASSE, inicio, realocado, fila_id, local_id))

    area_de = _area_por_fila(filtros.empresa_id)  # adendo 10.76
    eventos = defaultdict(list)
    for log in LogAtendimento.query.filter(
        LogAtendimento.senha_id.in_([s.id for s in senhas]),
        LogAtendimento.evento.in_(EVENTOS_JORNADA),
    ).all():
        eventos[log.senha_id].append(log)

    def segundos(segmento):
        return (segmento[2].timestamp - segmento[1].timestamp).total_seconds()

    operador = filtros.operador_id
    linhas, etapas = [], []
    for senha in senhas:
        evs = eventos[senha.id]
        esp = sorted(esperas[senha.id], key=lambda seg: seg[1].timestamp)
        atd = sorted(atendimentos[senha.id], key=lambda seg: seg[1].timestamp)
        historico = any(e.fila_id is None for e in evs)
        parcial = historico and any(e.evento in ("realocado", "voltou_aguardar") for e in evs)
        foi_chamada = any(e.evento in EVENTOS_INICIO_ATENDIMENTO for e in evs)
        fim = next((e for e in evs if e.evento == "fim_atendimento"), None)
        gerada = next((e for e in evs if e.evento == "gerada"), None)
        expirada = any(e.evento == EVENTO_EXPIRADA for e in evs)

        if expirada:
            situacao = SITUACAO_EXPIRADA_APOS_ESPERA if foi_chamada else SITUACAO_EXPIRADA_NUNCA_CHAMADA
        elif not foi_chamada:
            situacao = SITUACAO_NUNCA_CHAMADA
        elif senha.status == "cancelada":
            situacao = SITUACAO_CANCELADA
        elif fim is None:
            situacao = SITUACAO_NAO_FINALIZADA
        elif parcial:
            situacao = SITUACAO_HISTORICO_PARCIAL
        else:
            situacao = SITUACAO_CONCLUIDA

        if gerada is not None and gerada.fila_id is not None:
            fila_inicial = gerada.fila_id
        else:
            fila_inicial = None if parcial else senha.fila_id  # sem realocação, a fila nunca mudou

        if not operador or any(seg[1].usuario_id == operador for seg in atd):
            linhas.append({
                "senha_id": senha.id,
                "codigo": senha.codigo,
                "criada_em": senha.criada_em,
                "unidade_id": senha.unidade_id,
                "fila_inicial_id": fila_inicial,
                "fila_final_id": senha.fila_id,
                "area_id": area_de.get(senha.fila_id),  # área ATUAL da fila final (10.76)
                "espera_inicial_segundos": segundos(esp[0]) if esp and esp[0][1].evento == "gerada" else None,
                "espera_total_segundos": sum(map(segundos, esp)) if esp and not parcial else None,
                "atendimento_total_segundos": sum(map(segundos, atd)) if fim is not None and atd and not parcial else None,
                "etapas": len(atd) if atd and not parcial else None,
                "operador_id": fim.usuario_id if fim is not None else None,
                "local_id": senha.local_id if fim is not None else None,
                "situacao": situacao,
            })

        # Numeração da etapa na jornada COMPLETA da senha, antes do filtro de
        # operador (não "pula" números quando o filtro esconde etapas).
        for numero, seg in enumerate(sorted(esp + atd, key=lambda seg: seg[1].timestamp), start=1):
            tipo, abertura, fechamento, fila_id, local_id = seg
            operador_etapa = fechamento.usuario_id if tipo == TIPO_ETAPA_ESPERA else abertura.usuario_id
            if operador and operador_etapa != operador:
                continue
            etapas.append({
                "senha_id": senha.id,
                "codigo": senha.codigo,
                "criada_em": senha.criada_em,
                "unidade_id": senha.unidade_id,
                "numero": numero,
                "tipo": tipo,
                "fila_id": fila_id,
                "area_id": area_de.get(fila_id),
                "local_id": local_id,
                "operador_id": operador_etapa,
                "inicio": abertura.timestamp,
                "fim": fechamento.timestamp,
                "segundos": segundos(seg),
                "historico_parcial": parcial,
            })
    return linhas, etapas


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
            criada_hoje_na_clinica(),  # adendo 10.77
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


def contar_senhas_abertas_dias_anteriores(empresa, unidades_visiveis):
    """Dashboard (adendo 10.77): senhas ainda abertas criadas antes de hoje no
    dia da clínica, nas unidades visíveis. Com a varredura do middleware
    funcionando é sempre 0 - o alerta existe para que uma falha dela não fique
    escondida pelo filtro de dia das telas."""
    if not unidades_visiveis:
        return 0
    return (
        senhas_abertas_de_dias_anteriores()
        .filter(Senha.empresa_id == empresa.id, Senha.unidade_id.in_(unidades_visiveis))
        .count()
    )


def coletar_alertas(filas, totens, tvs, senhas_abertas_dias_anteriores=0):
    """Dashboard: alertas de configuração incompleta, calculados em Python sobre as
    MESMAS listas já buscadas por resumo_recursos (evita 4 queries extras). Cada
    alerta é {'tipo', 'mensagem'} - lista plana, sem uma tabela/query por tipo.

    `senhas_abertas_dias_anteriores` (adendo 10.77, de
    `contar_senhas_abertas_dias_anteriores`): > 0 gera um alerta próprio."""
    alertas = []
    if senhas_abertas_dias_anteriores:
        alertas.append(
            {
                "tipo": "senhas_dias_anteriores",
                "mensagem": (
                    f"{senhas_abertas_dias_anteriores} senha(s) aberta(s) de dias anteriores - deveriam "
                    "ter sido encerradas automaticamente. Verifique o log do servidor ou rode "
                    '"flask encerrar-pendentes --antes-de-hoje".'
                ),
            }
        )
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
