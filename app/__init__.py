import os

from flask import Flask, session
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import config_by_name
from .extensions import csrf, db, limiter, login_manager, migrate, socketio


def create_app(config_name=None):
    config_name = config_name or os.environ.get("FLASK_ENV", "development")

    app = Flask(__name__)
    app.config.from_object(config_by_name[config_name])

    if app.config.get("TRUST_PROXY_HEADERS"):
        # Sem isto, com o Apache2/Nginx do deploy (seção 10) na frente do Gunicorn,
        # request.remote_addr seria sempre o IP loopback do proxy - o rate limit por
        # IP do Flask-Limiter (item 1 da Fase 10) se tornaria inútil, pois todo
        # cliente cairia no mesmo "IP" para fins de limite. x_for=1/x_proto=1 porque
        # há exatamente 1 proxy reverso confiável entre o cliente e o Gunicorn.
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

    db.init_app(app)
    login_manager.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    limiter.init_app(app)
    socketio.init_app(app)

    login_manager.login_view = "auth.login"
    login_manager.login_message = "Faça login para continuar."
    login_manager.login_message_category = "info"

    from .models import Usuario

    @login_manager.user_loader
    def load_user(user_id):
        """Retornar None aqui faz o Flask-Login tratar a requisição como não
        autenticada (current_user vira AnonymousUserMixin, @login_required
        redireciona pro login) - é o mecanismo usado pra forçar logout remoto
        (ver Usuario.sessao_versao): se o número gravado no cookie de sessão não
        bater mais com o do banco (foi incrementado por
        admin/locais.py::desconectar_usuario_local), a sessão é considerada
        inválida mesmo com o cookie de login ainda tecnicamente assinado/válido."""
        usuario = db.session.get(Usuario, int(user_id))
        if usuario is None:
            return None
        if session.get("sessao_versao", 0) != usuario.sessao_versao:
            return None
        return usuario

    from .admin import admin_bp
    from .atendimento import atendimento_bp
    from .auth import auth_bp
    from .core import core_bp
    from .painel import painel_bp
    from .relatorios import relatorios_bp
    from .totem import totem_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(core_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(atendimento_bp)
    app.register_blueprint(relatorios_bp)
    app.register_blueprint(totem_bp)
    app.register_blueprint(painel_bp)

    from .middleware.tenant import register_tenant_middleware

    register_tenant_middleware(app)

    from .middleware.encerramento import register_encerramento_middleware

    register_encerramento_middleware(app)

    from .middleware.forcar_troca_senha import register_forcar_troca_senha_middleware

    register_forcar_troca_senha_middleware(app)

    from .errors import register_error_handlers

    register_error_handlers(app)

    from . import sockets  # noqa: F401  (importar registra os handlers no namespace /painel)

    return app
