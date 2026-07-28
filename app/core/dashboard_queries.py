"""Consultas do dashboard operacional (adendo 10.12.2 do BLUEPRINT) - tela padrão
pós-login pra admin/gerencial/super admin (e também acessível, com escopo mais
restrito, a atendente/executor).

Reaproveita, sem alterar, as consultas já existentes em `app/relatorios/queries.py`
(`ultimas_chamadas`) e `app/relatorios/helpers.py` (`mapas_nomes`, `unidades_da_empresa`)
pra não duplicar lógica já correta - só o que é específico desta tela nova (os 4
KPIs, o gráfico por hora e a lista de filas com status colorido) mora aqui.
"""
from datetime import datetime, timedelta

from flask import g
from flask_login import current_user

from app.admin.helpers import resolver_unidade_contexto, unidades_da_empresa
from app.models import Fila, LogAtendimento, Senha

# Mesma definição de "chamada" já usada em relatorios/queries.py (EVENTOS_INICIO_
# ATENDIMENTO) - 'chamada_nome' deliberadamente NÃO é gravado pelo Realocar (seção 7
# do BLUEPRINT), então esta contagem já não duplica uma única visita como 2 chamadas.
EVENTOS_CHAMADA = ("chamada_senha", "chamada_nome")


def unidades_visiveis_dashboard(empresa):
    """Escopo por perfil (adendo 10.12.2): super admin e os perfis 'admin'/
    'gerencial' veem a empresa inteira por padrão, com a opção de estreitar pra uma
    única unidade via a mesma barra de contexto já usada no painel admin
    (`resolver_unidade_contexto`, seção 10.22) - narrowing seguro, já que o teto de
    visibilidade deles já é a empresa toda. Perfis operacionais (atendente/executor/
    atendente_executor) NUNCA passam por `resolver_unidade_contexto` aqui de
    propósito - o escopo deles é sempre exatamente `g.unidades_permitidas` (as
    unidades explicitamente atribuídas, seção 10.19), sem nenhuma forma de
    estreitar/ampliar via querystring; isso fecha a possibilidade de um operador
    tentar ver outra unidade da mesma empresa manipulando `unidade_id` na URL.
    """
    todas = {u.id for u in unidades_da_empresa(empresa)}

    if current_user.is_super_admin or current_user.tem_perfil("admin", "gerencial"):
        unidade_escolhida = resolver_unidade_contexto(empresa)
        if unidade_escolhida is not None:
            return {unidade_escolhida.id}
        return todas

    permitidas = set(g.unidades_permitidas or [])
    return todas & permitidas


def _hoje_utc():
    """Início/fim (exclusivo) do dia UTC atual - mesma convenção já usada em todo o
    projeto pra 'hoje' (reset diário do sequencial do totem, histórico da TV): compara
    a data em UTC, não a data local da clínica, pra ficar consistente com o resto do
    sistema (timestamps sempre gravados como UTC "naive")."""
    hoje = datetime.utcnow().date()
    inicio = datetime.combine(hoje, datetime.min.time())
    return inicio, inicio + timedelta(days=1)


def calcular_kpis(empresa, unidades_visiveis):
    """Os 4 KPIs do topo do dashboard (adendo 10.12.2): senhas aguardando agora,
    chamadas de hoje, tempo médio de espera de hoje e quantidade de filas ativas -
    todos já escopados pelas unidades visíveis ao perfil logado."""
    if not unidades_visiveis:
        return {"aguardando": 0, "chamadas_hoje": 0, "tempo_medio_espera_segundos": None, "filas_ativas": 0}

    aguardando = Senha.query.filter(
        Senha.empresa_id == empresa.id,
        Senha.unidade_id.in_(unidades_visiveis),
        Senha.status == "aguardando",
    ).count()

    inicio_hoje, fim_hoje = _hoje_utc()

    chamadas_hoje = LogAtendimento.query.filter(
        LogAtendimento.unidade_id.in_(unidades_visiveis),
        LogAtendimento.evento.in_(EVENTOS_CHAMADA),
        LogAtendimento.timestamp >= inicio_hoje,
        LogAtendimento.timestamp < fim_hoje,
    ).count()

    filas_ativas = Fila.query.filter(
        Fila.empresa_id == empresa.id,
        Fila.unidade_id.in_(unidades_visiveis),
        Fila.ativo.is_(True),
    ).count()

    return {
        "aguardando": aguardando,
        "chamadas_hoje": chamadas_hoje,
        "tempo_medio_espera_segundos": _tempo_medio_espera_hoje(empresa, unidades_visiveis, inicio_hoje, fim_hoje),
        "filas_ativas": filas_ativas,
    }


def _tempo_medio_espera_hoje(empresa, unidades_visiveis, inicio_hoje, fim_hoje):
    """Tempo médio entre 'gerada' e 'chamada_senha' (mesmo par de eventos de
    `tempo_medio_espera` em relatorios/queries.py), só que agregado num único número
    (não por fila/dia) e restrito às senhas CRIADAS hoje - None sem nenhuma senha
    chamada ainda hoje, nunca 0 (evita sugerir 'espera zero' quando na verdade não há
    dado nenhum)."""
    senha_ids = [
        row.id
        for row in Senha.query.filter(
            Senha.empresa_id == empresa.id,
            Senha.unidade_id.in_(unidades_visiveis),
            Senha.criada_em >= inicio_hoje,
            Senha.criada_em < fim_hoje,
        ).with_entities(Senha.id)
    ]
    if not senha_ids:
        return None

    logs = LogAtendimento.query.filter(
        LogAtendimento.senha_id.in_(senha_ids),
        LogAtendimento.evento.in_(("gerada", "chamada_senha")),
    ).all()

    eventos_por_senha = {}
    for log in logs:
        eventos_por_senha.setdefault(log.senha_id, {})[log.evento] = log.timestamp

    tempos = []
    for eventos in eventos_por_senha.values():
        gerada_em = eventos.get("gerada")
        chamada_em = eventos.get("chamada_senha")
        if gerada_em is not None and chamada_em is not None:
            tempos.append((chamada_em - gerada_em).total_seconds())

    if not tempos:
        return None
    return round(sum(tempos) / len(tempos))


def filas_com_status(empresa, unidades_visiveis):
    """Lista de filas com status colorido (adendo 10.12.2): Ativa (verde, dentro do
    horário de funcionamento agora), Fora do horário (laranja - ativa, mas
    `Fila.aberta_em()` nega agora) ou Inativa (cinza). Reaproveita `aberta_em()`
    (seção 10.1/10.5), já testado, sem nenhuma lógica de horário nova."""
    filas = (
        Fila.query.filter(Fila.empresa_id == empresa.id, Fila.unidade_id.in_(unidades_visiveis))
        .order_by(Fila.nome)
        .all()
    )
    contagens = {}
    for fila_id, in (
        Senha.query.filter(
            Senha.empresa_id == empresa.id,
            Senha.unidade_id.in_(unidades_visiveis),
            Senha.status == "aguardando",
        ).with_entities(Senha.fila_id)
    ):
        contagens[fila_id] = contagens.get(fila_id, 0) + 1

    resultado = []
    for fila in filas:
        if not fila.ativo:
            status = "inativa"
        elif fila.aberta_em():
            status = "ativa"
        else:
            status = "fora_horario"
        resultado.append({"fila": fila, "status": status, "aguardando": contagens.get(fila.id, 0)})
    return resultado


def primeiro_nome(nome_completo):
    """Nome do paciente na tabela de últimos atendimentos (adendo 10.12.2, item 5) -
    só o primeiro nome, por privacidade/compacidade na tela. `nome_paciente` é texto
    livre (seção 10.2, sem cadastro) - sempre pode vir vazio/None."""
    if not nome_completo:
        return nome_completo
    partes = nome_completo.strip().split()
    return partes[0] if partes else nome_completo


def chamadas_por_hora_hoje(unidades_visiveis):
    """Timestamps (ISO, UTC) de cada chamada de hoje - o agrupamento por HORA em si
    é feito em JavaScript (core/dashboard.html), no fuso horário local do navegador
    de quem está vendo o dashboard, não no servidor - mesma convenção já estabelecida
    no projeto pra qualquer exibição de horário (nunca formatar hora no servidor;
    ver comentários em atendimento/painel_fila.html). 'Hoje', porém, continua sendo o
    dia UTC (consistente com o resto do projeto - ver `_hoje_utc`)."""
    inicio_hoje, fim_hoje = _hoje_utc()
    if not unidades_visiveis:
        return []
    linhas = (
        LogAtendimento.query.filter(
            LogAtendimento.unidade_id.in_(unidades_visiveis),
            LogAtendimento.evento.in_(EVENTOS_CHAMADA),
            LogAtendimento.timestamp >= inicio_hoje,
            LogAtendimento.timestamp < fim_hoje,
        )
        .with_entities(LogAtendimento.timestamp)
        .all()
    )
    return [timestamp.isoformat() for (timestamp,) in linhas]
