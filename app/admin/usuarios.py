from flask import abort, flash, g, redirect, render_template, request, url_for
from flask_login import current_user

from app.admin import admin_bp
from app.admin.helpers import autorizar_objeto, resolver_empresa_contexto, resolver_unidade_contexto
from app.extensions import db
from app.models import Empresa, Perfil, Unidade, Usuario


@admin_bp.route("/usuarios")
def listar_usuarios():
    empresa = resolver_empresa_contexto()
    if empresa is None:
        # Só chega aqui como super admin (resolver_empresa_contexto nunca retorna
        # None para admin de empresa) - aproveita a tela de seleção de empresa
        # para também listar/gerenciar os super admins globais (sem empresa).
        super_admins = Usuario.query.filter_by(empresa_id=None).order_by(Usuario.nome).all()
        return render_template(
            "admin/selecionar_empresa.html",
            empresas=Empresa.query.order_by(Empresa.nome).all(),
            destino="admin.listar_usuarios",
            super_admins=super_admins,
        )

    unidade = resolver_unidade_contexto(empresa)
    query = Usuario.query.filter_by(empresa_id=empresa.id)
    if unidade is not None:
        query = query.filter(Usuario.unidades.any(Unidade.id == unidade.id))
    usuarios = query.order_by(Usuario.nome).all()
    return render_template("admin/usuarios/listar.html", empresa=empresa, unidade=unidade, usuarios=usuarios)


@admin_bp.route("/usuarios/globais/novo", methods=["GET", "POST"])
def novo_usuario_global():
    """Cria um super admin global (sem empresa) - só acessível a super admins.

    Mesmo formato do usuário 'superadmin' semeado em seeds/seed_inicial.py: sem
    perfis nem unidades (irrelevantes, já que Usuario.is_super_admin - baseado
    só em empresa_id IS NULL - dá bypass em todo RBAC de perfil/unidade).
    """
    if not g.is_super_admin:
        abort(403)

    if request.method == "POST":
        nome, login, senha, _perfil_ids, _unidade_ids = _ler_formulario()

        erro = _validar_usuario(nome, login, senha, senha_obrigatoria=True)
        if not erro and Usuario.query.filter_by(login=login).first():
            erro = "Já existe um usuário com esse login."

        if erro:
            flash(erro, "error")
            return render_template("admin/usuarios/form_global.html", usuario=None, nome=nome, login=login)

        usuario = Usuario(nome=nome, login=login, empresa_id=None)
        usuario.set_senha(senha)
        db.session.add(usuario)
        db.session.commit()
        flash(f"Super admin '{usuario.nome}' criado, com acesso a todas as empresas.", "info")
        return redirect(url_for("admin.listar_usuarios"))

    return render_template("admin/usuarios/form_global.html", usuario=None, nome="", login="")


@admin_bp.route("/usuarios/novo", methods=["GET", "POST"])
def novo_usuario():
    empresa = resolver_empresa_contexto()
    if empresa is None:
        flash("Selecione uma empresa antes de cadastrar um usuário.", "error")
        return redirect(url_for("admin.listar_usuarios"))

    perfis, unidades = _opcoes_formulario(empresa)

    if request.method == "POST":
        nome, login, senha, perfil_ids, unidade_ids = _ler_formulario()

        erro = _validar_usuario(nome, login, senha, senha_obrigatoria=True)
        if not erro and Usuario.query.filter_by(login=login).first():
            erro = "Já existe um usuário com esse login."

        if erro:
            flash(erro, "error")
            return render_template(
                "admin/usuarios/form.html",
                empresa=empresa,
                usuario=None,
                perfis=perfis,
                unidades=unidades,
                perfis_selecionados=perfil_ids,
                unidades_selecionadas=unidade_ids,
            )

        usuario = Usuario(nome=nome, login=login, empresa_id=empresa.id)
        usuario.set_senha(senha)
        # filtra contra as listas pré-carregadas (já restritas à empresa) -
        # nunca confiar no id de unidade/perfil vindo direto do formulário
        usuario.perfis = [p for p in perfis if p.id in perfil_ids]
        usuario.unidades = [u for u in unidades if u.id in unidade_ids]
        db.session.add(usuario)
        db.session.commit()
        flash(f"Usuário '{usuario.nome}' criado.", "info")
        return redirect(url_for("admin.listar_usuarios", empresa_id=empresa.id))

    return render_template(
        "admin/usuarios/form.html",
        empresa=empresa,
        usuario=None,
        perfis=perfis,
        unidades=unidades,
        perfis_selecionados=set(),
        unidades_selecionadas=set(),
    )


@admin_bp.route("/usuarios/<int:usuario_id>/editar", methods=["GET", "POST"])
def editar_usuario(usuario_id):
    usuario = Usuario.query.get_or_404(usuario_id)
    autorizar_objeto(usuario)

    if usuario.empresa_id is None:
        # Super admin global: sem empresa, sem perfis/unidades para escolher -
        # usa o formulário simples (mesmo usado por novo_usuario_global).
        return _editar_usuario_global(usuario)

    empresa = usuario.empresa
    perfis, unidades = _opcoes_formulario(empresa)

    if request.method == "POST":
        nome, login, senha, perfil_ids, unidade_ids = _ler_formulario()

        erro = _validar_usuario(nome, login, senha, senha_obrigatoria=False)
        if not erro:
            duplicado = Usuario.query.filter(Usuario.login == login, Usuario.id != usuario.id).first()
            if duplicado:
                erro = "Já existe um usuário com esse login."

        if erro:
            flash(erro, "error")
            return render_template(
                "admin/usuarios/form.html",
                empresa=empresa,
                usuario=usuario,
                perfis=perfis,
                unidades=unidades,
                perfis_selecionados=perfil_ids,
                unidades_selecionadas=unidade_ids,
            )

        usuario.nome = nome
        usuario.login = login
        if senha:
            usuario.set_senha(senha)
        usuario.perfis = [p for p in perfis if p.id in perfil_ids]
        usuario.unidades = [u for u in unidades if u.id in unidade_ids]
        db.session.commit()
        flash("Usuário atualizado.", "info")
        return redirect(url_for("admin.listar_usuarios", empresa_id=empresa.id))

    perfis_selecionados = {p.id for p in usuario.perfis}
    unidades_selecionadas = {u.id for u in usuario.unidades}
    return render_template(
        "admin/usuarios/form.html",
        empresa=empresa,
        usuario=usuario,
        perfis=perfis,
        unidades=unidades,
        perfis_selecionados=perfis_selecionados,
        unidades_selecionadas=unidades_selecionadas,
    )


@admin_bp.route("/usuarios/<int:usuario_id>/toggle-ativo", methods=["POST"])
def alternar_usuario(usuario_id):
    usuario = Usuario.query.get_or_404(usuario_id)
    autorizar_objeto(usuario)

    if usuario.id == current_user.id:
        flash("Você não pode desativar a si mesmo.", "error")
        return redirect(_destino_pos_acao(usuario))

    usuario.ativo = not usuario.ativo
    db.session.commit()
    flash(f"Usuário {'ativado' if usuario.ativo else 'desativado'}.", "info")
    return redirect(_destino_pos_acao(usuario))


def _destino_pos_acao(usuario):
    # empresa_id=None (super admin global) não pode virar querystring `empresa_id=`
    # vazio - url_for omitiria o valor e cairia na mesma rota, mas de forma
    # implícita/frágil; deixa explícito que o destino é a listagem sem empresa.
    if usuario.empresa_id is None:
        return url_for("admin.listar_usuarios")
    return url_for("admin.listar_usuarios", empresa_id=usuario.empresa_id)


def _editar_usuario_global(usuario):
    if request.method == "POST":
        nome, login, senha, _perfil_ids, _unidade_ids = _ler_formulario()

        erro = _validar_usuario(nome, login, senha, senha_obrigatoria=False)
        if not erro:
            duplicado = Usuario.query.filter(Usuario.login == login, Usuario.id != usuario.id).first()
            if duplicado:
                erro = "Já existe um usuário com esse login."

        if erro:
            flash(erro, "error")
            return render_template("admin/usuarios/form_global.html", usuario=usuario, nome=nome, login=login)

        usuario.nome = nome
        usuario.login = login
        if senha:
            usuario.set_senha(senha)
        db.session.commit()
        flash("Super admin atualizado.", "info")
        return redirect(url_for("admin.listar_usuarios"))

    return render_template("admin/usuarios/form_global.html", usuario=usuario, nome=usuario.nome, login=usuario.login)


def _opcoes_formulario(empresa):
    perfis = Perfil.query.order_by(Perfil.nome).all()
    unidades = Unidade.query.filter_by(empresa_id=empresa.id).order_by(Unidade.nome).all()
    return perfis, unidades


def _ler_formulario():
    nome = request.form.get("nome", "").strip()
    login = request.form.get("login", "").strip()
    senha = request.form.get("senha", "")
    perfil_ids = {int(v) for v in request.form.getlist("perfis") if v.isdigit()}
    unidade_ids = {int(v) for v in request.form.getlist("unidades") if v.isdigit()}
    return nome, login, senha, perfil_ids, unidade_ids


def _validar_usuario(nome, login, senha, senha_obrigatoria):
    if not nome:
        return "Informe o nome do usuário."
    if not login:
        return "Informe o login do usuário."
    if senha_obrigatoria and not senha:
        return "Informe a senha do usuário."
    if senha and len(senha) < 6:
        return "A senha deve ter ao menos 6 caracteres."
    return None
