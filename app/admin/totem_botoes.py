import re

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
from app.models import Empresa, TotemBotao

SIGLA_RE = re.compile(r"^[A-Z]{2,4}$")


@admin_bp.route("/totem-botoes")
def listar_totem_botoes():
    empresa = resolver_empresa_contexto()
    if empresa is None:
        return render_template(
            "admin/selecionar_empresa.html",
            empresas=Empresa.query.order_by(Empresa.nome).all(),
            destino="admin.listar_totem_botoes",
        )

    unidade_contexto = resolver_unidade_contexto(empresa)
    unidades = [unidade_contexto] if unidade_contexto is not None else unidades_da_empresa(empresa)
    botoes_por_unidade = {
        unidade.id: (
            TotemBotao.query.filter_by(empresa_id=empresa.id, unidade_id=unidade.id, parent_id=None)
            .order_by(TotemBotao.ordem, TotemBotao.label)
            .all()
        )
        for unidade in unidades
    }
    return render_template(
        "admin/totem_botoes/listar.html", empresa=empresa, unidade=unidade_contexto,
        unidades=unidades, botoes_por_unidade=botoes_por_unidade,
    )


@admin_bp.route("/totem-botoes/novo", methods=["GET", "POST"])
def novo_totem_botao():
    empresa = resolver_empresa_contexto()
    if empresa is None:
        flash("Selecione uma empresa antes de cadastrar um botão.", "error")
        return redirect(url_for("admin.listar_totem_botoes"))

    unidades = unidades_da_empresa(empresa)
    if not unidades:
        flash("Cadastre ao menos uma unidade antes de criar um botão do totem.", "error")
        return redirect(url_for("admin.listar_totem_botoes", empresa_id=empresa.id))

    filas = filas_da_empresa(empresa)
    candidatos_pai = _candidatos_pai(empresa, botao_atual=None)

    if request.method == "POST":
        dados = _ler_formulario()
        erro = _validar_totem_botao(dados, unidades, filas, candidatos_pai, botao_atual=None)
        if erro:
            flash(erro, "error")
            return render_template(
                "admin/totem_botoes/form.html",
                empresa=empresa, botao=None, unidades=unidades,
                filas=filas, candidatos_pai=candidatos_pai, valores=dados,
            )

        botao = TotemBotao(
            empresa_id=empresa.id,
            unidade_id=dados["unidade_id"],
            parent_id=dados["parent_id"],
            fila_id=dados["fila_id"],
            label=dados["label"],
            sigla=dados["sigla"],
            ordem=dados["ordem"],
        )
        db.session.add(botao)
        db.session.commit()
        flash(f"Botão '{botao.label}' criado.", "info")
        return redirect(url_for("admin.listar_totem_botoes", empresa_id=empresa.id))

    return render_template(
        "admin/totem_botoes/form.html",
        empresa=empresa, botao=None, unidades=unidades,
        filas=filas, candidatos_pai=candidatos_pai, valores=None,
    )


@admin_bp.route("/totem-botoes/<int:botao_id>/editar", methods=["GET", "POST"])
def editar_totem_botao(botao_id):
    botao = TotemBotao.query.get_or_404(botao_id)
    autorizar_objeto(botao)
    empresa = Empresa.query.get_or_404(botao.empresa_id)
    unidades = unidades_da_empresa(empresa)
    filas = filas_da_empresa(empresa)
    candidatos_pai = _candidatos_pai(empresa, botao_atual=botao)

    if request.method == "POST":
        dados = _ler_formulario()
        erro = _validar_totem_botao(dados, unidades, filas, candidatos_pai, botao_atual=botao)
        if erro:
            flash(erro, "error")
            return render_template(
                "admin/totem_botoes/form.html",
                empresa=empresa, botao=botao, unidades=unidades,
                filas=filas, candidatos_pai=candidatos_pai, valores=dados,
                esconder_seletor_contexto=True,
            )

        botao.unidade_id = dados["unidade_id"]
        botao.parent_id = dados["parent_id"]
        botao.fila_id = dados["fila_id"]
        botao.label = dados["label"]
        botao.sigla = dados["sigla"]
        botao.ordem = dados["ordem"]
        db.session.commit()
        flash("Botão atualizado.", "info")
        return redirect(url_for("admin.listar_totem_botoes", empresa_id=empresa.id))

    return render_template(
        "admin/totem_botoes/form.html",
        empresa=empresa, botao=botao, unidades=unidades,
        filas=filas, candidatos_pai=candidatos_pai, valores=None,
        esconder_seletor_contexto=True,
    )


@admin_bp.route("/totem-botoes/<int:botao_id>/toggle-ativo", methods=["POST"])
def alternar_totem_botao(botao_id):
    botao = TotemBotao.query.get_or_404(botao_id)
    autorizar_objeto(botao)

    botao.ativo = not botao.ativo
    db.session.commit()
    flash(f"Botão {'ativado' if botao.ativo else 'desativado'}.", "info")
    return redirect(url_for("admin.listar_totem_botoes", empresa_id=botao.empresa_id))


@admin_bp.route("/totem-botoes/<int:botao_id>/excluir", methods=["POST"])
def excluir_totem_botao(botao_id):
    """Exclusão segura (ver mesmo padrão em admin/filas.py::excluir_fila) - só apaga
    quando o botão não tem nenhum botão filho (hierarquia, seção 5.5/10.5)."""
    botao = TotemBotao.query.get_or_404(botao_id)
    autorizar_objeto(botao)

    if botao.filhos:
        flash(
            f"Não é possível excluir '{botao.label}': existem botões filhos vinculados a ele. "
            "Exclua ou mova os filhos primeiro, ou desative este botão em vez de excluir.", "error",
        )
        return redirect(url_for("admin.listar_totem_botoes", empresa_id=botao.empresa_id))

    empresa_id, label = botao.empresa_id, botao.label
    db.session.delete(botao)
    db.session.commit()
    flash(f"Botão '{label}' excluído.", "info")
    return redirect(url_for("admin.listar_totem_botoes", empresa_id=empresa_id))


def _descendentes(botao):
    """IDs de todos os descendentes de `botao` (busca em profundidade pela relação `filhos`)."""
    ids = set()
    pilha = list(botao.filhos)
    while pilha:
        atual = pilha.pop()
        ids.add(atual.id)
        pilha.extend(atual.filhos)
    return ids


def _candidatos_pai(empresa, botao_atual):
    """Botões que podem ser escolhidos como pai - exclui o próprio botão e seus descendentes
    (evitaria um ciclo na hierarquia)."""
    candidatos = TotemBotao.query.filter_by(empresa_id=empresa.id).order_by(TotemBotao.label).all()
    if botao_atual is None:
        return candidatos
    excluidos = _descendentes(botao_atual) | {botao_atual.id}
    return [b for b in candidatos if b.id not in excluidos]


def _ler_formulario():
    fila_id = request.form.get("fila_id", type=int) or None
    return {
        "label": request.form.get("label", "").strip(),
        "unidade_id": request.form.get("unidade_id", type=int),
        "parent_id": request.form.get("parent_id", type=int) or None,
        "fila_id": fila_id,
        # sigla (adendo 10.47) só faz sentido em botão-folha (fila_id preenchido) -
        # botão-menu nunca tem sigla, mesma convenção do próprio fila_id desde a
        # 10.1, então ela é descartada aqui se não houver fila selecionada.
        "sigla": (request.form.get("sigla", "").strip().upper() or None) if fila_id is not None else None,
        "ordem": request.form.get("ordem", type=int) or 0,
    }


def _validar_totem_botao(dados, unidades, filas, candidatos_pai, botao_atual):
    if not dados["label"]:
        return "Informe o texto do botão."

    if dados["unidade_id"] not in {u.id for u in unidades}:
        return "Selecione uma unidade válida."

    if dados["parent_id"] is not None:
        pai = next((b for b in candidatos_pai if b.id == dados["parent_id"]), None)
        if pai is None:
            return "Selecione um botão pai válido (ou nenhum, para botão raiz)."
        if pai.unidade_id != dados["unidade_id"]:
            return "O botão pai selecionado pertence a outra unidade."

    if dados["fila_id"] is not None:
        fila = next((f for f in filas if f.id == dados["fila_id"]), None)
        if fila is None:
            return "Selecione uma fila válida (ou nenhuma, para botão apenas de navegação)."
        if fila.unidade_id != dados["unidade_id"]:
            return "A fila selecionada pertence a outra unidade."

        # adendo 10.47: botão-folha (com fila vinculada) exige sigla - sem ela o
        # totem não teria como compor o código da senha (ver bloqueio defensivo
        # em app/totem/routes.py::gerar_senha).
        if not dados["sigla"]:
            return "Informe a sigla do botão (obrigatória quando uma fila é selecionada)."
        if not SIGLA_RE.match(dados["sigla"]):
            return "A sigla deve ter de 2 a 4 letras (A-Z)."

        # unicidade: duas FILAS diferentes não podem usar a mesma sigla na
        # mesma unidade (código ambíguo entre filas de verdade diferentes) -
        # mas vários botões da MESMA fila podem compartilhar a sigla dela de
        # propósito (ex.: "Recepção Banco de Sangue" com 5 botões-folha
        # diferentes, todos sigla "BSG", mesmo sequencial) - caso real de
        # produção, não duplicidade a ser bloqueada.
        query = TotemBotao.query.filter_by(unidade_id=dados["unidade_id"], sigla=dados["sigla"]).filter(
            TotemBotao.fila_id != dados["fila_id"]
        )
        if botao_atual:
            query = query.filter(TotemBotao.id != botao_atual.id)
        if query.first():
            return "Essa sigla já está em uso por outra fila nessa unidade."

    return None
