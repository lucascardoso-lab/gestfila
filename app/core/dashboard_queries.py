"""Consultas do dashboard operacional (adendo 10.12.2 do BLUEPRINT, revisto no
10.86) - tela padrão pós-login pra admin/gerencial/super admin (e também
acessível, com escopo mais restrito, a atendente/executor).

Reaproveita, sem duplicar, as consultas dos relatórios (`app/relatorios/
queries.py`): `_senhas_filtradas` (a MESMA seleção de senhas do Tempos por
senha e do Volume), `tempos_por_senha` + `_resumo_tempos` (média e mediana POR
SENHA, sobre os segundos exatos - nenhum cálculo novo) e `ultimas_chamadas`. Só
o que é específico desta tela mora aqui: o período do Dashboard (limite de 31
dias), as contagens por senha, "aguardando agora"/"acima do tempo máximo", o
bloco Prioritários x Normal, o gráfico e a lista de filas com status.
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

from flask import g, request, session
from flask_login import current_user
from sqlalchemy import exists

from app.admin.helpers import CHAVE_SESSAO_UNIDADE_CONTEXTO, resolver_unidade_contexto, unidades_da_empresa
from app.models import AreaFila, Fila, LogAtendimento, Senha, Unidade
from app.models.senha import criada_hoje_na_clinica
from app.relatorios.helpers import Filtros, resolver_filtros
from app.relatorios.queries import _resumo_tempos, _senhas_filtradas, tempos_por_senha
from app.utils.tempo import (
    agora_na_clinica,
    agora_utc_naive,
    formatar_duracao,
    formatar_hora_clinica,
    janela_dia_clinica_em_utc,
    para_horario_clinica,
)

# Adendo 10.86: período máximo do filtro De/Até. `tempos_por_senha` custa ~0,13 s
# por 1.000 senhas (medido: 31 dias x 600 senhas/dia ~ 4 s em SQLite local) - se
# o carregamento de 31 dias passar de ~8 s em produção, a saída documentada é
# reduzir este limite.
LIMITE_DIAS_DASHBOARD = 31

# Cartões de tempo (chave do Tempos por senha, rótulo) - mesma chave de
# TEMPOS_POR_SENHA/_resumo_tempos, então o número é o mesmo do relatório.
TEMPOS_DASHBOARD = (
    ("espera_inicial", "Espera inicial"),
    ("atendimento_executor", "Atendimento executor"),
    ("permanencia_total", "Permanência total"),
)


def _pode_estreitar_unidade():
    return current_user.is_super_admin or current_user.tem_perfil("admin", "gerencial")


def _unidade_sem_persistir(empresa):
    """Unidade escolhida SEM gravar na sessão (polling, adendo 10.86): o
    `unidade_id` explícito da querystring (vazio = todas) ou, sem ele, a última
    escolha salva. `resolver_unidade_contexto` grava a sessão a cada chamada -
    o polling de uma aba sobrescreveria a escolha feita em outra aba."""
    if "unidade_id" in request.args:
        unidade_id = request.args.get("unidade_id", type=int)
    else:
        unidade_id = session.get(CHAVE_SESSAO_UNIDADE_CONTEXTO)
    if not unidade_id:
        return None
    return Unidade.query.filter_by(id=unidade_id, empresa_id=empresa.id).first()


def unidades_visiveis_dashboard(empresa, persistir=True):
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

    `persistir=False` (polling, adendo 10.86): mesma regra, sem gravar a
    escolha na sessão (ver `_unidade_sem_persistir`).
    """
    todas = {u.id for u in unidades_da_empresa(empresa)}

    if _pode_estreitar_unidade():
        unidade_escolhida = resolver_unidade_contexto(empresa) if persistir else _unidade_sem_persistir(empresa)
        if unidade_escolhida is not None:
            return {unidade_escolhida.id}
        return todas

    permitidas = set(g.unidades_permitidas or [])
    return todas & permitidas


@dataclass
class PeriodoDashboard:
    filtros: Filtros
    inicio: date
    fim: date
    hoje: date
    aviso: Optional[str] = None

    @property
    def inclui_hoje(self):
        return self.inicio <= self.hoje <= self.fim

    @property
    def exatamente_hoje(self):
        return self.inicio == self.fim == self.hoje

    @property
    def um_dia(self):
        return self.inicio == self.fim


def resolver_periodo_dashboard(empresa, unidades_visiveis):
    """Filtros do Dashboard (adendo 10.86).

    - Datas e Área: a MESMA validação dos relatórios (`resolver_filtros`: data
      inválida vira hoje, padrão "hoje", dia civil da clínica - 10.54/10.74;
      Área de unidade visível - 10.76). Além dela: De > Até inverte, e acima de
      LIMITE_DIAS_DASHBOARD recorta para os 31 dias que terminam em "Até", com
      aviso.
    - Unidade: do escopo do Dashboard (`unidades_visiveis`, RBAC do 10.12.2) -
      nunca do `unidade_id` validado pelos relatórios. Por isso Área de unidade
      fora desse escopo (ex.: admin estreitado em outra unidade) é descartada.
    - Fila/operador: o Dashboard não filtra por eles.
    """
    base = resolver_filtros(empresa)
    inicio = datetime.strptime(base.data_inicio_str, "%Y-%m-%d").date()
    fim = datetime.strptime(base.data_fim_str, "%Y-%m-%d").date()
    if inicio > fim:
        inicio, fim = fim, inicio
    aviso = None
    if (fim - inicio).days + 1 > LIMITE_DIAS_DASHBOARD:
        inicio = fim - timedelta(days=LIMITE_DIAS_DASHBOARD - 1)
        aviso = (f"Período maior que {LIMITE_DIAS_DASHBOARD} dias: mostrando "
                 f"{inicio:%d/%m/%Y} a {fim:%d/%m/%Y}.")

    area_fila_id = base.area_fila_id
    if area_fila_id is not None:
        area = AreaFila.query.filter_by(id=area_fila_id, empresa_id=empresa.id).first()
        if area is None or area.unidade_id not in unidades_visiveis:
            area_fila_id = None
    return montar_periodo(empresa.id, unidades_visiveis, inicio, fim, area_fila_id=area_fila_id, aviso=aviso)


def montar_periodo(empresa_id, unidades_visiveis, inicio=None, fim=None, area_fila_id=None, aviso=None):
    """`PeriodoDashboard` de datas JÁ validadas (dias civis da clínica; padrão
    hoje) - a janela UTC de cada dia vem de `janela_dia_clinica_em_utc`, como nos
    relatórios (10.54/10.74). Usado por `resolver_periodo_dashboard` e por quem
    calcula os cartões fora de uma request (testes)."""
    agora = agora_na_clinica()
    hoje, fuso = agora.date(), agora.tzinfo
    inicio = inicio or hoje
    fim = fim or hoje
    data_inicio, _ = janela_dia_clinica_em_utc(datetime.combine(inicio, datetime.min.time(), tzinfo=fuso))
    _, data_fim_exclusivo = janela_dia_clinica_em_utc(datetime.combine(fim, datetime.min.time(), tzinfo=fuso))
    filtros = Filtros(
        empresa_id=empresa_id, unidades_permitidas=sorted(unidades_visiveis), unidade_id=None,
        fila_id=None, operador_id=None, data_inicio=data_inicio, data_fim_exclusivo=data_fim_exclusivo,
        data_inicio_str=inicio.isoformat(), data_fim_str=fim.isoformat(), area_fila_id=area_fila_id,
    )
    return PeriodoDashboard(filtros=filtros, inicio=inicio, fim=fim, hoje=hoje, aviso=aviso)


def fila_ids_da_area(filtros):
    """Filas da Área filtrada (None = sem filtro de Área)."""
    if not filtros.area_fila_id:
        return None
    return {fila_id for fila_id, in Fila.query.filter_by(
        empresa_id=filtros.empresa_id, area_fila_id=filtros.area_fila_id).with_entities(Fila.id)}


def areas_visiveis_dashboard(empresa, unidades_visiveis):
    """Opções do filtro de Área: áreas das unidades do escopo, inclusive as
    inativas (consultáveis, marcadas "(inativa)" - mesmo critério do 10.76)."""
    return sorted(
        (a for a in AreaFila.query.filter_by(empresa_id=empresa.id).all() if a.unidade_id in unidades_visiveis),
        key=lambda a: (a.unidade.nome, a.nome),
    )


def _aguardando_agora(empresa, unidades_visiveis, fila_ids_area):
    """(aguardando, acima do tempo máximo) agora - senhas 'aguardando' de hoje
    (dia da clínica, 10.77). "Acima do tempo máximo" usa a regra do 10.52, nível
    "estourado": espera desde a emissão >= tempo_espera_maximo_min da fila; fila
    sem limite (ou limite <= 0) nunca entra."""
    query = Senha.query.filter(
        Senha.empresa_id == empresa.id,
        Senha.unidade_id.in_(unidades_visiveis),
        Senha.status == "aguardando",
        criada_hoje_na_clinica(),
    )
    if fila_ids_area is not None:
        query = query.filter(Senha.fila_id.in_(fila_ids_area))
    senhas = query.with_entities(Senha.fila_id, Senha.criada_em).all()

    limites = dict(Fila.query.filter_by(empresa_id=empresa.id).with_entities(Fila.id, Fila.tempo_espera_maximo_min))
    agora_utc = agora_utc_naive()  # mesma base de criada_em, mesmo relógio de "hoje"
    acima = 0
    for fila_id, criada_em in senhas:
        limite = limites.get(fila_id)
        if limite is not None and limite > 0 and (agora_utc - criada_em).total_seconds() >= limite * 60:
            acima += 1
    return len(senhas), acima


def _formatar_resumo(resumo):
    return {
        "quantidade": resumo["quantidade"],
        "media_segundos": resumo["media"],
        "mediana_segundos": resumo["mediana"],
        # mesma formatação dos relatórios (10.75); sem dado -> None ("—" na tela)
        "media_texto": formatar_duracao(resumo["media"]) or None,
        "mediana_texto": formatar_duracao(resumo["mediana"]) or None,
    }


def calcular_tempos(filtros):
    """Cartões de tempo + Prioritários x Normal (adendo 10.86) - UMA chamada de
    `tempos_por_senha(filtros)` e `_resumo_tempos`, exatamente como o Tempos
    por senha faz: no mesmo filtro, os números são os mesmos do relatório.

    Prioritário = `Senha.prioridade` (gravado na emissão: peso do tipo > 0, ou a
    prioridade padrão da fila quando não há tipo) - o mesmo do badge "P", do
    Chamar e do relatório de Volume."""
    linhas, _ = tempos_por_senha(filtros)
    resumo = _resumo_tempos(linhas)
    prioridade = dict(_senhas_filtradas(filtros).with_entities(Senha.id, Senha.prioridade))
    grupos = {}
    for grupo, valor in (("prioritario", True), ("normal", False)):
        do_grupo = [l for l in linhas if bool(prioridade.get(l["senha_id"])) is valor]
        grupos[grupo] = {"geradas": len(do_grupo), **_formatar_resumo(_resumo_tempos(do_grupo)["espera_inicial"])}
    return {
        "tempos": {chave: _formatar_resumo(resumo[chave]) for chave, _rotulo in TEMPOS_DASHBOARD},
        "prioritarios": grupos,
        "calculado_em": formatar_hora_clinica(agora_utc_naive()),
    }


def calcular_kpis(empresa, unidades_visiveis, periodo, com_tempos=True):
    """Cartões do Dashboard (adendo 10.86), contados POR SENHA:

    - `geradas`: senhas emitidas no período (`_senhas_filtradas` - a mesma
      seleção e o mesmo total do Tempos por senha e do Volume).
    - `atendidas`: das geradas, as que têm `fim_atendimento` ("Atendidas (das
      geradas)") - uma senha realocada ou chamada de novo depois de "Aguardar"
      conta 1. Fecha com Concluída + Histórico parcial do Tempos por senha.
    - `aguardando`/`acima_maximo`: "agora" - só quando o período inclui hoje
      (None nos demais).
    - `com_tempos`: cartões de tempo e Prioritários x Normal (`calcular_tempos`).
    """
    filtros = periodo.filtros
    geradas = _senhas_filtradas(filtros).count()
    atendidas = _senhas_filtradas(filtros).filter(exists().where(
        LogAtendimento.senha_id == Senha.id, LogAtendimento.evento == "fim_atendimento"
    )).count()

    dados = {"geradas": geradas, "atendidas": atendidas, "aguardando": None, "acima_maximo": None,
             "inclui_hoje": periodo.inclui_hoje}
    if periodo.inclui_hoje:
        dados["aguardando"], dados["acima_maximo"] = _aguardando_agora(
            empresa, unidades_visiveis, fila_ids_da_area(filtros))
    if com_tempos:
        dados.update(calcular_tempos(filtros))
    return dados


def senhas_geradas_no_periodo(periodo):
    """Gráfico (adendo 10.86): senhas geradas por HORA (período de 1 dia) ou por
    DIA (mais de 1 dia), agrupadas no servidor, no fuso da clínica
    (`para_horario_clinica`) - hora e dia na mesma base (10.74)."""
    criadas = [para_horario_clinica(c) for c, in _senhas_filtradas(periodo.filtros).with_entities(Senha.criada_em)]
    if periodo.um_dia:
        valores = [0] * 24
        for momento in criadas:
            valores[momento.hour] += 1
        return {"titulo": "Senhas geradas por hora", "rotulos": [f"{h:02d}h" for h in range(24)], "valores": valores}

    dias = [periodo.inicio + timedelta(days=i) for i in range((periodo.fim - periodo.inicio).days + 1)]
    contagem = {dia: 0 for dia in dias}
    for momento in criadas:
        if momento.date() in contagem:
            contagem[momento.date()] += 1
    return {"titulo": "Senhas geradas por dia", "rotulos": [f"{d:%d/%m}" for d in dias],
            "valores": [contagem[d] for d in dias]}


def filas_com_status(empresa, unidades_visiveis, fila_ids_area=None):
    """Lista de filas com status colorido (adendo 10.12.2): Ativa (verde, dentro do
    horário de funcionamento agora), Fora do horário (laranja - ativa, mas
    `Fila.aberta_em()` nega agora) ou Inativa (cinza). Reaproveita `aberta_em()`
    (seção 10.1/10.5), já testado, sem nenhuma lógica de horário nova.
    `fila_ids_area` (adendo 10.86): só as filas da Área filtrada."""
    query = Fila.query.filter(Fila.empresa_id == empresa.id, Fila.unidade_id.in_(unidades_visiveis))
    if fila_ids_area is not None:
        query = query.filter(Fila.id.in_(fila_ids_area))
    filas = query.order_by(Fila.nome).all()
    contagens = {}
    for fila_id, in (
        Senha.query.filter(
            Senha.empresa_id == empresa.id,
            Senha.unidade_id.in_(unidades_visiveis),
            Senha.status == "aguardando",
            criada_hoje_na_clinica(),  # adendo 10.77
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
