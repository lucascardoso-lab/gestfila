from flask import Blueprint, abort, redirect, request, url_for
from flask_login import current_user

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


@admin_bp.before_request
def proteger_admin():
    """Único ponto de controle de acesso ao painel administrativo.

    Libera super admin e usuários com perfil 'admin' da própria empresa;
    qualquer outro perfil recebe 403.
    """
    if not current_user.is_authenticated:
        return redirect(url_for("auth.login", next=request.path))
    if not (current_user.is_super_admin or current_user.tem_perfil("admin")):
        abort(403)


@admin_bp.route("/")
def painel():
    if current_user.is_super_admin:
        return redirect(url_for("admin.listar_empresas"))
    return redirect(url_for("admin.listar_unidades"))


@admin_bp.app_template_global()
def unidades_do_contexto(empresa):
    """Usada pela barra de contexto e pelo breadcrumb (`admin/_layout.html`) - lista
    de unidades pra popular o select de Unidade, sempre a partir da MESMA `empresa`
    já em uso na página (a que a rota passou pro template, seja via
    resolver_empresa_contexto() numa listagem ou via objeto.empresa numa tela de
    edição) - nunca de um valor de sessão à parte, o que poderia divergir do que a
    página está realmente mostrando."""
    from app.models import Unidade

    return Unidade.query.filter_by(empresa_id=empresa.id).order_by(Unidade.nome).all()


@admin_bp.app_template_global()
def empresas_para_contexto():
    """Lista de empresas pro select de Empresa da barra de contexto - só renderizado
    pra super admin (app/templates/admin/_layout.html)."""
    from app.models import Empresa

    return Empresa.query.order_by(Empresa.nome).all()


from . import empresas, filas, locais, perfis, servicos, tipos_senha, totem_botoes, totens, tvs, unidades, usuarios  # noqa: E402  (registram rotas no admin_bp)

__all__ = ["admin_bp"]
