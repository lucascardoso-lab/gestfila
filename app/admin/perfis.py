from flask import render_template

from app.admin import admin_bp
from app.models import Perfil
from app.utils.decorators import super_admin_required


@admin_bp.route("/perfis")
@super_admin_required
def listar_perfis():
    perfis = Perfil.query.order_by(Perfil.nome).all()
    return render_template("admin/perfis/listar.html", perfis=perfis)
