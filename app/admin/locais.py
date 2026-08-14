import os

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
from app.atendimento.helpers import caminho_audio_local, usuarios_ativos_da_empresa
from app.extensions import db
from app.models import Empresa, Fila, Local, LocalRodizioFila, Senha, Tv, Usuario
from app.models.local import TIPO_LOCAL
from app.utils.slug import gerar_slug_audio_local


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
    # ordenado por identificador_admin (não nome_exibicao) - é o campo que de
    # fato aparece nesta tela (BLUEPRINT.md, seção 10.32).
    locais = query.order_by(Local.identificador_admin).all()
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
        erro = _validar_local(dados, unidades, filas, empresa)
        if erro:
            flash(erro, "error")
            return render_template(
                "admin/locais/form.html", empresa=empresa, local=None, unidades=unidades, filas=filas,
                valores=dados, filas_destino_selecionadas=dados["fila_destino_ids"],
                unidades_siglas=_siglas_por_unidade(unidades),
            )

        local = Local(
            empresa_id=empresa.id, unidade_id=dados["unidade_id"],
            # `nome` (legado) mantido em sincronia com nome_exibicao - ver
            # BLUEPRINT.md seção 10.32 (será removido numa fase futura).
            nome=dados["nome_exibicao"], nome_exibicao=dados["nome_exibicao"],
            identificador_admin=dados["identificador_admin"], tipo=dados["tipo"],
        )
        db.session.add(local)
        db.session.flush()  # garante local.id antes de gravar a M2M abaixo
        local.filas_destino_permitidas = [f for f in filas if f.id in dados["fila_destino_ids"]]
        db.session.commit()
        flash(f"Local '{local.identificador_admin}' criado.", "info")
        _avisar_se_falta_audio_pregravado(local)
        return redirect(url_for("admin.listar_locais", empresa_id=empresa.id))

    return render_template(
        "admin/locais/form.html", empresa=empresa, local=None, unidades=unidades, filas=filas,
        valores=None, filas_destino_selecionadas=set(), unidades_siglas=_siglas_por_unidade(unidades),
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
        erro = _validar_local(dados, unidades, filas, empresa, local_atual=local)
        if erro:
            flash(erro, "error")
            return render_template(
                "admin/locais/form.html", empresa=empresa, local=local, unidades=unidades, filas=filas,
                valores=dados, filas_destino_selecionadas=dados["fila_destino_ids"],
                unidades_siglas=_siglas_por_unidade(unidades), esconder_seletor_contexto=True,
            )

        local.unidade_id = dados["unidade_id"]
        local.nome = dados["nome_exibicao"]
        local.nome_exibicao = dados["nome_exibicao"]
        local.identificador_admin = dados["identificador_admin"]
        local.tipo = dados["tipo"]
        local.filas_destino_permitidas = [f for f in filas if f.id in dados["fila_destino_ids"]]
        db.session.commit()
        flash("Local atualizado.", "info")
        _avisar_se_falta_audio_pregravado(local)
        return redirect(url_for("admin.listar_locais", empresa_id=empresa.id))

    filas_destino_selecionadas = {f.id for f in local.filas_destino_permitidas}
    return render_template(
        "admin/locais/form.html", empresa=empresa, local=local, unidades=unidades, filas=filas,
        valores=None, filas_destino_selecionadas=filas_destino_selecionadas,
        unidades_siglas=_siglas_por_unidade(unidades), esconder_seletor_contexto=True,
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
            f"Não é possível excluir '{local.identificador_admin}': existem senhas atendidas registradas "
            "neste local (histórico). Desative o local em vez de excluir.", "error",
        )
        return redirect(url_for("admin.listar_locais", empresa_id=local.empresa_id))

    empresa_id, nome = local.empresa_id, local.identificador_admin
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

    nome_local = usuario.local_atual.identificador_admin if usuario.local_atual else "—"
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
                esconder_seletor_contexto=True,
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
        esconder_seletor_contexto=True,
    )


def _avisar_se_falta_audio_pregravado(local):
    """Aviso não-bloqueante (Opção A do adendo do áudio pré-gravado): se este Local
    ainda não tem o arquivo `local_<slug>.wav` gravado E existe pelo menos uma TV na
    MESMA unidade usando o fallback de áudio pré-gravado, avisa que essas TVs vão
    anunciar este local com a frase genérica até o arquivo ser adicionado. Sem TV com
    o fallback ligado naquela unidade, não avisa nada (evita ruído/falso positivo)."""
    slug = gerar_slug_audio_local(local.nome_exibicao)
    if os.path.exists(caminho_audio_local(slug)):
        return
    tem_tv_pregravada = (
        Tv.query.filter_by(unidade_id=local.unidade_id, usa_audio_pregravado=True).first() is not None
    )
    if not tem_tv_pregravada:
        return
    flash(
        f"Atenção: ainda não há áudio gravado para '{local.nome_exibicao}' "
        f"(arquivo esperado: static/audio/local_{slug}.wav). Enquanto ele não for adicionado, "
        "as TVs desta unidade que usam áudio pré-gravado vão anunciar este local com uma frase "
        "genérica (\"dirija-se ao local indicado no painel\"), não com o nome exato.",
        "info",
    )


def _siglas_por_unidade(unidades):
    """Mapa id -> sigla_exibicao para a sugestão automática de identificador_admin
    no form (JS) - mesma convenção da migration 5ba0cdce86cb/adendo 10.22."""
    return {u.id: u.sigla_exibicao for u in unidades}


def _ler_formulario():
    tipo = request.form.get("tipo", "").strip()
    return {
        "nome_exibicao": request.form.get("nome_exibicao", "").strip(),
        "identificador_admin": request.form.get("identificador_admin", "").strip(),
        "unidade_id": request.form.get("unidade_id", type=int),
        "tipo": tipo if tipo in TIPO_LOCAL else None,
        "fila_destino_ids": {int(v) for v in request.form.getlist("filas_destino") if v.isdigit()},
    }


def _validar_local(dados, unidades, filas, empresa, local_atual=None):
    if not dados["nome_exibicao"]:
        return "Informe o nome de exibição do local."

    if not dados["identificador_admin"]:
        return "Informe o identificador administrativo do local."

    if dados["unidade_id"] not in {u.id for u in unidades}:
        return "Selecione uma unidade válida."

    # Unicidade de identificador_admin por empresa (BLUEPRINT.md, seção 10.32) -
    # checada aqui, em Python, para dar uma mensagem clara em vez de deixar
    # estourar o erro cru de integridade do banco (que também tem essa
    # constraint, como rede de segurança). Case-insensitive de propósito -
    # mesma semântica da collation real do MySQL (utf8mb4_0900_ai_ci) usada
    # nesta coluna, confirmada na migration.
    chave_nova = dados["identificador_admin"].strip().lower()
    query_existentes = Local.query.filter_by(empresa_id=empresa.id)
    if local_atual is not None:
        query_existentes = query_existentes.filter(Local.id != local_atual.id)
    for outro in query_existentes.all():
        if outro.identificador_admin.strip().lower() == chave_nova:
            return (
                f"Já existe um local com o identificador administrativo '{outro.identificador_admin}' "
                "nesta empresa. Ajuste o identificador para diferenciar (ex.: incluindo a sigla da unidade)."
            )

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
