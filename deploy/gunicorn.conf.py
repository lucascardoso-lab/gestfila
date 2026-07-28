"""Configuração do Gunicorn para produção (seção 10 do BLUEPRINT).

Uso: gunicorn -c deploy/gunicorn.conf.py wsgi:app
(equivalente a "gunicorn -k eventlet -w 1 --bind 127.0.0.1:8000 wsgi:app", mas
centralizado num arquivo versionado em vez de repetir flags na linha de comando).
"""

bind = "127.0.0.1:8000"  # atrás do proxy reverso (Apache2/Nginx) - nunca exposto direto à internet

worker_class = "eventlet"

# 1 único worker: Flask-SocketIO mantém em memória, por processo, a lista de
# clientes conectados e as salas (tv_<id>/fila_<id>). Com mais de um worker, um
# cliente conectado ao worker A nunca receberia eventos emitidos a partir do
# worker B - seria necessário um message_queue compartilhado (Redis) entre os
# workers do Flask-SocketIO, o que não está configurado nesta fase. Se o volume de
# TVs/filas exigir escalar horizontalmente, configurar Redis como message_queue
# (https://flask-socketio.readthedocs.io/en/latest/deployment.html#using-multiple-workers)
# antes de aumentar este número.
workers = 1

# eventlet já sustenta muitas conexões WebSocket concorrentes num único worker
# (cada conexão é uma greenlet, não uma thread/processo do SO) - não há motivo
# para usar `threads` aqui.

accesslog = "-"  # stdout (systemd/journald captura)
errorlog = "-"
loglevel = "info"

# Tempo generoso por estar atrás de um proxy reverso com keep-alive para WebSocket;
# ajuste para baixo se notar workers travados por requisições penduradas.
timeout = 60
graceful_timeout = 30
keepalive = 5
