from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user

from app.admin import admin_bp
from app.admin.helpers import (
    autorizar_objeto,
    filas_da_empresa,
    resolver_empresa_contexto,
    resolver_unidade_contexto,
    unidades_da_empresa,
)
from app.atendimento.helpers import usuarios_ativos_da_empresa
from app.extensions import db
from app.models import Empresa, Fila, Local, LocalRodizioFila, Senha, Usuario
from app.models.local import TIPO_LOCAL


@admin_bp.route("/locais")
def listar_locais():
    empresa = resolver_empresa_contexto()
    if empresa is None:
        return render_template(
            "admin/selecionar_empresa.html",
            empresas=Empresa.query.order_by(Empresa.nome).all(),
            destino="admin.listar_locais",
        )

    unidade = resolver_unidade_contexto(empresa)
    query = Local.query.filter_by(empresa_id=empresa.id)
    if unidade is not None:
        query = query.filter_by(unidade_id=unidade.id)
    locais = query.order_by(Local.nome).all()
    return render_template("admin/locais/listar.html", empresa=empresa, unidade=unidade, locais=locais)


@admin_bp.route("/locais/novo", methods=["GET", "POST"])
def novo_local():
    empresa = resolver_empresa_contexto()
    if empresa is None:
        flash("Selecione uma empresa antes de cadastrar um local.", "error")
        return redirect(url_for("admin.listar_locais"))

    unidades = unidades_da_empresa(empresa)
    if not unidades:
        flash("Cadastre ao menos uma unidade antes de criar um local.", "error")
        return redirect(url_for("admin.listar_locais", empresa_id=empresa.id))

    filas = filas_da_empresa(empresa)

    if request.method == "POST":
        dados = _ler_formulario()
        erro = _validar_local(dados, unidades, filas)
        if erro:
            flash(erro, "error")
            return render_template(
                "admin/locais/form.html", empresa=empresa, local=None, unidades=unidades, filas=filas,
                valores=dados, filas_destino_selecionadas=dados["fila_destino_ids"],
            )

        local = Local(
            empresa_id=empresa.id, unidade_id=dados["unidade_id"], nome=dados["nome"], tipo=dados["tipo"],
        )
        db.session.add(local)
        db.session.flush()  # garante local.id antes de gravar a M2M abaixo
        local.filas_destino_permitidas = [f for f in filas if f.id in dados["fila_destino_ids"]]
        db.session.commit()
        flash(f"Local '{local.nome}' criado.", "info")
        return redirect(url_for("admin.listar_locais", empresa_id=empresa.id))

    return render_template(
        "admin/locais/form.html", empresa=empresa, local=None, unidades=unidades, filas=filas,
        valores=None, filas_destino_selecionadas=set(),
    )


@admin_bp.route("/locais/<int:local_id>/editar", methods=["GET", "POST"])
def editar_local(local_id):
    local = Local.query.get_or_404(local_id)
    autorizar_objeto(local)
    empresa = Empresa.query.get_or_404(local.empresa_id)
    unidades = unidades_da_empresa(empresa)
    filas = filas_da_empresa(empresa)

    if request.method == "POST":
        dados = _ler_formulario()
        erro = _validar_local(dados, unidades, filas)
        if erro:
            flash(erro, "error")
            return render_template(
                "admin/locais/form.html", empresa=empresa, local=local, unidades=unidades, filas=filas,
                valores=dados, filas_destino_selecionadas=dados["fila_destino_ids"],
            )

        local.unidade_id = dados["unidade_id"]
        local.nome = dados["nome"]
        local.tipo = dados["tipo"]
        local.filas_destino_permitidas = [f for f in filas if f.id in dados["fila_destino_ids"]]
        db.session.commit()
        flash("Local atualizado.", "info")
        return redirect(url_for("admin.listar_locais", empresa_id=empresa.id))

    filas_destino_selecionadas = {f.id for f in local.filas_destino_permitidas}
    return render_template(
        "admin/locais/form.html", empresa=empresa, local=local, unidades=unidades, filas=filas,
        valores=None, filas_destino_selecionadas=filas_destino_selecionadas,
    )


@admin_bp.route("/locais/<int:local_id>/toggle-ativo", methods=["POST"])
def alternar_local(local_id):
    local = Local.query.get_or_404(local_id)
    autorizar_objeto(local)

    local.ativo = not local.ativo
    db.session.commit()
    flash(f"Local {'ativado' if local.ativo else 'desativado'}.", "info")
    return redirect(url_for("admin.listar_locais", empresa_id=local.empresa_id))


@admin_bp.route("/locais/<int:local_id>/excluir", methods=["POST"])
def excluir_local(local_id):
    """Exclusão segura (ver mesmo padrão em admin/filas.py::excluir_fila) - só apaga
    quando não há nenhuma senha (histórico) registrada neste local."""
    local = Local.query.get_or_404(local_id)
    autorizar_objeto(local)

    if Senha.query.filter_by(local_id=local.id).first():
        flash(
            f"Não é possível excluir '{local.nome}': existem senhas atendidas registradas neste local "
            "(histórico). Desative o local em vez de excluir.", "error",
        )
        return redirect(url_for("admin.listar_locais", empresa_id=local.empresa_id))

    empresa_id, nome = local.empresa_id, local.nome
    # "local atual" do operador é só um estado de sessão persistido, não histórico -
    # seguro limpar. filas_destino_permitidas é vínculo de configuração, idem.
    Usuario.query.filter_by(local_atual_id=local.id).update({"local_atual_id": None, "local_desde": None})
    local.filas_destino_permitidas = []
    db.session.delete(local)  # rodizio_ordenado cascateia sozinho (cascade="all, delete-orphan")
    db.session.commit()
    flash(f"Local '{nome}' excluído.", "info")
    return redirect(url_for("admin.listar_locais", empresa_id=empresa_id))


@admin_bp.route("/locais/ocupacao")
def ocupacao_locais():
    """Tela administrativa (item pendente desde a seção 10.3 - `usuarios_ativos_por_local`
    já existia "para uso futuro") mostrando quem está ocupando qual Local agora, na
    empresa inteira - de onde o admin pode desconectar um usuário travado."""
    empresa = resolver_empresa_contexto()
    if empresa is None:
        return render_template(
            "admin/selecionar_empresa.html",
            empresas=Empresa.query.order_by(Empresa.nome).all(),
            destino="admin.ocupacao_locais",
        )

    unidade = resolver_unidade_contexto(empresa)
    ativos = usuarios_ativos_da_empresa(empresa.id)
    if unidade is not None:
        ativos = [item for item in ativos if item["local"].unidade_id == unidade.id]
    return render_template("admin/locais/ocupacao.html", empresa=empresa, unidade=unidade, ativos=ativos)


@admin_bp.route("/locais/ocupacao/<int:usuario_id>/desconectar", methods=["POST"])
def desconectar_usuario_local(usuario_id):
    """Libera o Local E derruba a sessão de login do usuário (Usuario.sessao_versao,
    migration a1c4e8f2b7d3) - na próxima requisição dele, load_user() (app/__init__.py)
    detecta que o número gravado no cookie não bate mais com o do banco e trata como
    não autenticado, redirecionando pro login. Sem isso, o local ficava só "liberado
    no banco" mas o operador continuava logado e podia, por exemplo, escolher outro
    local imediatamente sem nunca ter sido de fato desconectado."""
    usuario = Usuario.query.get_or_404(usuario_id)
    autorizar_objeto(usuario)

    if usuario.id == current_user.id:
        flash("Você não pode desconectar a si mesmo.", "error")
        return redirect(url_for("admin.ocupacao_locais", empresa_id=usuario.empresa_id))

    usuario.sessao_versao = (usuario.sessao_versao or 0) + 1

    if usuario.local_atual_id is None:
        db.session.commit()
        flash(f"{usuario.nome} já não estava ocupando nenhum local - a sessão dele foi encerrada mesmo assim.", "info")
        return redirect(url_for("admin.ocupacao_locais", empresa_id=usuario.empresa_id))

    nome_local = usuario.local_atual.nome if usuario.local_atual else "—"
    usuario.local_atual_id = None
    usuario.local_desde = None
    db.session.commit()
    flash(f"{usuario.nome} foi desconectado de '{nome_local}' e a sessão dele foi encerrada.", "info")
    return redirect(url_for("admin.ocupacao_locais", empresa_id=usuario.empresa_id))


@admin_bp.route("/locais/<int:local_id>/rodizio", methods=["GET", "POST"])
def rodizio_local(local_id):
    """Configuração do rodízio automático entre múltiplas filas (adendo 10.8, Fase
    10.8-B) - só a lista de filas da MESMA unidade do Local aparece como opção (a
    própria query já filtra por unidade_id, diferente de filas_destino_permitidas
    que mostra a empresa toda e valida depois - aqui não existe seletor de unidade
    nesta tela, então nem mostrar fila de outra unidade como opção é possível)."""
    local = Local.query.get_or_404(local_id)
    autorizar_objeto(local)
    empresa = Empresa.query.get_or_404(local.empresa_id)

    filas = (
        Fila.query.filter_by(empresa_id=local.empresa_id, unidade_id=local.unidade_id, ativo=True)
        .order_by(Fila.nome)
        .all()
    )

    if request.method == "POST":
        dados = _ler_formulario_rodizio()
        erro = _validar_rodizio(dados, filas)
        if erro:
            flash(erro, "error")
            valores_por_fila = {
                fila_id: {
                    "ordem": cfg["ordem"] if cfg["ordem"] is not None else 0,
                    "chamadas_seguidas": cfg["chamadas_seguidas"] if cfg["chamadas_seguidas"] is not None else 1,
                }
                for fila_id, cfg in dados["config"].items()
            }
            return render_template(
                "admin/locais/rodizio.html", empresa=empresa, unidade=local.unidade, local=local, filas=filas,
                fila_ids_selecionadas=dados["fila_ids"], valores_por_fila=valores_por_fila,
            )

        # substitui a lista inteira (mesmo padrão já usado em filas_destino_permitidas/
        # tv.filas) - mais simples que calcular diff de adição/remoção
        local.rodizio_ordenado = [
            LocalRodizioFila(
                fila_id=fila_id,
                ordem=dados["config"][fila_id]["ordem"],
                chamadas_seguidas=dados["config"][fila_id]["chamadas_seguidas"],
            )
            for fila_id in dados["fila_ids"]
        ]
        db.session.commit()

        if dados["fila_ids"]:
            flash(
                "Rodízio salvo. Este Local passará a usar a tela unificada de Atendimento "
                "por Local (rotação automática entre as filas configuradas), em vez da "
                "escolha manual de fila.", "info",
            )
        else:
            flash("Rodízio removido - este Local volta a usar a escolha manual de fila.", "info")
        return redirect(url_for("admin.listar_locais", empresa_id=empresa.id))

    fila_ids_selecionadas = {item.fila_id for item in local.rodizio_ordenado}
    valores_por_fila = {
        item.fila_id: {"ordem": item.ordem, "chamadas_seguidas": item.chamadas_seguidas}
        for item in local.rodizio_ordenado
    }
    return render_template(
        "admin/locais/rodizio.html", empresa=empresa, unidade=local.unidade, local=local, filas=filas,
        fila_ids_selecionadas=fila_ids_selecionadas, valores_por_fila=valores_por_fila,
    )


def _ler_formulario():
    tipo = request.form.get("tipo", "").strip()
    return {
        "nome": request.form.get("nome", "").strip(),
        "unidade_id": request.form.get("unidade_id", type=int),
        "tipo": tipo if tipo in TIPO_LOCAL else None,
        "fila_destino_ids": {int(v) for v in request.form.getlist("filas_destino") if v.isdigit()},
    }


def _validar_local(dados, unidades, filas):
    if not dados["nome"]:
        return "Informe o nome do local."

    if dados["unidade_id"] not in {u.id for u in unidades}:
        return "Selecione uma unidade válida."

    filas_por_id = {f.id: f for f in filas}
    if not dados["fila_destino_ids"] <= filas_por_id.keys():
        return "Selecione filas de destino válidas."

    # nunca deixar configurar cruzado (seção 10.3/diagnóstico item 2) - a tabela
    # associativa não tem unidade_id própria, então essa checagem é o único lugar
    # que garante que Local e filas de destino permitidas pertencem à mesma unidade.
    for fila_id in dados["fila_destino_ids"]:
        if filas_por_id[fila_id].unidade_id != dados["unidade_id"]:
            return "As filas de destino selecionadas devem pertencer à mesma unidade do local."

    return None


def _ler_formulario_rodizio():
    fila_ids = {int(v) for v in request.form.getlist("rodizio_filas") if v.isdigit()}
    config = {
        fila_id: {
            "ordem": request.form.get(f"ordem_{fila_id}", type=int),
            "chamadas_seguidas": request.form.get(f"chamadas_seguidas_{fila_id}", type=int),
        }
        for fila_id in fila_ids
    }
    return {"fila_ids": fila_ids, "config": config}


def _validar_rodizio(dados, filas):
    # `filas` já vem filtrada pela MESMA unidade do local (query em rodizio_local) -
    # nunca confiar no id vindo direto do formulário mesmo assim (adendo 10.8, seção 1).
    filas_validas = {f.id for f in filas}
    if not dados["fila_ids"] <= filas_validas:
        return "Selecione filas válidas (da mesma unidade do local)."

    for fila_id in dados["fila_ids"]:
        cfg = dados["config"][fila_id]
        if cfg["chamadas_seguidas"] is None or cfg["chamadas_seguidas"] < 1:
            return "Quantidade de chamadas seguidas deve ser um número maior ou igual a 1."
        if cfg["ordem"] is None or cfg["ordem"] < 0:
            return "Ordem deve ser um número maior ou igual a 0."

    return None
