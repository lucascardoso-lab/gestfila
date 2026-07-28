from flask import Blueprint

painel_bp = Blueprint("painel", __name__, url_prefix="/painel")

from . import routes  # noqa: E402  (registra rotas no painel_bp)

__all__ = ["painel_bp"]
