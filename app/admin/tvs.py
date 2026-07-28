from flask import flash, redirect, render_template, request, url_for

from app.admin import admin_bp
from app.admin.helpers import (
    autorizar_objeto,
    filas_da_empresa,
    resolver_empresa_contexto,
    resolver_unidade_contexto,
    unidades_da_empresa,
)
from app.extensions import db
from app.models import Empresa, Fila, Tv
from app.utils.slug import gerar_slug_unico


@admin_bp.route("/tvs")
def listar_tvs():
    empresa = resolver_empresa_contexto()
    if empresa is None:
        return render_template(
            "admin/selecionar_empresa.html",
            empresas=Empresa.query.order_by(Empresa.nome).all(),
            destino="admin.listar_tvs",
        )

    unidade = resolver_unidade_contexto(empresa)
    query = Tv.query.filter_by(empresa_id=empresa.id)
    if unidade is not None:
        query = query.filter_by(unidade_id=unidade.id)
    tvs = query.order_by(Tv.nome).all()
    return render_template("admin/tvs/listar.html", empresa=empresa, unidade=unidade, tvs=tvs)


@admin_bp.route("/tvs/nova", methods=["GET", "POST"])
def nova_tv():
    empresa = resolver_empresa_contexto()
    if empresa is None:
        flash("Selecione uma empresa antes de cadastrar uma TV.", "error")
        return redirect(url_for("admin.listar_tvs"))

    unidades = unidades_da_empresa(empresa)
    if not unidades:
        flash("Cadastre ao menos uma unidade antes de criar uma TV.", "error")
        return redirect(url_for("admin.listar_tvs", empresa_id=empresa.id))

    filas = filas_da_empresa(empresa)

    if request.method == "POST":
        dados = _ler_formulario()
        erro = _validar_tv(dados, unidades)
        if erro:
            flash(erro, "error")
            return render_template(
                "admin/tvs/form.html", empresa=empresa, tv=None, unidades=unidades, filas=filas,
                valores=dados, filas_selecionadas=dados["fila_ids"],
            )

        # slug fixado na criação (URL pública /painel/tv/<slug>) - nunca recalculado
        # ao renomear a TV depois, ver app/utils/slug.py.
        slug = gerar_slug_unico(dados["nome"], lambda s: Tv.query.filter_by(slug=s).first() is not None)

        tv = Tv(
            empresa_id=empresa.id,
            unidade_id=dados["unidade_id"],
            nome=dados["nome"],
            layout=dados["layout"],
            som_ativo=dados["som_ativo"],
            volume=dados["volume"],
            slug=slug,
        )
        # nunca confiar no id de fila vindo direto do formulário - filtra contra a lista pré-carregada,
        # já restrita à empresa
        tv.filas = [f for f in filas if f.id in dados["fila_ids"]]
        db.session.add(tv)
        db.session.commit()
        flash(f"TV '{tv.nome}' criada.", "info")
        return redirect(url_for("admin.listar_tvs", empresa_id=empresa.id))

    return render_template(
        "admin/tvs/form.html", empresa=empresa, tv=None, unidades=unidades, filas=filas,
        valores=None, filas_selecionadas=set(),
    )


@admin_bp.route("/tvs/<int:tv_id>/editar", methods=["GET", "POST"])
def editar_tv(tv_id):
    tv = Tv.query.get_or_404(tv_id)
    autorizar_objeto(tv)
    empresa = Empresa.query.get_or_404(tv.empresa_id)
    unidades = unidades_da_empresa(empresa)
    filas = filas_da_empresa(empresa)

    if request.method == "POST":
        dados = _ler_formulario()
        erro = _validar_tv(dados, unidades)
        if erro:
            flash(erro, "error")
            return render_template(
                "admin/tvs/form.html", empresa=empresa, tv=tv, unidades=unidades, filas=filas,
                valores=dados, filas_selecionadas=dados["fila_ids"],
            )

        tv.unidade_id = dados["unidade_id"]
        tv.nome = dados["nome"]
        tv.layout = dados["layout"]
        tv.som_ativo = dados["som_ativo"]
        tv.volume = dados["volume"]
        tv.filas = [f for f in filas if f.id in dados["fila_ids"]]
        db.session.commit()
        flash("TV atualizada.", "info")
        return redirect(url_for("admin.listar_tvs", empresa_id=empresa.id))

    filas_selecionadas = {f.id for f in tv.filas}
    return render_template(
        "admin/tvs/form.html", empresa=empresa, tv=tv, unidades=unidades, filas=filas,
        valores=None, filas_selecionadas=filas_selecionadas,
    )


@admin_bp.route("/tvs/<int:tv_id>/toggle-ativo", methods=["POST"])
def alternar_tv(tv_id):
    tv = Tv.query.get_or_404(tv_id)
    autorizar_objeto(tv)

    tv.ativo = not tv.ativo
    db.session.commit()
    flash(f"TV {'ativada' if tv.ativo else 'desativada'}.", "info")
    return redirect(url_for("admin.listar_tvs", empresa_id=tv.empresa_id))


@admin_bp.route("/tvs/<int:tv_id>/excluir", methods=["POST"])
def excluir_tv(tv_id):
    """Exclusão segura (ver mesmo padrão em admin/filas.py::excluir_fila) - só apaga
    quando nenhuma fila usa esta TV como atalho de cadastro rápido."""
    tv = Tv.query.get_or_404(tv_id)
    autorizar_objeto(tv)

    if Fila.query.filter_by(tv_id=tv.id).first():
        flash(
            f"Não é possível excluir '{tv.nome}': existem filas com esta TV configurada como atalho de "
            "cadastro. Desative a TV em vez de excluir.", "error",
        )
        return redirect(url_for("admin.listar_tvs", empresa_id=tv.empresa_id))

    empresa_id, nome = tv.empresa_id, tv.nome
    tv.filas = []  # vínculo de configuração (tv_filas), não histórico - limpo antes de excluir
    db.session.delete(tv)
    db.session.commit()
    flash(f"TV '{nome}' excluída.", "info")
    return redirect(url_for("admin.listar_tvs", empresa_id=empresa_id))


def _ler_formulario():
    return {
        "nome": request.form.get("nome", "").strip(),
        "unidade_id": request.form.get("unidade_id", type=int),
        "layout": request.form.get("layout", "").strip() or None,
        "fila_ids": {int(v) for v in request.form.getlist("filas") if v.isdigit()},
        "som_ativo": request.form.get("som_ativo") == "on",
        "volume": request.form.get("volume", type=int),
    }


def _validar_tv(dados, unidades):
    if not dados["nome"]:
        return "Informe o nome da TV."

    if dados["unidade_id"] not in {u.id for u in unidades}:
        return "Selecione uma unidade válida."

    if dados["volume"] is None or not (0 <= dados["volume"] <= 100):
        return "Volume deve ser um número entre 0 e 100."

    return None
