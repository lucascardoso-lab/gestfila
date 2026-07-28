from flask import Blueprint

totem_bp = Blueprint("totem", __name__, url_prefix="/totem")

from . import routes  # noqa: E402  (registra rotas no totem_bp)

__all__ = ["totem_bp"]
