import os
import subprocess
import sys

_CAMINHO_WORKER = os.path.join(os.path.dirname(__file__), "_pdf_worker.py")


class FalhaGeracaoPdf(Exception):
    """Levantada quando o subprocess de geração de PDF falha ou expira - o
    chamador (app/relatorios/routes.py) trata isso como erro recuperável
    (flash + volta pra tela do relatório), não um 500 cru."""


def renderizar_pdf(html):
    """Converte um HTML auto-contido (ver `relatorios/_layout_impressao.html` -
    sem `<link>`/recursos externos, CSS já inline) em PDF, rodando o worker
    (`_pdf_worker.py`) num SUBPROCESS isolado (ver docstring dele para o
    motivo - eventlet monkey-patch, testado antes de escrever este código).

    `sys.executable` garante o MESMO Python/venv do processo pai (não um
    `python` genérico do PATH, que poderia nem ter Playwright instalado)."""
    try:
        resultado = subprocess.run(
            [sys.executable, _CAMINHO_WORKER],
            input=html.encode("utf-8"),
            capture_output=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired as erro:
        raise FalhaGeracaoPdf("Tempo esgotado ao gerar o PDF (30s).") from erro

    if resultado.returncode != 0:
        detalhe = resultado.stderr.decode("utf-8", errors="replace")
        raise FalhaGeracaoPdf(f"Worker de PDF terminou com erro: {detalhe}")

    return resultado.stdout
