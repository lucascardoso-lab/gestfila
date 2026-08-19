from flask import render_template, request

from app.admin.helpers import resolver_empresa_contexto
from app.models import Empresa

from . import relatorios_bp
from .export import exportar
from .helpers import _unidades_ids_visiveis, filtros_para_querystring, mapas_nomes, opcoes_filtro, resolver_filtros
from .queries import (
    TIPO_FECHAMENTO_ROTULOS,
    coletar_alertas,
    ranking_operadores,
    resumo_recursos,
    senhas_aguardando_por_fila,
    tempo_atendimento,
    tempo_medio_espera,
    ultimas_chamadas,
    volume_senhas,
)


def _empresa_ou_selecao(destino):
    empresa = resolver_empresa_contexto()
    if empresa is None:
        return None, render_template(
            "admin/selecionar_empresa.html",
            empresas=Empresa.query.order_by(Empresa.nome).all(),
            destino=destino,
        )
    return empresa, None


@relatorios_bp.route("/")
def index():
    empresa, selecao = _empresa_ou_selecao("relatorios.index")
    if empresa is None:
        return selecao
    return render_template("relatorios/index.html", empresa=empresa)


@relatorios_bp.route("/dashboard")
def dashboard():
    """Dashboard operacional (fora do BLUEPRINT original) - visão geral de saúde do
    sistema para admin/gerencial: contagens de filas/totens/TVs, senhas aguardando
    (total e por fila), feed de últimas chamadas e alertas de configuração
    incompleta. Query direta por request (sem WebSocket) - ver justificativa no
    plano: os únicos emits hoje (atendimento/helpers.py) são por sala de fila e não
    cobririam totens/TVs/alertas sem uma mudança bem maior."""
    empresa, selecao = _empresa_ou_selecao("relatorios.dashboard")
    if empresa is None:
        return selecao

    unidades_visiveis = _unidades_ids_visiveis(empresa)
    recursos = resumo_recursos(empresa, unidades_visiveis)
    aguardando_por_fila = senhas_aguardando_por_fila(empresa, unidades_visiveis)
    chamadas = ultimas_chamadas(empresa, unidades_visiveis)
    alertas = coletar_alertas(recursos["filas"], recursos["totens"], recursos["tvs"])

    filas_com_espera = [
        {"fila": fila, "aguardando": aguardando_por_fila.get(fila.id, 0)}
        for fila in recursos["filas"]
        if fila.ativo
    ]

    filas_nomes = {fila.id: fila.nome for fila in recursos["filas"]}
    _, _, _, locais = mapas_nomes(empresa)
    for chamada in chamadas:
        chamada["fila"] = filas_nomes.get(chamada["fila_id"], "—")
        # Só identificador_admin - mesmo motivo do dashboard operacional
        # (core/routes.py), BLUEPRINT.md seção 10.32: a coluna "Fila" já existe
        # ao lado, formato combinado descartado após conferência visual real.
        chamada["local"] = locais.get(chamada["local_id"], "—") if chamada["local_id"] else "—"

    return render_template(
        "relatorios/dashboard.html",
        empresa=empresa,
        recursos=recursos,
        senhas_aguardando_total=sum(aguardando_por_fila.values()),
        filas_com_espera=filas_com_espera,
        chamadas=chamadas,
        alertas=alertas,
    )


@relatorios_bp.route("/tempo-espera")
def tempo_espera():
    empresa, selecao = _empresa_ou_selecao("relatorios.tempo_espera")
    if empresa is None:
        return selecao

    filtros = resolver_filtros(empresa)
    linhas = tempo_medio_espera(filtros)
    filas, unidades, _, _ = mapas_nomes(empresa)
    for linha in linhas:
        linha["fila"] = filas.get(linha["fila_id"], "—")
        linha["unidade"] = unidades.get(linha["unidade_id"], "—")

    if request.args.get("formato") in ("csv", "xlsx"):
        colunas = [
            {"data": l["data"], "unidade": l["unidade"], "fila": l["fila"],
             "quantidade": l["quantidade"], "tempo_medio_segundos": l["tempo_medio_segundos"]}
            for l in linhas
        ]
        return exportar(colunas, "tempo_medio_espera", request.args["formato"])

    return render_template(
        "relatorios/tempo_espera.html", empresa=empresa, filtros=filtros, linhas=linhas,
        opcoes=opcoes_filtro(empresa), querystring=filtros_para_querystring(filtros, empresa),
    )


@relatorios_bp.route("/tempo-atendimento")
def tempo_atendimento_view():
    empresa, selecao = _empresa_ou_selecao("relatorios.tempo_atendimento_view")
    if empresa is None:
        return selecao

    filtros = resolver_filtros(empresa)
    linhas = tempo_atendimento(filtros)
    filas, _, operadores, locais = mapas_nomes(empresa)
    for linha in linhas:
        linha["fila"] = filas.get(linha["fila_id"], "—")
        linha["operador"] = operadores.get(linha["operador_id"], "—") if linha["operador_id"] else "—"
        # Só identificador_admin - mesmo motivo do dashboard operacional
        # (core/routes.py), BLUEPRINT.md seção 10.32: a coluna "Fila" já existe
        # ao lado, formato combinado descartado após conferência visual real.
        linha["local"] = locais.get(linha["local_id"], "—") if linha["local_id"] else "—"
        # Rótulo pro humano (Conclusão/Repasse, adendo 10.63) - a linha já
        # carrega o valor bruto ("conclusao"/"repasse") em tipo_fechamento,
        # este campo novo é só a versão exibível (HTML + export).
        linha["tipo_fechamento_rotulo"] = TIPO_FECHAMENTO_ROTULOS[linha["tipo_fechamento"]]

    if request.args.get("formato") in ("csv", "xlsx"):
        colunas = [
            {"operador": l["operador"], "setor": l["setor"], "fila": l["fila"], "local": l["local"],
             "tipo": l["tipo_fechamento_rotulo"],
             "quantidade": l["quantidade"], "tempo_medio_segundos": l["tempo_medio_segundos"]}
            for l in linhas
        ]
        return exportar(colunas, "tempo_atendimento", request.args["formato"])

    return render_template(
        "relatorios/tempo_atendimento.html", empresa=empresa, filtros=filtros, linhas=linhas,
        opcoes=opcoes_filtro(empresa), querystring=filtros_para_querystring(filtros, empresa),
    )


@relatorios_bp.route("/volume")
def volume():
    empresa, selecao = _empresa_ou_selecao("relatorios.volume")
    if empresa is None:
        return selecao

    filtros = resolver_filtros(empresa)
    linhas = volume_senhas(filtros)
    filas, unidades, _, _ = mapas_nomes(empresa)
    for linha in linhas:
        linha["fila"] = filas.get(linha["fila_id"], "—")
        linha["unidade"] = unidades.get(linha["unidade_id"], "—")

    if request.args.get("formato") in ("csv", "xlsx"):
        colunas = [
            {"data": l["data"], "unidade": l["unidade"], "fila": l["fila"],
             "prioridade": l["prioridade"], "normal": l["normal"], "total": l["total"]}
            for l in linhas
        ]
        return exportar(colunas, "volume_senhas", request.args["formato"])

    return render_template(
        "relatorios/volume.html", empresa=empresa, filtros=filtros, linhas=linhas,
        opcoes=opcoes_filtro(empresa), querystring=filtros_para_querystring(filtros, empresa),
    )


@relatorios_bp.route("/ranking-operadores")
def ranking():
    empresa, selecao = _empresa_ou_selecao("relatorios.ranking")
    if empresa is None:
        return selecao

    filtros = resolver_filtros(empresa)
    linhas = ranking_operadores(filtros)
    _, _, operadores, _ = mapas_nomes(empresa)
    for linha in linhas:
        linha["operador"] = operadores.get(linha["operador_id"], "—")

    if request.args.get("formato") in ("csv", "xlsx"):
        colunas = [
            {"operador": l["operador"], "volume": l["volume"], "tempo_medio_segundos": l["tempo_medio_segundos"]}
            for l in linhas
        ]
        return exportar(colunas, "ranking_operadores", request.args["formato"])

    return render_template(
        "relatorios/ranking.html", empresa=empresa, filtros=filtros, linhas=linhas,
        opcoes=opcoes_filtro(empresa), querystring=filtros_para_querystring(filtros, empresa),
    )
