from flask import flash, redirect, render_template, request, url_for

from app.admin import admin_bp
from app.admin.helpers import autorizar_objeto, resolver_empresa_contexto
from app.extensions import db
from app.models import Empresa, Unidade


@admin_bp.route("/unidades")
def listar_unidades():
    empresa = resolver_empresa_contexto()
    if empresa is None:
        return render_template(
            "admin/selecionar_empresa.html",
            empresas=Empresa.query.order_by(Empresa.nome).all(),
            destino="admin.listar_unidades",
        )

    unidades = Unidade.query.filter_by(empresa_id=empresa.id).order_by(Unidade.nome).all()
    return render_template("admin/unidades/listar.html", empresa=empresa, unidades=unidades)


@admin_bp.route("/unidades/nova", methods=["GET", "POST"])
def nova_unidade():
    empresa = resolver_empresa_contexto()
    if empresa is None:
        flash("Selecione uma empresa antes de cadastrar uma unidade.", "error")
        return redirect(url_for("admin.listar_unidades"))

    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        if not nome:
            flash("Informe o nome da unidade.", "error")
            return render_template("admin/unidades/form.html", empresa=empresa, unidade=None)

        unidade = Unidade(
            empresa_id=empresa.id,
            nome=nome,
            endereco=request.form.get("endereco", "").strip() or None,
            telefone=request.form.get("telefone", "").strip() or None,
        )
        db.session.add(unidade)
        db.session.commit()
        flash(f"Unidade '{unidade.nome}' criada.", "info")
        return redirect(url_for("admin.listar_unidades", empresa_id=empresa.id))

    return render_template("admin/unidades/form.html", empresa=empresa, unidade=None)


@admin_bp.route("/unidades/<int:unidade_id>/editar", methods=["GET", "POST"])
def editar_unidade(unidade_id):
    unidade = Unidade.query.get_or_404(unidade_id)
    autorizar_objeto(unidade)
    empresa = unidade.empresa

    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        if not nome:
            flash("Informe o nome da unidade.", "error")
            return render_template(
                "admin/unidades/form.html", empresa=empresa, unidade=unidade, esconder_seletor_contexto=True,
            )

        unidade.nome = nome
        unidade.endereco = request.form.get("endereco", "").strip() or None
        unidade.telefone = request.form.get("telefone", "").strip() or None
        db.session.commit()
        flash("Unidade atualizada.", "info")
        return redirect(url_for("admin.listar_unidades", empresa_id=empresa.id))

    return render_template(
        "admin/unidades/form.html", empresa=empresa, unidade=unidade, esconder_seletor_contexto=True,
    )


@admin_bp.route("/unidades/<int:unidade_id>/toggle-ativo", methods=["POST"])
def alternar_unidade(unidade_id):
    unidade = Unidade.query.get_or_404(unidade_id)
    autorizar_objeto(unidade)

    unidade.ativo = not unidade.ativo
    db.session.commit()
    flash(f"Unidade {'ativada' if unidade.ativo else 'desativada'}.", "info")
    return redirect(url_for("admin.listar_unidades", empresa_id=unidade.empresa_id))
