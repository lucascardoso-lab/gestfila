"""Script standalone (NÃO faz parte da árvore de import de `app` de propósito -
roda como arquivo, `python caminho/_pdf_worker.py`, não como `-m app.relatorios.
_pdf_worker`) que converte um HTML auto-contido (sem recursos externos - CSS já
inline, ver `relatorios/_layout_impressao.html`) em PDF via Playwright/Chromium.

Por que um script separado, chamado via subprocess (ver `pdf.py::renderizar_pdf`),
em vez de chamar Playwright direto de dentro do processo Flask: testado antes de
escrever este código (não presumido) - a API síncrona do Playwright, chamada
dentro de um processo com `eventlet.monkey_patch()` ativo (Gunicorn `--worker-
class eventlet`, README - `run.py` chama isso globalmente), emite o aviso "1
RLock(s) were not greened", sinal de que ela usa threading real não-cooperativa
com o agendador de greenthreads do eventlet. Um worker eventlet processa MUITAS
requisições concorrentes numa única thread do SO - se a geração de PDF bloquear
essa thread de verdade (não de forma cooperativa), todas as outras requisições
daquele worker ficam paradas até o PDF terminar. Rodando num subprocess (`python`
novo, SEM o monkey-patch do pai), o Playwright roda sem nenhuma interferência -
o processo PAI só espera o resultado via `subprocess.run()`, que o eventlet já
sabe tornar cooperativo (não bloqueia as outras requisições enquanto espera).

Lê o HTML da stdin, escreve os bytes do PDF na stdout - sem argumentos de
linha de comando (evita qualquer limite de tamanho de argv em relatórios
grandes) e sem tocar em `app/relatorios/__init__.py` (que importaria toda a
árvore de rotas/queries só para gerar um PDF, desnecessário aqui).

Diretório de perfil do Chromium GERENCIADO EXPLICITAMENTE (achado testando
esta correção, não presumido): `p.chromium.launch()` sozinho usa um perfil
temporário interno do próprio Chromium (pasta `scoped_dir*` dentro do temp do
SO) que, no Windows, nem sempre é removido de volta por `browser.close()` -
confirmado gerando várias dezenas de PDFs em sequência nesta máquina e
inspecionando o temp depois: as pastas ficavam pra trás (vazias, não é
vazamento de espaço em disco, mas lixo acumulando indefinidamente numa
máquina de produção rodando por meses). Corrigido usando
`launch_persistent_context(perfil_dir)` com um `tempfile.mkdtemp()` PRÓPRIO,
removido explicitamente em `finally` (`shutil.rmtree`) - testado e confirmado
sem nenhum resíduo depois, em qualquer cenário (sucesso ou exceção)."""
import shutil
import sys
import tempfile

from playwright.sync_api import sync_playwright


def gerar_pdf(html):
    perfil_dir = tempfile.mkdtemp(prefix="gestfila_pdf_")
    try:
        with sync_playwright() as p:
            contexto = p.chromium.launch_persistent_context(perfil_dir)
            pagina = contexto.new_page()
            pagina.set_content(html, wait_until="load")
            pdf_bytes = pagina.pdf(format="A4", print_background=True)
            contexto.close()
        return pdf_bytes
    finally:
        shutil.rmtree(perfil_dir, ignore_errors=True)


if __name__ == "__main__":
    conteudo_html = sys.stdin.buffer.read().decode("utf-8")
    sys.stdout.buffer.write(gerar_pdf(conteudo_html))
