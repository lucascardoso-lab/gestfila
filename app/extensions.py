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

# async_mode fixo em "eventlet" porque é o worker definido para produção (seção 10 do
# BLUEPRINT: "Gunicorn + eventlet worker"). O monkey-patch do eventlet é feito só no
# entrypoint real do servidor (run.py), nunca aqui, para não afetar testes/ferramentas
# que apenas importam o pacote `app`.
socketio = SocketIO(async_mode="eventlet")
