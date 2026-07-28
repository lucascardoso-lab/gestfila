from flask import g
from flask_login import current_user


def register_tenant_middleware(app):
    """Injeta o contexto multi-tenant (empresa_id, unidades) em `g` a cada request.

    Toda query de dados deve filtrar por `g.empresa_id` (exceto para super admin) -
    nunca confiar em filtro feito só no front, conforme seção 8 do BLUEPRINT.
    """

    @app.before_request
    def carregar_contexto_tenant():
        if current_user.is_authenticated:
            g.empresa_id = current_user.empresa_id
            g.is_super_admin = current_user.is_super_admin

            if g.is_super_admin:
                g.unidades_permitidas = None  # sentinela: acesso irrestrito
            elif current_user.tem_perfil("admin"):
                from app.models import Unidade

                g.unidades_permitidas = [
                    u.id for u in Unidade.query.filter_by(empresa_id=current_user.empresa_id).all()
                ]
            else:
                g.unidades_permitidas = [u.id for u in current_user.unidades]
        else:
            g.empresa_id = None
            g.is_super_admin = False
            g.unidades_permitidas = []
