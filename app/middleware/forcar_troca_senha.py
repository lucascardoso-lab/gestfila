from flask import redirect, request, url_for
from flask_login import current_user

# Endpoints acessíveis mesmo com Usuario.deve_trocar_senha=True - só o
# necessário pra trocar a senha e sair, nada de tela operacional/admin.
ENDPOINTS_PERMITIDOS = {"auth.trocar_senha_obrigatoria", "auth.logout", "static"}


def register_forcar_troca_senha_middleware(app):
    @app.before_request
    def _forcar_troca_senha():
        if not current_user.is_authenticated:
            return None  # rotas públicas (totem/painel de TV) nunca são afetadas
        if not current_user.deve_trocar_senha:
            return None
        if request.endpoint in ENDPOINTS_PERMITIDOS:
            return None
        return redirect(url_for("auth.trocar_senha_obrigatoria"))
