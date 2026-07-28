import io

import pandas as pd
from flask import send_file


def exportar(linhas, nome_base, formato):
    """Exporta uma lista de dicts (já com nomes legíveis, não IDs crus) para CSV/Excel."""
    df = pd.DataFrame(linhas)
    buffer = io.BytesIO()

    if formato == "xlsx":
        df.to_excel(buffer, index=False, engine="openpyxl")
        mimetype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        nome_arquivo = f"{nome_base}.xlsx"
    else:
        # utf-8-sig (BOM) para o Excel no Windows reconhecer acentuação ao abrir o CSV direto
        df.to_csv(buffer, index=False, encoding="utf-8-sig")
        mimetype = "text/csv"
        nome_arquivo = f"{nome_base}.csv"

    buffer.seek(0)
    return send_file(buffer, mimetype=mimetype, as_attachment=True, download_name=nome_arquivo)
