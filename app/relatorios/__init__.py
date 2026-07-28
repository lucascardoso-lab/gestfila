from flask import Blueprint

from app.utils.decorators import roles_required

relatorios_bp = Blueprint("relatorios", __name__, url_prefix="/relatorios")

PERFIS_RELATORIOS = ("gerencial", "admin")


@relatorios_bp.before_request
@roles_required(*PERFIS_RELATORIOS)
def proteger_relatorios():
    """Único ponto de RBAC desta área: só 'gerencial' e 'admin' têm acesso a
    relatórios (seção 3 do BLUEPRINT - recepção/coleta nunca acessam relatórios)."""
    return None


from . import routes  # noqa: E402  (registra rotas no relatorios_bp)

__all__ = ["relatorios_bp"]
