import re

from flask import flash, redirect, render_template, request, url_for

from app.admin import admin_bp
from app.admin.helpers import autorizar_objeto, resolver_empresa_contexto, resolver_unidade_contexto, tipos_senha_da_empresa
from app.extensions import db
from app.models import Empresa, Senha, TipoSenha

COR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


@admin_bp.route("/tipos-senha")
def listar_tipos_senha():
    empresa = resolver_empresa_contexto()
    if empresa is None:
        return render_template(
            "admin/selecionar_empresa.html",
            empresas=Empresa.query.order_by(Empresa.nome).all(),
            destino="admin.listar_tipos_senha",
        )

    unidade = resolver_unidade_contexto(empresa)
    tipos = tipos_senha_da_empresa(empresa)
    return render_template("admin/tipos_senha/listar.html", empresa=empresa, unidade=unidade, tipos=tipos)


@admin_bp.route("/tipos-senha/novo", methods=["GET", "POST"])
def novo_tipo_senha():
    empresa = resolver_empresa_contexto()
    if empresa is None:
        flash("Selecione uma empresa antes de cadastrar um tipo de senha.", "error")
        return redirect(url_for("admin.listar_tipos_senha"))

    if request.method == "POST":
        dados = _ler_formulario()
        erro = _validar_tipo_senha(dados)
        if erro:
            flash(erro, "error")
            return render_template("admin/tipos_senha/form.html", empresa=empresa, tipo=None, valores=dados)

        tipo = TipoSenha(
            empresa_id=empresa.id, nome=dados["nome"], prefixo=dados["prefixo"], cor=dados["cor"],
            peso_prioridade=dados["peso_prioridade"], tempo_maximo_min=dados["tempo_maximo_min"],
        )
        db.session.add(tipo)
        db.session.commit()
        flash(f"Tipo de senha '{tipo.nome}' criado.", "info")
        return redirect(url_for("admin.listar_tipos_senha", empresa_id=empresa.id))

    return render_template("admin/tipos_senha/form.html", empresa=empresa, tipo=None, valores=None)


@admin_bp.route("/tipos-senha/<int:tipo_id>/editar", methods=["GET", "POST"])
def editar_tipo_senha(tipo_id):
    tipo = TipoSenha.query.get_or_404(tipo_id)
    autorizar_objeto(tipo)
    empresa = Empresa.query.get_or_404(tipo.empresa_id)

    if request.method == "POST":
        dados = _ler_formulario()
        erro = _validar_tipo_senha(dados)
        if erro:
            flash(erro, "error")
            return render_template(
                "admin/tipos_senha/form.html", empresa=empresa, tipo=tipo, valores=dados,
                esconder_seletor_contexto=True,
            )

        tipo.nome = dados["nome"]
        tipo.prefixo = dados["prefixo"]
        tipo.cor = dados["cor"]
        tipo.peso_prioridade = dados["peso_prioridade"]
        tipo.tempo_maximo_min = dados["tempo_maximo_min"]
        db.session.commit()
        flash("Tipo de senha atualizado.", "info")
        return redirect(url_for("admin.listar_tipos_senha", empresa_id=empresa.id))

    return render_template(
        "admin/tipos_senha/form.html", empresa=empresa, tipo=tipo, valores=None, esconder_seletor_contexto=True,
    )


@admin_bp.route("/tipos-senha/<int:tipo_id>/toggle-ativo", methods=["POST"])
def alternar_tipo_senha(tipo_id):
    tipo = TipoSenha.query.get_or_404(tipo_id)
    autorizar_objeto(tipo)

    tipo.ativo = not tipo.ativo
    db.session.commit()
    flash(f"Tipo de senha {'ativado' if tipo.ativo else 'desativado'}.", "info")
    return redirect(url_for("admin.listar_tipos_senha", empresa_id=tipo.empresa_id))


@admin_bp.route("/tipos-senha/<int:tipo_id>/excluir", methods=["POST"])
def excluir_tipo_senha(tipo_id):
    """Exclusão segura (ver mesmo padrão em admin/filas.py::excluir_fila) - só apaga
    quando não há nenhuma senha (histórico) registrada com este tipo."""
    tipo = TipoSenha.query.get_or_404(tipo_id)
    autorizar_objeto(tipo)

    if Senha.query.filter_by(tipo_senha_id=tipo.id).first():
        flash(
            f"Não é possível excluir '{tipo.nome}': existem senhas registradas com este tipo (histórico). "
            "Desative o tipo em vez de excluir.", "error",
        )
        return redirect(url_for("admin.listar_tipos_senha", empresa_id=tipo.empresa_id))

    empresa_id, nome = tipo.empresa_id, tipo.nome
    tipo.filas = []  # vínculo de configuração (fila_tipos_senha), não histórico
    db.session.delete(tipo)
    db.session.commit()
    flash(f"Tipo de senha '{nome}' excluído.", "info")
    return redirect(url_for("admin.listar_tipos_senha", empresa_id=empresa_id))


def _ler_formulario():
    prefixo = request.form.get("prefixo", "").strip().upper()
    cor = request.form.get("cor", "").strip()
    return {
        "nome": request.form.get("nome", "").strip(),
        "prefixo": prefixo or None,
        "cor": cor or None,
        "peso_prioridade": request.form.get("peso_prioridade", type=int) or 0,
        "tempo_maximo_min": request.form.get("tempo_maximo_min", type=int) or None,
    }


def _validar_tipo_senha(dados):
    if not dados["nome"]:
        return "Informe o nome do tipo de senha."

    if dados["prefixo"] is not None and not re.match(r"^[A-Z]{1,2}$", dados["prefixo"]):
        return "O prefixo, quando informado, deve ter 1 ou 2 letras (A-Z)."

    if dados["cor"] is not None and not COR_RE.match(dados["cor"]):
        return "A cor, quando informada, deve estar no formato hexadecimal (ex.: #E53935)."

    if not (0 <= dados["peso_prioridade"] <= 9):
        return "Peso de prioridade deve ser um número entre 0 e 9."

    if dados["tempo_maximo_min"] is not None and dados["tempo_maximo_min"] <= 0:
        return "Tempo máximo, quando informado, deve ser maior que zero (em minutos)."

    return None
