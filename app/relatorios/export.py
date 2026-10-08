import io

import pandas as pd
from flask import send_file

# Formato numérico das colunas de minutos no Excel (adendo 10.73): NÚMERO com
# 2 casas (não texto "mm:ss"), para a média/soma funcionar direto na planilha.
FORMATO_MINUTOS_EXCEL = "0.00"
FORMATO_DATA_HORA_EXCEL = "dd/mm/yyyy hh:mm"


def exportar(linhas, nome_base, formato, abas=None, csv_pt_br=False, colunas_minutos=()):
    """Exporta uma lista de dicts (já com nomes legíveis, não IDs crus) para CSV/Excel.

    Parâmetros opcionais (adendo 10.73 - os relatórios antigos não usam nenhum
    e continuam idênticos: uma aba, CSV com vírgula):
    - `abas`: {nome da aba: linhas} - Excel com várias abas (no lugar de `linhas`).
    - `csv_pt_br`: CSV no padrão do Excel em português (separador ";",
      decimal ",", números com 2 casas, data dd/mm/aaaa hh:mm) - aberto direto
      no Excel brasileiro, os minutos chegam como número, não como texto.
    - `colunas_minutos`: colunas formatadas como número com 2 casas no Excel.
    """
    buffer = io.BytesIO()

    if formato == "xlsx":
        with pd.ExcelWriter(buffer, engine="openpyxl") as escritor:
            for nome_aba, linhas_aba in (abas or {"Sheet1": linhas}).items():
                df = pd.DataFrame(linhas_aba)
                df.to_excel(escritor, index=False, sheet_name=nome_aba)
                _formatar_aba(escritor.sheets[nome_aba], df, colunas_minutos)
        mimetype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        nome_arquivo = f"{nome_base}.xlsx"
    else:
        df = pd.DataFrame(linhas)
        # utf-8-sig (BOM) para o Excel no Windows reconhecer acentuação ao abrir o CSV direto
        if csv_pt_br:
            df.to_csv(buffer, index=False, encoding="utf-8-sig", sep=";", decimal=",",
                      float_format="%.2f", date_format="%d/%m/%Y %H:%M")
        else:
            df.to_csv(buffer, index=False, encoding="utf-8-sig")
        mimetype = "text/csv"
        nome_arquivo = f"{nome_base}.csv"

    buffer.seek(0)
    return send_file(buffer, mimetype=mimetype, as_attachment=True, download_name=nome_arquivo)


def _formatar_aba(planilha, df, colunas_minutos):
    for indice, coluna in enumerate(df.columns, start=1):
        if coluna in colunas_minutos:
            formato = FORMATO_MINUTOS_EXCEL
        elif pd.api.types.is_datetime64_any_dtype(df[coluna]):
            formato = FORMATO_DATA_HORA_EXCEL
        else:
            continue
        for (celula,) in planilha.iter_rows(min_row=2, min_col=indice, max_col=indice):
            celula.number_format = formato
