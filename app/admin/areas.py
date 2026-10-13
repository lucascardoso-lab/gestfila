"""Cadastro de Áreas (adendo 10.76) - agrupamento de filas de UMA unidade,
usado só no admin e nos relatórios. RBAC igual ao de Filas: `before_request`
do admin_bp (admin da empresa ou super admin) + `autorizar_objeto` (404 para
outra empresa)."""
from flask import flash, redirect, render_template, request, url_for
from sqlalchemy.exc import IntegrityError

from app.admin import admin_bp
from app.admin.helpers import (
    autorizar_objeto,
    resolver_empresa_contexto,
    resolver_unidade_contexto,
    unidades_da_empresa,
)
from app.extensions import db
from app.models import AreaFila, Empresa, Fila, LogExportacaoRelatorio
from app.utils.rotulos import chave_colisao, rotulos_areas


@admin_bp.route("/areas")
def listar_areas():
    empresa = resolver_empresa_contexto()
    if empresa is None:
        return render_template(
            "admin/selecionar_empresa.html",
            empresas=Empresa.query.order_by(Empresa.nome).all(),
            destino="admin.listar_areas",
        )

    unidade = resolver_unidade_contexto(empresa)
    areas = AreaFila.query.filter_by(empresa_id=empresa.id)
    filas_sem_area = Fila.query.filter_by(empresa_id=empresa.id, area_fila_id=None)
    if unidade is not None:
        areas = areas.filter_by(unidade_id=unidade.id)
        filas_sem_area = filas_sem_area.filter_by(unidade_id=unidade.id)
    return render_template(
        "admin/areas/listar.html", empresa=empresa, unidade=unidade,
        areas=sorted(areas.all(), key=lambda a: (a.unidade.nome, a.nome)),
        rotulos=rotulos_areas(empresa.id),
        # "Filas sem área" (inclusive inativas): nunca somem - aparecem aqui e
        # como "Sem área" nos agrupamentos dos relatórios.
        filas_sem_area=sorted(filas_sem_area.all(), key=lambda f: (f.unidade.nome, f.nome)),
    )


@admin_bp.route("/areas/nova", methods=["GET", "POST"])
def nova_area():
    empresa = resolver_empresa_contexto()
    if empresa is None:
        flash("Selecione uma empresa antes de cadastrar uma área.", "error")
        return redirect(url_for("admin.listar_areas"))
    return _formulario(empresa, area=None)


@admin_bp.route("/areas/<int:area_id>/editar", methods=["GET", "POST"])
def editar_area(area_id):
    area = AreaFila.query.get_or_404(area_id)
    autorizar_objeto(area)
    return _formulario(area.empresa, area=area)


@admin_bp.route("/areas/<int:area_id>/toggle-ativo", methods=["POST"])
def alternar_area(area_id):
    area = AreaFila.query.get_or_404(area_id)
    autorizar_objeto(area)
    area.ativo = not area.ativo
    db.session.commit()
    flash(
        f"Área '{area.nome}' {'ativada' if area.ativo else 'desativada'}."
        + ("" if area.ativo else " As filas continuam vinculadas a ela nos relatórios."),
        "info",
    )
    return redirect(url_for("admin.listar_areas", empresa_id=area.empresa_id))


@admin_bp.route("/areas/<int:area_id>/excluir", methods=["POST"])
def excluir_area(area_id):
    """Exclusão segura (padrão do 10.14): bloqueada enquanto houver fila
    vinculada (ativa ou inativa) ou exportação registrada com o filtro desta
    área (log de auditoria do 10.67 - não se apaga histórico). Desativar é
    sempre possível."""
    area = AreaFila.query.get_or_404(area_id)
    autorizar_objeto(area)

    bloqueios = []
    if Fila.query.filter_by(area_fila_id=area.id).first():
        bloqueios.append("filas vinculadas a ela (desvincule-as na edição da área)")
    if LogExportacaoRelatorio.query.filter_by(area_fila_id=area.id).first():
        bloqueios.append("exportações de relatório registradas com o filtro desta área (auditoria)")
    if bloqueios:
        flash(
            f"Não é possível excluir a área '{area.nome}': existem {' e '.join(bloqueios)}. "
            "Desative a área em vez de excluir.", "error",
        )
        return redirect(url_for("admin.listar_areas", empresa_id=area.empresa_id))

    empresa_id, nome = area.empresa_id, area.nome
    db.session.delete(area)
    db.session.commit()
    flash(f"Área '{nome}' excluída.", "info")
    return redirect(url_for("admin.listar_areas", empresa_id=empresa_id))


def _formulario(empresa, area):
    unidades = unidades_da_empresa(empresa)
    if not unidades:
        flash("Cadastre ao menos uma unidade antes de criar uma área.", "error")
        return redirect(url_for("admin.listar_areas", empresa_id=empresa.id))
    filas = Fila.query.filter_by(empresa_id=empresa.id).all()
    filas.sort(key=lambda f: (f.unidade.nome, f.nome))

    if request.method == "POST":
        dados = {
            "nome": request.form.get("nome", "").strip(),
            "unidade_id": request.form.get("unidade_id", type=int),
            "ativo": request.form.get("ativo") == "on",
            "fila_ids": {int(v) for v in request.form.getlist("filas") if v.isdigit()},
        }
        erro = _validar(dados, empresa, unidades, filas, area)
        if erro:
            flash(erro, "error")
            return _render_form(empresa, area, unidades, filas, dados)

        area_original = area
        try:
            if area is None:
                area = AreaFila(empresa_id=empresa.id)
                db.session.add(area)
            area.nome, area.unidade_id, area.ativo = dados["nome"], dados["unidade_id"], dados["ativo"]
            db.session.flush()

            movidas = []
            for fila in filas:
                if fila.id in dados["fila_ids"]:
                    if fila.area_fila_id != area.id:
                        if fila.area_fila is not None:
                            movidas.append(f"'{fila.nome}' movida de '{fila.area_fila.nome}' para '{area.nome}'")
                        fila.area_fila = area
                elif fila.area_fila_id == area.id:
                    fila.area_fila = None  # desmarcada: passa a "Sem área"
            db.session.commit()
        except IntegrityError:
            # Corrida com outro admin criando o mesmo nome (a validação acima
            # já cobre o caso normal).
            db.session.rollback()
            flash(f"Já existe uma área chamada '{dados['nome']}' nesta unidade.", "error")
            return _render_form(empresa, area_original, unidades, filas, dados)

        flash(f"Área '{area.nome}' salva.", "info")
        for resumo in movidas:
            flash(resumo, "info")
        return redirect(url_for("admin.listar_areas", empresa_id=empresa.id))

    return _render_form(empresa, area, unidades, filas, None)


def _render_form(empresa, area, unidades, filas, valores):
    return render_template(
        "admin/areas/form.html", empresa=empresa, area=area, unidades=unidades, filas=filas,
        valores=valores, esconder_seletor_contexto=area is not None,
        filas_marcadas=(valores["fila_ids"] if valores else {f.id for f in filas if area and f.area_fila_id == area.id}),
    )


def _validar(dados, empresa, unidades, filas, area_atual):
    if not dados["nome"]:
        return "Informe o nome da área."
    if len(dados["nome"]) > 100:
        return "O nome da área pode ter até 100 caracteres."
    if dados["unidade_id"] not in {u.id for u in unidades}:
        return "Selecione uma unidade válida."

    # Nome único por unidade, ignorando maiúsculas e acentos (mesma regra da
    # collation; a UniqueConstraint do banco é a rede de segurança).
    for outra in AreaFila.query.filter_by(empresa_id=empresa.id, unidade_id=dados["unidade_id"]).all():
        if (area_atual is None or outra.id != area_atual.id) and chave_colisao(outra.nome) == chave_colisao(dados["nome"]):
            return f"Já existe uma área chamada '{outra.nome}' nesta unidade."

    # Regra do 10.76: fila só entra em área da PRÓPRIA unidade (validada aqui,
    # não só escondida no formulário).
    por_id = {f.id: f for f in filas}
    for fila_id in dados["fila_ids"]:
        fila = por_id.get(fila_id)
        if fila is None:
            return "Selecione filas válidas desta empresa."
        if fila.unidade_id != dados["unidade_id"]:
            return f"A fila '{fila.nome}' é de outra unidade - uma área só agrupa filas da própria unidade."
    return None
