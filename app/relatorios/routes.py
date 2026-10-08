import math
import statistics

from flask import Response, current_app, flash, redirect, render_template, request, url_for

from app.admin.helpers import resolver_empresa_contexto
from app.models import Empresa
from app.utils.tempo import agora_na_clinica, para_horario_clinica

from . import relatorios_bp
from .export import exportar
from .helpers import (
    _unidades_ids_visiveis,
    filtros_legiveis,
    filtros_para_querystring,
    mapas_nomes,
    opcoes_filtro,
    registrar_exportacao,
    resolver_filtros,
)
from .pdf import FalhaGeracaoPdf, renderizar_pdf
from .queries import (
    DICAS_SITUACAO,
    ROTULOS_TIPO_ETAPA,
    SITUACAO_HISTORICO_PARCIAL,
    TIPO_FECHAMENTO_ROTULOS,
    coletar_alertas,
    ranking_operadores,
    resumo_recursos,
    senhas_aguardando_por_fila,
    tempo_atendimento,
    tempo_medio_espera,
    tempos_por_senha,
    ultimas_chamadas,
    volume_senhas,
)


def _exportar_relatorio(tipo_relatorio, nome_base, template, empresa, filtros, linhas, colunas, opcoes,
                        abas_excel=None, csv_pt_br=False, colunas_minutos=(), csvs_extras=None,
                        contexto_pdf=None):
    """Dispatch compartilhado pelos 3 formatos de exportação (CSV/Excel/PDF,
    adendo 10.67) - usado pelas 4 rotas de relatório abaixo. Retorna a
    Response de exportação se `?formato=` pedir um dos 3; retorna `None`
    (sinalizando "não é uma exportação, renderize a tela normal") caso
    contrário - mesmo padrão que cada rota já checava individualmente antes,
    só que agora numa função só (as 4 rotas tinham o MESMO bloco
    `if request.args.get("formato") in (...)` duplicado, e cada uma
    precisaria ganhar o mesmo trecho novo de PDF + log de auditoria -
    diferente do caso de `_segmentos_espera_por_senha`/`_segmentos_
    intermediarios_realocacao` em `queries.py`, aqui o dispatch é
    IDÊNTICO entre os 4 relatórios, não há fallback/formato de dado
    divergente que justifique manter 4 cópias).

    PDF renderiza o MESMO `template` da tela (`layout_relatorio` trocado pra
    `_layout_impressao.html` via context - ver `app/relatorios/__init__.py`),
    não um template à parte - qualquer ajuste visual futuro na tabela já
    reflete nos dois formatos automaticamente.

    `registrar_exportacao()` só é chamado APÓS o arquivo ser produzido com
    sucesso (revisão antes da aprovação) - antes, era chamado antes de saber
    se o PDF renderizava; se `renderizar_pdf()` falhasse, o log de auditoria
    já tinha gravado a exportação como se tivesse ocorrido, mesmo o usuário
    não recebendo nada além de um flash de erro. Pra um log cujo propósito é
    auditoria LGPD ("quem teve acesso a este dado"), registrar uma tentativa
    falha como acesso real é impreciso - CSV/Excel nunca tinham esse risco
    (não há um caminho de erro capturado pra eles hoje), mas a ordem foi
    corrigida pros 3 formatos por consistência, não só o PDF.

    Parâmetros opcionais (adendo 10.73, "Tempos por senha" - os 4 relatórios
    antigos não passam nenhum e continuam idênticos): `abas_excel` (Excel com
    várias abas), `csv_pt_br`/`colunas_minutos` (ver `export.exportar`),
    `csvs_extras` ({formato da querystring: (colunas, nome_base,
    tipo_relatorio)} - um CSV adicional, registrado no log de auditoria como
    formato "csv" com o próprio tipo_relatorio, sem mexer no ENUM do log) e
    `contexto_pdf` (variáveis extras para o template no PDF)."""
    formato = request.args.get("formato")
    csvs_extras = csvs_extras or {}
    if formato not in ("csv", "xlsx", "pdf", *csvs_extras):
        return None

    if formato in csvs_extras:
        colunas_extra, nome_extra, tipo_extra = csvs_extras[formato]
        resposta = exportar(colunas_extra, nome_extra, "csv", csv_pt_br=csv_pt_br)
        registrar_exportacao(tipo_extra, "csv", empresa, filtros)
        return resposta

    if formato in ("csv", "xlsx"):
        resposta = exportar(colunas, nome_base, formato, abas=abas_excel if formato == "xlsx" else None,
                            csv_pt_br=csv_pt_br, colunas_minutos=colunas_minutos)
        registrar_exportacao(tipo_relatorio, formato, empresa, filtros)
        return resposta

    html = render_template(
        template,
        empresa=empresa, filtros=filtros, linhas=linhas, opcoes=opcoes,
        querystring=filtros_para_querystring(filtros, empresa),
        layout_relatorio="relatorios/_layout_impressao.html", modo_impressao=True,
        filtros_legiveis=filtros_legiveis(filtros, opcoes),
        # Fuso da CLÍNICA (app/utils/tempo.py, seção 10.36/10.37), não UTC do
        # servidor - mesma classe de bug já corrigida 3x neste projeto
        # (10.37/10.39/10.54): um PDF impresso/arquivado mostrando "Gerado em
        # 23:47" quando na clínica são 20:47 é o tipo de coisa que o usuário
        # nota e reporta. datetime.utcnow() aqui seria reabrir esse bug num
        # 4º call-site.
        gerado_em=agora_na_clinica(),
        **(contexto_pdf or {}),
    )
    try:
        pdf_bytes = renderizar_pdf(html)
    except FalhaGeracaoPdf:
        current_app.logger.exception("Falha ao gerar PDF do relatório %s", tipo_relatorio)
        flash("Não foi possível gerar o PDF agora. Tente novamente ou use CSV/Excel.", "error")
        return redirect(url_for(request.endpoint, **filtros_para_querystring(filtros, empresa)))

    registrar_exportacao(tipo_relatorio, formato, empresa, filtros)
    return Response(
        pdf_bytes, mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{nome_base}.pdf"'},
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

    mapas = mapas_nomes(empresa)
    filas_com_espera = [
        {"fila": fila, "rotulo": mapas.filas[fila.id], "aguardando": aguardando_por_fila.get(fila.id, 0)}
        for fila in recursos["filas"]
        if fila.ativo
    ]

    for chamada in chamadas:
        chamada["fila"] = mapas.filas.get(chamada["fila_id"], "—")
        # Só identificador_admin - mesmo motivo do dashboard operacional
        # (core/routes.py), BLUEPRINT.md seção 10.32: a coluna "Fila" já existe
        # ao lado, formato combinado descartado após conferência visual real.
        chamada["local"] = mapas.locais.get(chamada["local_id"], "—") if chamada["local_id"] else "—"

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
    mapas = mapas_nomes(empresa)
    for linha in linhas:
        linha["fila"] = mapas.filas.get(linha["fila_id"], "—")
        linha["fila_nome"] = mapas.filas_nome.get(linha["fila_id"], "—")
        linha["unidade"] = mapas.unidades.get(linha["unidade_id"], "—")

    opcoes = opcoes_filtro(empresa)
    colunas = [
        {"data": l["data"], "unidade": l["unidade"], "fila": l["fila_nome"],
         "quantidade": l["quantidade"], "tempo_medio_segundos": l["tempo_medio_segundos"]}
        for l in linhas
    ]
    resposta_exportacao = _exportar_relatorio(
        "tempo_medio_espera", "tempo_medio_espera", "relatorios/tempo_espera.html",
        empresa, filtros, linhas, colunas, opcoes,
    )
    if resposta_exportacao is not None:
        return resposta_exportacao

    return render_template(
        "relatorios/tempo_espera.html", empresa=empresa, filtros=filtros, linhas=linhas,
        opcoes=opcoes, querystring=filtros_para_querystring(filtros, empresa),
    )


@relatorios_bp.route("/tempo-atendimento")
def tempo_atendimento_view():
    empresa, selecao = _empresa_ou_selecao("relatorios.tempo_atendimento_view")
    if empresa is None:
        return selecao

    filtros = resolver_filtros(empresa)
    linhas = tempo_atendimento(filtros)
    mapas = mapas_nomes(empresa)
    for linha in linhas:
        linha["fila"] = mapas.filas.get(linha["fila_id"], "—")
        linha["fila_nome"] = mapas.filas_nome.get(linha["fila_id"], "—")
        # Coluna "unidade" só no CSV/Excel (adendo 10.69) - a tela já mostra a
        # unidade no prefixo "[SIGLA]" da fila. Linha com fila conhecida usa a
        # unidade da fila; segmento de Repasse histórico sem snapshot de fila
        # (fila_id None, adendo 10.62) usa a unidade da senha.
        unidade_id = mapas.filas_unidade.get(linha["fila_id"]) or linha["unidade_id"]
        linha["unidade"] = mapas.unidades.get(unidade_id, "—")
        linha["operador"] = mapas.operadores.get(linha["operador_id"], "—") if linha["operador_id"] else "—"
        # Só identificador_admin - mesmo motivo do dashboard operacional
        # (core/routes.py), BLUEPRINT.md seção 10.32: a coluna "Fila" já existe
        # ao lado, formato combinado descartado após conferência visual real.
        linha["local"] = mapas.locais.get(linha["local_id"], "—") if linha["local_id"] else "—"
        # Rótulo pro humano (Conclusão/Repasse, adendo 10.63) - a linha já
        # carrega o valor bruto ("conclusao"/"repasse") em tipo_fechamento,
        # este campo novo é só a versão exibível (HTML + export).
        linha["tipo_fechamento_rotulo"] = TIPO_FECHAMENTO_ROTULOS[linha["tipo_fechamento"]]

    opcoes = opcoes_filtro(empresa)
    colunas = [
        {"operador": l["operador"], "setor": l["setor"], "unidade": l["unidade"], "fila": l["fila_nome"],
         "local": l["local"],
         "tipo": l["tipo_fechamento_rotulo"],
         "quantidade": l["quantidade"], "tempo_medio_segundos": l["tempo_medio_segundos"]}
        for l in linhas
    ]
    resposta_exportacao = _exportar_relatorio(
        "tempo_atendimento", "tempo_atendimento", "relatorios/tempo_atendimento.html",
        empresa, filtros, linhas, colunas, opcoes,
    )
    if resposta_exportacao is not None:
        return resposta_exportacao

    return render_template(
        "relatorios/tempo_atendimento.html", empresa=empresa, filtros=filtros, linhas=linhas,
        opcoes=opcoes, querystring=filtros_para_querystring(filtros, empresa),
    )


@relatorios_bp.route("/volume")
def volume():
    empresa, selecao = _empresa_ou_selecao("relatorios.volume")
    if empresa is None:
        return selecao

    filtros = resolver_filtros(empresa)
    linhas = volume_senhas(filtros)
    mapas = mapas_nomes(empresa)
    for linha in linhas:
        linha["fila"] = mapas.filas.get(linha["fila_id"], "—")
        linha["fila_nome"] = mapas.filas_nome.get(linha["fila_id"], "—")
        linha["unidade"] = mapas.unidades.get(linha["unidade_id"], "—")

    opcoes = opcoes_filtro(empresa)
    colunas = [
        {"data": l["data"], "unidade": l["unidade"], "fila": l["fila_nome"],
         "prioridade": l["prioridade"], "normal": l["normal"], "total": l["total"]}
        for l in linhas
    ]
    resposta_exportacao = _exportar_relatorio(
        "volume_senhas", "volume_senhas", "relatorios/volume.html",
        empresa, filtros, linhas, colunas, opcoes,
    )
    if resposta_exportacao is not None:
        return resposta_exportacao

    return render_template(
        "relatorios/volume.html", empresa=empresa, filtros=filtros, linhas=linhas,
        opcoes=opcoes, querystring=filtros_para_querystring(filtros, empresa),
    )


@relatorios_bp.route("/ranking-operadores")
def ranking():
    empresa, selecao = _empresa_ou_selecao("relatorios.ranking")
    if empresa is None:
        return selecao

    filtros = resolver_filtros(empresa)
    linhas = ranking_operadores(filtros)
    operadores = mapas_nomes(empresa).operadores
    for linha in linhas:
        linha["operador"] = operadores.get(linha["operador_id"], "—")

    opcoes = opcoes_filtro(empresa)
    colunas = [
        {"operador": l["operador"], "volume": l["volume"], "tempo_medio_segundos": l["tempo_medio_segundos"]}
        for l in linhas
    ]
    resposta_exportacao = _exportar_relatorio(
        "ranking_operadores", "ranking_operadores", "relatorios/ranking.html",
        empresa, filtros, linhas, colunas, opcoes,
    )
    if resposta_exportacao is not None:
        return resposta_exportacao

    return render_template(
        "relatorios/ranking.html", empresa=empresa, filtros=filtros, linhas=linhas,
        opcoes=opcoes, querystring=filtros_para_querystring(filtros, empresa),
    )


# "Tempos por senha" (adendo 10.73) - constantes nomeadas, ajustáveis quando o
# volume real de produção for conhecido.
LIMITE_LINHAS_PDF = 500
LINHAS_POR_PAGINA = 100


def _minutos(segundos):
    """Segundos -> minutos com 2 casas (exportação); None continua None - sem
    dado fica VAZIO, nunca 0."""
    return round(segundos / 60, 2) if segundos is not None else None


def _resumo_tempos(linhas):
    """Média, mediana e quantidade POR SENHA de cada tempo - só as senhas com o
    dado entram (vazio fica fora da conta, não vira 0)."""
    resumo = {}
    for chave in ("espera_inicial", "espera_total", "atendimento_total"):
        valores = [l[f"{chave}_min"] for l in linhas if l[f"{chave}_min"] is not None]
        resumo[chave] = {
            "quantidade": len(valores),
            "media": round(statistics.fmean(valores), 2) if valores else None,
            "mediana": round(statistics.median(valores), 2) if valores else None,
        }
    return resumo


@relatorios_bp.route("/tempos-por-senha")
def tempos_por_senha_view():
    empresa, selecao = _empresa_ou_selecao("relatorios.tempos_por_senha_view")
    if empresa is None:
        return selecao

    filtros = resolver_filtros(empresa)
    linhas, etapas = tempos_por_senha(filtros)
    mapas = mapas_nomes(empresa)

    def emissao(momento):
        # Excel não aceita datetime com fuso - hora de parede da clínica, sem tzinfo.
        return para_horario_clinica(momento).replace(tzinfo=None)

    for l in linhas:
        l["emissao"] = emissao(l["criada_em"])
        l["unidade"] = mapas.unidades.get(l["unidade_id"], "—")
        l["fila_inicial"] = mapas.filas.get(l["fila_inicial_id"], "—")
        l["fila_final"] = mapas.filas.get(l["fila_final_id"], "—")
        l["operador"] = mapas.operadores.get(l["operador_id"], "—")
        l["local"] = mapas.locais.get(l["local_id"], "—")
        l["dica_situacao"] = DICAS_SITUACAO[l["situacao"]]
        for chave in ("espera_inicial", "espera_total", "atendimento_total"):
            l[f"{chave}_min"] = _minutos(l[f"{chave}_segundos"])

    colunas = [
        {"Senha": l["codigo"], "Emissão": l["emissao"], "Unidade": l["unidade"],
         "Fila inicial": mapas.filas_nome.get(l["fila_inicial_id"]),
         "Fila final": mapas.filas_nome.get(l["fila_final_id"]),
         "Espera inicial (min)": l["espera_inicial_min"], "Espera total (min)": l["espera_total_min"],
         "Atendimento total (min)": l["atendimento_total_min"], "Etapas de atendimento": l["etapas"],
         "Operador (finalização)": mapas.operadores.get(l["operador_id"]),
         "Local (finalização)": mapas.locais.get(l["local_id"]), "Situação": l["situacao"]}
        for l in linhas
    ]
    colunas_etapas = [
        {"Senha": e["codigo"], "Emissão": emissao(e["criada_em"]),
         "Unidade": mapas.unidades.get(e["unidade_id"]), "Etapa": e["numero"],
         "Tipo": ROTULOS_TIPO_ETAPA[e["tipo"]], "Fila": mapas.filas_nome.get(e["fila_id"]),
         # Coluna única (adendo 10.73): numa linha de Espera é quem CHAMOU a senha
         # (encerrou a espera); numa de Atendimento, quem atendeu. Explicado na
         # nota da tela.
         "Local": mapas.locais.get(e["local_id"]),
         "Operador (chamada/atendimento)": mapas.operadores.get(e["operador_id"]),
         "Início": emissao(e["inicio"]), "Fim": emissao(e["fim"]), "Minutos": _minutos(e["segundos"]),
         "Observação": SITUACAO_HISTORICO_PARCIAL if e["historico_parcial"] else None}
        for e in etapas
    ]
    colunas_minutos = ("Espera inicial (min)", "Espera total (min)", "Atendimento total (min)", "Minutos")

    total_paginas = max(1, math.ceil(len(linhas) / LINHAS_POR_PAGINA))
    pagina = min(max(request.args.get("pagina", 1, type=int), 1), total_paginas)
    pdf_omitido = len(linhas) > LIMITE_LINHAS_PDF
    contexto = {
        "resumo": _resumo_tempos(linhas), "total_senhas": len(linhas),
        "limite_linhas_pdf": LIMITE_LINHAS_PDF, "pdf_omitido": pdf_omitido,
        "pagina": pagina, "total_paginas": total_paginas,
    }

    opcoes = opcoes_filtro(empresa)
    resposta_exportacao = _exportar_relatorio(
        "tempos_por_senha", "tempos_por_senha", "relatorios/tempos_por_senha.html",
        empresa, filtros, linhas, colunas, opcoes,
        abas_excel={"Por senha": colunas, "Por etapa": colunas_etapas},
        csv_pt_br=True, colunas_minutos=colunas_minutos,
        csvs_extras={"csv_etapas": (colunas_etapas, "tempos_por_etapa", "tempos_por_senha_etapas")},
        contexto_pdf={**contexto, "linhas_exibidas": [] if pdf_omitido else linhas},
    )
    if resposta_exportacao is not None:
        return resposta_exportacao

    inicio = (pagina - 1) * LINHAS_POR_PAGINA
    return render_template(
        "relatorios/tempos_por_senha.html", empresa=empresa, filtros=filtros, linhas=linhas,
        linhas_exibidas=linhas[inicio:inicio + LINHAS_POR_PAGINA], opcoes=opcoes,
        querystring=filtros_para_querystring(filtros, empresa), **contexto,
    )
