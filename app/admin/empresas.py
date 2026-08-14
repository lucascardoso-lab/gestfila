from flask import flash, redirect, render_template, request, url_for

from app.admin import admin_bp
from app.extensions import db
from app.models import Empresa, Fila, Local, Servico, TipoSenha, Totem, TotemBotao, Tv, Unidade, Usuario
from app.utils.decorators import super_admin_required
from app.utils.uploads import remover_logo_empresa, salvar_logo_empresa


@admin_bp.route("/empresas")
@super_admin_required
def listar_empresas():
    empresas = Empresa.query.order_by(Empresa.nome).all()
    return render_template("admin/empresas/listar.html", empresas=empresas)


@admin_bp.route("/empresas/nova", methods=["GET", "POST"])
@super_admin_required
def nova_empresa():
    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        cnpj = request.form.get("cnpj", "").strip() or None

        erro = _validar_empresa(nome, cnpj, empresa_atual=None)
        if erro:
            flash(erro, "error")
            return render_template("admin/empresas/form.html", empresa=None)

        empresa = Empresa(nome=nome, cnpj=cnpj)
        db.session.add(empresa)
        db.session.commit()
        flash(f"Empresa '{empresa.nome}' criada.", "info")
        return redirect(url_for("admin.listar_empresas"))

    return render_template("admin/empresas/form.html", empresa=None)


@admin_bp.route("/empresas/<int:empresa_id>/editar", methods=["GET", "POST"])
@super_admin_required
def editar_empresa(empresa_id):
    empresa = Empresa.query.get_or_404(empresa_id)

    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        cnpj = request.form.get("cnpj", "").strip() or None

        erro = _validar_empresa(nome, cnpj, empresa_atual=empresa)
        if erro:
            flash(erro, "error")
            return render_template("admin/empresas/form.html", empresa=empresa, esconder_seletor_contexto=True)

        # Logo (adendo 10.43) - opcional, só troca se um arquivo novo foi de
        # fato enviado neste POST (campo vazio = mantém o logo atual, não
        # apaga por engano). Validação de conteúdo real (não extensão) e
        # gravação do arquivo ficam em app/utils/uploads.py - aqui só decide
        # o que fazer com o resultado.
        arquivo_logo = request.files.get("logo")
        if arquivo_logo and arquivo_logo.filename:
            try:
                empresa.logo_filename = salvar_logo_empresa(empresa.id, arquivo_logo)
            except ValueError as erro_logo:
                flash(str(erro_logo), "error")
                return render_template("admin/empresas/form.html", empresa=empresa, esconder_seletor_contexto=True)

        empresa.nome = nome
        empresa.cnpj = cnpj
        db.session.commit()
        flash("Empresa atualizada.", "info")
        return redirect(url_for("admin.listar_empresas"))

    return render_template("admin/empresas/form.html", empresa=empresa, esconder_seletor_contexto=True)


@admin_bp.route("/empresas/<int:empresa_id>/logo/remover", methods=["POST"])
@super_admin_required
def remover_logo_empresa_view(empresa_id):
    empresa = Empresa.query.get_or_404(empresa_id)
    remover_logo_empresa(empresa.id)
    empresa.logo_filename = None
    db.session.commit()
    flash("Logo removido - a empresa volta a ser exibida só com o nome em texto.", "info")
    return redirect(url_for("admin.editar_empresa", empresa_id=empresa.id))


@admin_bp.route("/empresas/<int:empresa_id>/toggle-ativo", methods=["POST"])
@super_admin_required
def alternar_empresa(empresa_id):
    empresa = Empresa.query.get_or_404(empresa_id)
    empresa.ativo = not empresa.ativo
    db.session.commit()
    flash(f"Empresa {'ativada' if empresa.ativo else 'desativada'}.", "info")
    return redirect(url_for("admin.listar_empresas"))


@admin_bp.route("/empresas/<int:empresa_id>/excluir", methods=["POST"])
@super_admin_required
def excluir_empresa(empresa_id):
    """Exclusão segura (ver mesmo padrão em admin/filas.py::excluir_fila) - a mais
    restritiva das 7: uma empresa só é excluída de verdade se estiver completamente
    vazia (nenhuma unidade/usuário/fila/local/totem/tv/tipo de senha/botão cadastrado
    nela) - na prática, só empresas criadas por engano, antes de qualquer cadastro."""
    empresa = Empresa.query.get_or_404(empresa_id)

    checagens = [
        (Unidade, "unidades"),
        (Usuario, "usuários"),
        (Fila, "filas"),
        (Local, "locais"),
        (Totem, "totens"),
        (Tv, "TVs"),
        (TipoSenha, "tipos de senha"),
        (TotemBotao, "botões do totem"),
        (Servico, "serviços (legado)"),
    ]
    bloqueios = [
        nome for modelo, nome in checagens
        if modelo.query.filter_by(empresa_id=empresa.id).first()
    ]

    if bloqueios:
        flash(
            f"Não é possível excluir '{empresa.nome}': ainda há {', '.join(bloqueios)} cadastrados nela. "
            "Remova-os primeiro, ou desative a empresa em vez de excluir.", "error",
        )
        return redirect(url_for("admin.listar_empresas"))

    nome = empresa.nome
    db.session.delete(empresa)
    db.session.commit()
    flash(f"Empresa '{nome}' excluída.", "info")
    return redirect(url_for("admin.listar_empresas"))


def _validar_empresa(nome, cnpj, empresa_atual):
    if not nome:
        return "Informe o nome da empresa."

    if cnpj:
        query = Empresa.query.filter_by(cnpj=cnpj)
        if empresa_atual:
            query = query.filter(Empresa.id != empresa_atual.id)
        if query.first():
            return "Já existe uma empresa cadastrada com esse CNPJ."

    return None
