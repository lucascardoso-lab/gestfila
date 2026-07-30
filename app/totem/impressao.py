"""Impressão física da senha (seção 10.6 do BLUEPRINT).

Abordagem: `python-escpos` só monta os BYTES do cupom (perfil `Dummy`, que nunca abre
conexão nenhuma - só acumula em `.output`); o envio físico é feito separadamente via
`win32print`, na fila de impressão nativa do Windows (não há comunicação USB raw).

Falha de impressão (impressora desligada, sem papel, fila travada) nunca deve
interromper nem desfazer a geração da senha - por isso esta função roda em background
(thread separada) e nunca deixa uma exceção escapar; só loga o erro.
"""
import logging
import threading
from datetime import datetime

from escpos.printer import Dummy

try:
    # win32print (pywin32) só é instalado em Windows (ver requirements.txt,
    # `sys_platform == "win32"`) - este módulo é importado por
    # app/totem/routes.py -> create_app(), ou seja, TODA a aplicação
    # (suíte de testes inteira, e o deploy real em Linux, seção 10 do
    # BLUEPRINT) passa por aqui. Um import incondicional quebraria a
    # aplicação inteira em qualquer ambiente não-Windows, mesmo em totens
    # sem impressora configurada. `win32print = None` nesse caso: o único
    # lugar que de fato usa o nome (enviar_para_impressora_windows) só é
    # chamado quando um totem tem impressora configurada - cenário que já
    # pressupõe Windows por natureza (fila de impressão nativa do SO) - e
    # está dentro do try/except de _imprimir_sincrono, que já trata
    # qualquer falha de impressão como não-fatal (nunca desfaz a senha).
    # Continua um atributo de módulo de propósito (não import local dentro
    # da função) - os testes (test_regressao_fase10_6.py) fazem
    # `patch.object(impressao, "win32print")`, que exige isso.
    import win32print
except ImportError:
    win32print = None

logger = logging.getLogger(__name__)

LARGURA_CUPOM = 32  # colunas aproximadas de uma térmica de 58mm, suficiente para o layout da seção 10.6


def montar_cupom(nome_empresa, codigo_senha, momento):
    """Monta os bytes ESC/POS do cupom: nome da empresa, código em fonte grande,
    data/hora, corte de papel ao final - layout exato da seção 10.6."""
    impressora = Dummy()
    impressora.set(align="center")
    impressora.text(f"{nome_empresa}\n")
    impressora.text("-" * LARGURA_CUPOM + "\n")
    impressora.set(align="center", double_width=True, double_height=True)
    impressora.text(f"{codigo_senha}\n")
    impressora.set(align="center", double_width=False, double_height=False)
    impressora.text("-" * LARGURA_CUPOM + "\n")
    impressora.text(momento.strftime("%d/%m/%Y  %H:%M") + "\n")
    impressora.cut()
    return impressora.output


def enviar_para_impressora_windows(nome_impressora, dados):
    """Envia `dados` (bytes ESC/POS já montados) como um trabalho RAW para a fila de
    impressão nativa do Windows - a impressora precisa já estar instalada e visível
    em "Dispositivos e Impressoras" com exatamente este nome."""
    handle = win32print.OpenPrinter(nome_impressora)
    try:
        win32print.StartDocPrinter(handle, 1, ("Senha gerada - Painel de Senhas", None, "RAW"))
        try:
            win32print.StartPagePrinter(handle)
            win32print.WritePrinter(handle, dados)
            win32print.EndPagePrinter(handle)
        finally:
            win32print.EndDocPrinter(handle)
    finally:
        win32print.ClosePrinter(handle)


def _imprimir_sincrono(nome_impressora, nome_empresa, codigo_senha, momento):
    try:
        dados = montar_cupom(nome_empresa, codigo_senha, momento)
        enviar_para_impressora_windows(nome_impressora, dados)
    except Exception:
        # log de aplicação (não logs_atendimento - isso é sobre o fluxo de
        # atendimento, não sobre infraestrutura de impressão) - a senha já foi
        # gravada e segue seu fluxo normal independente deste resultado.
        logger.exception(
            "Falha ao imprimir a senha '%s' na impressora Windows '%s'.", codigo_senha, nome_impressora,
        )


def imprimir_senha_se_configurado(totem, nome_empresa, codigo_senha, momento=None):
    """Ponto de entrada chamado pelo totem após gravar a senha no banco (seção 10.6).

    Pula silenciosamente (sem erro) se este totem não tiver impressora configurada -
    totens sem impressora física continuam funcionando normalmente, só em tela, como
    já era antes deste adendo. Quando configurada, a impressão roda em background
    (thread separada) para não travar a tela esperando a impressora responder.

    Retorna a Thread iniciada (ou None, se pulou) - só para os testes poderem
    aguardá-la (`.join()`) antes de verificar o resultado; o chamador em produção
    não precisa (nem deve) esperar por ela.
    """
    nome_impressora = (totem.nome_impressora_windows or "").strip() if totem else ""
    if not nome_impressora:
        return None

    momento = momento or datetime.now()
    thread = threading.Thread(
        target=_imprimir_sincrono, args=(nome_impressora, nome_empresa, codigo_senha, momento), daemon=True,
    )
    thread.start()
    return thread
