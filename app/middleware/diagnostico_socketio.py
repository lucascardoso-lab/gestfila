"""Diagnóstico de POST do long-polling do Socket.IO (adendo 10.89).

O python-engineio recusa um POST com mais pacotes que
`Payload.max_decode_packets` ("Too many packets in payload"), registra só
"post request handler error" - sem sid nem tela - e responde 200: os pacotes
somem em silêncio e o navegador acha que deu certo. Este middleware WSGI fica
POR FORA do middleware do Flask-SocketIO e, só em `POST /socket.io/`, conta os
pacotes do corpo (separador \\x1e do Engine.IO 4) ANTES do engineio. Acima de
`LIMITE_PACOTES_LOG` registra um WARNING com o sid, o caminho do Referer (a
tela), o número de pacotes e os nomes dos eventos com contagem - nunca os
argumentos (nenhum dado de paciente).

O corpo é lido uma vez (exatamente CONTENT_LENGTH bytes, como o engineio já
faz) e devolvido intacto em `wsgi.input`. Sem CONTENT_LENGTH válido, corpo
vazio ou acima de `max_bytes` (o engineio recusa por conta própria), segue sem
ler nem logar.
"""
import io
import re
from urllib.parse import parse_qs, urlsplit

LIMITE_PACOTES_LOG = 16  # o limite padrão do engineio - acima disso, algo enviou demais
# Maior corpo lido: o mesmo teto do engineio (max_http_buffer_size, 1 MB por
# padrão) - lê qualquer POST que o engineio aceita (64 pacotes = alguns KB) e
# nunca mais do que o próprio engineio leria. app/__init__.py passa o valor
# efetivo do servidor; este é só o padrão da classe.
MAX_BYTES_PADRAO = 1_000_000
SEPARADOR = b"\x1e"
_EVENTO = re.compile(rb'^42(?:/[^,]*,)?\d*\["([^"\\]*)"')
_NOME_EVENTO_VALIDO = re.compile(r"[A-Za-z0-9_]{1,40}")
_TIPOS_ENGINEIO = {"1": "close", "2": "ping", "3": "pong", "4": "message", "5": "upgrade", "6": "noop"}
_TIPOS_SOCKETIO = {"0": "connect", "1": "disconnect", "2": "event", "3": "ack", "4": "connect_error",
                   "5": "binary_event", "6": "binary_ack"}


def rotulo_pacote(pacote):
    """Nome do evento ('entrar_fila') ou o tipo do pacote ('pong', 'connect') -
    nunca os argumentos. Nome de evento fora de [A-Za-z0-9_] vira um rótulo
    fixo (o texto vem do cliente)."""
    achado = _EVENTO.match(pacote)
    if achado:
        nome = achado.group(1).decode("utf-8", "replace")
        return nome if _NOME_EVENTO_VALIDO.fullmatch(nome) else "<evento com nome inválido>"
    texto = pacote[:2].decode("ascii", "replace")
    if texto[:1] == "4" and len(texto) > 1:
        return "socketio:" + _TIPOS_SOCKETIO.get(texto[1], "?")
    return "engineio:" + _TIPOS_ENGINEIO.get(texto[:1], "?")


class DiagnosticoPostSocketIO:
    def __init__(self, wsgi_app, logger, caminho="/socket.io/", max_bytes=MAX_BYTES_PADRAO, limite=LIMITE_PACOTES_LOG):
        self.wsgi_app = wsgi_app
        self.logger = logger
        self.caminho = caminho
        self.max_bytes = max_bytes
        self.limite = limite

    def __call__(self, environ, start_response):
        if environ.get("REQUEST_METHOD") == "POST" and environ.get("PATH_INFO", "").startswith(self.caminho):
            self._inspecionar(environ)
        return self.wsgi_app(environ, start_response)

    def _inspecionar(self, environ):
        try:
            tamanho = int(environ.get("CONTENT_LENGTH") or 0)
        except (TypeError, ValueError):
            return
        if tamanho <= 0 or tamanho > self.max_bytes:
            return
        try:
            corpo = environ["wsgi.input"].read(tamanho)
        except Exception:  # leitura falhou: o engineio também falharia - segue com corpo vazio
            corpo = b""
        environ["wsgi.input"] = io.BytesIO(corpo)
        environ["CONTENT_LENGTH"] = str(len(corpo))
        if not corpo:
            return
        pacotes = corpo.split(SEPARADOR)
        if len(pacotes) <= self.limite:
            return
        eventos = {}
        for pacote in pacotes:
            rotulo = rotulo_pacote(pacote)
            eventos[rotulo] = eventos.get(rotulo, 0) + 1
        sid = parse_qs(environ.get("QUERY_STRING", "")).get("sid", ["?"])[0]
        tela = urlsplit(environ.get("HTTP_REFERER", "")).path or "?"
        self.logger.warning(
            "Socket.IO: POST com %d pacotes (acima de %d) sid=%s tela=%s eventos=%s",
            len(pacotes), self.limite, sid, tela,
            ", ".join(f"{nome}: {qtd}" for nome, qtd in sorted(eventos.items())),
        )
