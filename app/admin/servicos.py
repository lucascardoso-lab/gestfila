from flask import redirect, request, url_for

from app.admin import admin_bp


@admin_bp.route("/servicos")
def listar_servicos():
    """Serviço foi fundido em Fila (seção 10.1 do BLUEPRINT) - mantém esta rota só
    para não quebrar links/favoritos antigos, redirecionando para a tela de Filas."""
    return redirect(url_for("admin.listar_filas", **request.args))
