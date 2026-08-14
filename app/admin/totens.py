from flask import flash, redirect, render_template, request, url_for

from app.admin import admin_bp
from app.admin.helpers import autorizar_objeto, resolver_empresa_contexto, resolver_unidade_contexto, unidades_da_empresa
from app.extensions import db
from app.models import Empresa, Fila, Totem
from app.utils.slug import gerar_slug_unico


@admin_bp.route("/totens")
def listar_totens():
    empresa = resolver_empresa_contexto()
    if empresa is None:
        return render_template(
            "admin/selecionar_empresa.html",
            empresas=Empresa.query.order_by(Empresa.nome).all(),
            destino="admin.listar_totens",
        )

    unidade = resolver_unidade_contexto(empresa)
    query = Totem.query.filter_by(empresa_id=empresa.id)
    if unidade is not None:
        query = query.filter_by(unidade_id=unidade.id)
    totens = query.order_by(Totem.nome).all()
    return render_template("admin/totens/listar.html", empresa=empresa, unidade=unidade, totens=totens)


@admin_bp.route("/totens/novo", methods=["GET", "POST"])
def novo_totem():
    empresa = resolver_empresa_contexto()
    if empresa is None:
        flash("Selecione uma empresa antes de cadastrar um totem.", "error")
        return redirect(url_for("admin.listar_totens"))

    unidades = unidades_da_empresa(empresa)
    if not unidades:
        flash("Cadastre ao menos uma unidade antes de criar um totem.", "error")
        return redirect(url_for("admin.listar_totens", empresa_id=empresa.id))

    if request.method == "POST":
        dados = _ler_formulario()
        erro = _validar_totem(dados, unidades)
        if erro:
            flash(erro, "error")
            return render_template(
                "admin/totens/form.html", empresa=empresa, totem=None, unidades=unidades, valores=dados
            )

        # slug fixado na criação (URL pública /totem/<slug>) - nunca recalculado ao
        # renomear o totem depois, ver app/utils/slug.py.
        slug = gerar_slug_unico(dados["nome"], lambda s: Totem.query.filter_by(slug=s).first() is not None)

        totem = Totem(
            empresa_id=empresa.id, unidade_id=dados["unidade_id"], nome=dados["nome"],
            nome_impressora_windows=dados["nome_impressora_windows"],
            slug=slug,
        )
        db.session.add(totem)
        db.session.commit()
        flash(f"Totem '{totem.nome}' criado.", "info")
        return redirect(url_for("admin.listar_totens", empresa_id=empresa.id))

    return render_template(
        "admin/totens/form.html", empresa=empresa, totem=None, unidades=unidades, valores=None
    )


@admin_bp.route("/totens/<int:totem_id>/editar", methods=["GET", "POST"])
def editar_totem(totem_id):
    totem = Totem.query.get_or_404(totem_id)
    autorizar_objeto(totem)
    empresa = Empresa.query.get_or_404(totem.empresa_id)
    unidades = unidades_da_empresa(empresa)

    if request.method == "POST":
        dados = _ler_formulario()
        erro = _validar_totem(dados, unidades)
        if erro:
            flash(erro, "error")
            return render_template(
                "admin/totens/form.html", empresa=empresa, totem=totem, unidades=unidades, valores=dados,
                esconder_seletor_contexto=True,
            )

        totem.unidade_id = dados["unidade_id"]
        totem.nome = dados["nome"]
        totem.nome_impressora_windows = dados["nome_impressora_windows"]
        db.session.commit()
        flash("Totem atualizado.", "info")
        return redirect(url_for("admin.listar_totens", empresa_id=empresa.id))

    return render_template(
        "admin/totens/form.html", empresa=empresa, totem=totem, unidades=unidades, valores=None,
        esconder_seletor_contexto=True,
    )


@admin_bp.route("/totens/<int:totem_id>/toggle-ativo", methods=["POST"])
def alternar_totem(totem_id):
    totem = Totem.query.get_or_404(totem_id)
    autorizar_objeto(totem)

    totem.ativo = not totem.ativo
    db.session.commit()
    flash(f"Totem {'ativado' if totem.ativo else 'desativado'}.", "info")
    return redirect(url_for("admin.listar_totens", empresa_id=totem.empresa_id))


@admin_bp.route("/totens/<int:totem_id>/excluir", methods=["POST"])
def excluir_totem(totem_id):
    """Exclusão segura (ver mesmo padrão em admin/filas.py::excluir_fila) - só apaga
    quando não há nenhuma fila vinculada a este totem."""
    totem = Totem.query.get_or_404(totem_id)
    autorizar_objeto(totem)

    if Fila.query.filter_by(totem_id=totem.id).first():
        flash(
            f"Não é possível excluir '{totem.nome}': existem filas vinculadas a este totem. "
            "Desative o totem em vez de excluir.", "error",
        )
        return redirect(url_for("admin.listar_totens", empresa_id=totem.empresa_id))

    empresa_id, nome = totem.empresa_id, totem.nome
    db.session.delete(totem)
    db.session.commit()
    flash(f"Totem '{nome}' excluído.", "info")
    return redirect(url_for("admin.listar_totens", empresa_id=empresa_id))


def _ler_formulario():
    return {
        "nome": request.form.get("nome", "").strip(),
        "unidade_id": request.form.get("unidade_id", type=int),
        "nome_impressora_windows": request.form.get("nome_impressora_windows", "").strip() or None,
    }


def _validar_totem(dados, unidades):
    if not dados["nome"]:
        return "Informe o nome do totem."

    if dados["unidade_id"] not in {u.id for u in unidades}:
        return "Selecione uma unidade válida."

    return None
