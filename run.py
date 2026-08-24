import sys

if sys.platform != "win32":
    import eventlet
    eventlet.monkey_patch()

from app import create_app  # noqa: E402  (import após o monkey_patch, de propósito)
from app.extensions import socketio  # noqa: E402

app = create_app()

if __name__ == "__main__":
    # socketio.run (não app.run) é necessário aqui porque o servidor de desenvolvimento
    # padrão do Flask/Werkzeug não sustenta upgrade de conexão para WebSocket - com
    # async_mode="eventlet", isto sobe um servidor WSGI eventlet que sustenta.
    #
    # Em produção (seção 10 do BLUEPRINT) isto não é usado: o Gunicorn com worker
    # eventlet serve `wsgi:app` diretamente (`gunicorn -k eventlet -w 1 wsgi:app`), e o
    # Apache2/Nginx na frente faz proxy reverso com suporte a WebSocket
    # (mod_proxy_wstunnel ou Nginx) - Apache2 puro com mod_wsgi não sustenta WebSocket.
    #
    # No Windows (dev local), o monkey_patch do eventlet acima é pulado: o eventlet não
    # torna subprocess.run() cooperativo direito nesse SO, e a geração de PDF (adendo
    # 10.67, que roda o Playwright em subprocess isolado) travava o servidor inteiro -
    # achado real, reproduzido e investigado, não hipótese.
    socketio.run(app, host="0.0.0.0", port=5000, debug=app.config.get("DEBUG", False))
