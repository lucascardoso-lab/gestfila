import sys

from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_migrate import Migrate
from flask_socketio import SocketIO
from flask_wtf import CSRFProtect

db = SQLAlchemy()
login_manager = LoginManager()
migrate = Migrate()
csrf = CSRFProtect()
limiter = Limiter(key_func=get_remote_address)

# async_mode "eventlet" em produção/Linux, casando com o worker do Gunicorn (seção 10
# do BLUEPRINT: "Gunicorn + eventlet worker"). No Windows (dev local), o eventlet NÃO
# torna subprocess.run() cooperativo direito - a geração de PDF (adendo 10.67, que roda
# o Playwright em subprocess isolado) trava o hub inteiro do servidor local nesse SO
# (achado real, não hipotético - reproduzido e investigado). "threading" usa threads
# reais do SO, que não sofrem dessa limitação. O monkey-patch do eventlet continua
# sendo feito só no entrypoint real do servidor (run.py), nunca aqui.
_ASYNC_MODE = "eventlet" if sys.platform != "win32" else "threading"
socketio = SocketIO(async_mode=_ASYNC_MODE)
