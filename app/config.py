import os
from dotenv import load_dotenv
load_dotenv()
from datetime import timedelta


def _montar_database_uri():
    url_completa = os.environ.get("DATABASE_URL")
    if url_completa:
        return url_completa

    usuario = os.environ.get("DB_USER", "root")
    senha = os.environ.get("DB_PASSWORD", "")
    host = os.environ.get("DB_HOST", "localhost")
    porta = os.environ.get("DB_PORT", "3306")
    nome_banco = os.environ.get("DB_NAME", "painel_senha")
    return f"mysql+pymysql://{usuario}:{senha}@{host}:{porta}/{nome_banco}"


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")
    SQLALCHEMY_DATABASE_URI = _montar_database_uri()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True, "pool_recycle": 280}

    REMEMBER_COOKIE_DURATION = timedelta(days=14)
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"

    # As telas de Atendimento (painel_fila/painel_local) ficam abertas por um turno
    # inteiro sem reload - o default do Flask-WTF (1h) expirava o token CSRF renderizado
    # no carregamento da página e qualquer clique em Chamar/Tocar/Realocar/Finalizar/
    # Aguardar caía num "Bad Request" cru. Sem limite de tempo, o token só deixa de
    # valer se a sessão de login expirar/fizer logout (SECRET_KEY + sessão já protegem
    # contra token roubado de outra sessão).
    WTF_CSRF_TIME_LIMIT = None

    # Fora de produção não há proxy reverso na frente - confiar em X-Forwarded-*
    # sem ele significaria aceitar esses cabeçalhos vindos direto do cliente, que
    # poderia forjá-los para burlar o rate limit por IP (Flask-Limiter).
    TRUST_PROXY_HEADERS = False

    # Encerramento automático das sessões de atendimento (seção 10.18) - formato
    # HH:MM, string mesmo (não datetime.time): comparado a cada request tanto no
    # servidor (app/middleware/encerramento.py) quanto lido cru pelo JS via Jinja
    # (atendimento/_layout.html, que faz o próprio split(":")).
    HORARIO_ENCERRAMENTO_SESSOES = os.environ.get("HORARIO_ENCERRAMENTO_SESSOES", "23:00")
    AVISO_ENCERRAMENTO_MINUTOS = int(os.environ.get("AVISO_ENCERRAMENTO_MINUTOS", "5"))


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False
    SESSION_COOKIE_SECURE = True
    # Seção 10 do BLUEPRINT: Apache2/Nginx sempre na frente do Gunicorn em produção -
    # confiar em X-Forwarded-For/Proto (1 hop) para Flask-Limiter ver o IP real do
    # cliente, não o IP loopback do proxy (ver app/__init__.py: ProxyFix).
    TRUST_PROXY_HEADERS = True


class TestingConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    RATELIMIT_ENABLED = False
    SQLALCHEMY_DATABASE_URI = os.environ.get("TEST_DATABASE_URL", "sqlite:///:memory:")


config_by_name = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
}
