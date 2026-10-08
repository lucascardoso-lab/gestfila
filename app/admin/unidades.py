import re
from collections import Counter, defaultdict

from flask import flash, redirect, render_template, request, url_for
from sqlalchemy.exc import IntegrityError

from app.admin import admin_bp
from app.admin.helpers import autorizar_objeto, resolver_empresa_contexto
from app.extensions import db
from app.models import Empresa, Local, Unidade
from app.utils.rotulos import chave_colisao

# Adendo 10.71: só letras A-Z (sem acento) e dígitos, 2 a 4 caracteres, gravada
# em maiúsculas. O fallback automático (Unidade.sigla_exibicao) pode ter acento
# ("ANÁ") - só a sigla CADASTRADA segue este formato.
SIGLA_VALIDA = re.compile(r"^[A-Z0-9]{2,4}$")

# "[XXX] resto" no início de Local.identificador_admin. Até 4 caracteres entre
# colchetes - o tamanho de uma sigla (cadastrada ou fallback de 3 letras) -, pra
# não confundir com um identificador editado à mão do tipo "[Térreo] Guichê 1".
PREFIXO_IDENTIFICADOR = re.compile(r"^\[([^\]]{1,4})\] ")


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

    # Siglas automáticas (ou cadastradas) iguais entre unidades - mesma
    # comparação dos rótulos de fila (chave_colisao): o admin vê o que
    # precisa cadastrar pra acabar com o " (id)" nos rótulos (10.69/10.70).
    por_sigla = defaultdict(list)
    for unidade in unidades:
        por_sigla[chave_colisao(unidade.sigla_exibicao)].append(unidade)
    colisoes = {
        unidade.id: [outra.nome for outra in grupo if outra.id != unidade.id]
        for grupo in por_sigla.values() if len(grupo) > 1
        for unidade in grupo
    }

    return render_template(
        "admin/unidades/listar.html", empresa=empresa, unidades=unidades,
        colisoes_sigla=colisoes, pendencias_prefixo=_locais_com_prefixo_divergente(empresa.id),
    )


@admin_bp.route("/unidades/nova", methods=["GET", "POST"])
def nova_unidade():
    empresa = resolver_empresa_contexto()
    if empresa is None:
        flash("Selecione uma empresa antes de cadastrar uma unidade.", "error")
        return redirect(url_for("admin.listar_unidades"))

    if request.method == "POST":
        dados = _ler_formulario()
        erro = _validar_unidade(dados, empresa, unidade_atual=None)
        if erro:
            flash(erro, "error")
            return render_template("admin/unidades/form.html", empresa=empresa, unidade=None, valores=dados)

        unidade = Unidade(empresa_id=empresa.id, **dados)
        db.session.add(unidade)
        if not _commit_sigla_unica(dados):
            return render_template("admin/unidades/form.html", empresa=empresa, unidade=None, valores=dados)
        flash(f"Unidade '{unidade.nome}' criada.", "info")
        return redirect(url_for("admin.listar_unidades", empresa_id=empresa.id))

    return render_template("admin/unidades/form.html", empresa=empresa, unidade=None, valores=None)


@admin_bp.route("/unidades/<int:unidade_id>/editar", methods=["GET", "POST"])
def editar_unidade(unidade_id):
    unidade = Unidade.query.get_or_404(unidade_id)
    autorizar_objeto(unidade)
    empresa = unidade.empresa

    if request.method == "POST":
        dados = _ler_formulario()
        erro = _validar_unidade(dados, empresa, unidade_atual=unidade)
        if erro:
            flash(erro, "error")
            return _render_form_edicao(empresa, unidade, valores=dados)

        sigla_antiga = unidade.sigla_exibicao
        for campo, valor in dados.items():
            setattr(unidade, campo, valor)
        if not _commit_sigla_unica(dados):
            return _render_form_edicao(empresa, unidade, valores=dados)

        # Sigla EFETIVA mudou (cadastrar, trocar, limpar - ou renomear uma
        # unidade sem sigla, que muda o fallback): a unidade já está salva;
        # os Locais só mudam depois da confirmação explícita na prévia.
        if unidade.sigla_exibicao != sigla_antiga and _locais_com_prefixo(unidade, sigla_antiga):
            flash(
                f"Unidade atualizada. Alguns locais desta unidade ainda usam o prefixo "
                f"[{sigla_antiga}] - revise abaixo se quer atualizá-los para [{unidade.sigla_exibicao}].",
                "info",
            )
            return redirect(url_for("admin.prefixo_locais_unidade", unidade_id=unidade.id, prefixo_antigo=sigla_antiga))

        flash("Unidade atualizada.", "info")
        return redirect(url_for("admin.listar_unidades", empresa_id=empresa.id))

    return _render_form_edicao(empresa, unidade, valores=None)


@admin_bp.route("/unidades/<int:unidade_id>/prefixo-locais", methods=["GET", "POST"])
def prefixo_locais_unidade(unidade_id):
    """Prévia (GET) e confirmação (POST) da troca de prefixo dos Locais da
    unidade (adendo 10.71). O POST RECALCULA o plano a partir do banco - nunca
    confia no que a prévia mostrou - e grava tudo ou nada."""
    unidade = Unidade.query.get_or_404(unidade_id)
    autorizar_objeto(unidade)
    empresa = unidade.empresa
    prefixo_antigo = (request.form if request.method == "POST" else request.args).get("prefixo_antigo", "")

    itens = _plano_prefixo_locais(unidade, prefixo_antigo)
    if not itens:
        flash(f"Nenhum local desta unidade usa o prefixo [{prefixo_antigo}] para atualizar.", "info")
        return redirect(url_for("admin.listar_unidades", empresa_id=empresa.id))

    if request.method == "POST":
        colisoes = [item for item in itens if item["conflito"]]
        if colisoes:
            flash(
                "Nenhum local foi atualizado: "
                + "; ".join(f"'{item['depois']}' já é usado {item['conflito']}" for item in colisoes)
                + ". Ajuste esses locais à mão (ou o outro local) e tente de novo.",
                "error",
            )
            return _render_prefixo(empresa, unidade, prefixo_antigo, itens)

        for item in itens:
            item["local"].identificador_admin = item["depois"]
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("Nenhum local foi atualizado: outro identificador igual foi salvo nesse meio-tempo. Tente de novo.", "error")
            return redirect(url_for("admin.prefixo_locais_unidade", unidade_id=unidade.id, prefixo_antigo=prefixo_antigo))
        flash(f"{len(itens)} local(is) atualizado(s) para o prefixo [{unidade.sigla_exibicao}].", "info")
        return redirect(url_for("admin.listar_unidades", empresa_id=empresa.id))

    return _render_prefixo(empresa, unidade, prefixo_antigo, itens)


@admin_bp.route("/unidades/<int:unidade_id>/toggle-ativo", methods=["POST"])
def alternar_unidade(unidade_id):
    unidade = Unidade.query.get_or_404(unidade_id)
    autorizar_objeto(unidade)

    unidade.ativo = not unidade.ativo
    db.session.commit()
    flash(f"Unidade {'ativada' if unidade.ativo else 'desativada'}.", "info")
    return redirect(url_for("admin.listar_unidades", empresa_id=unidade.empresa_id))


def _render_form_edicao(empresa, unidade, valores):
    return render_template(
        "admin/unidades/form.html", empresa=empresa, unidade=unidade, valores=valores,
        esconder_seletor_contexto=True,
        pendencias_prefixo=_locais_com_prefixo_divergente(empresa.id).get(unidade.id, []),
    )


def _render_prefixo(empresa, unidade, prefixo_antigo, itens):
    return render_template(
        "admin/unidades/prefixo_locais.html", empresa=empresa, unidade=unidade,
        prefixo_antigo=prefixo_antigo, itens=itens, esconder_seletor_contexto=True,
    )


def _ler_formulario():
    return {
        "nome": request.form.get("nome", "").strip(),
        "sigla": request.form.get("sigla", "").strip().upper() or None,
        "endereco": request.form.get("endereco", "").strip() or None,
        "telefone": request.form.get("telefone", "").strip() or None,
    }


def _validar_unidade(dados, empresa, unidade_atual):
    if not dados["nome"]:
        return "Informe o nome da unidade."

    sigla = dados["sigla"]
    if sigla is not None and not SIGLA_VALIDA.match(sigla):
        return "A sigla deve ter de 2 a 4 caracteres, só letras de A a Z (sem acento) e números."

    efetiva = Unidade.sigla_efetiva(sigla, dados["nome"])
    # Sem sigla e com a sigla efetiva inalterada (ex.: editar o endereço de
    # uma das duas "Anápolis" que já colidem hoje no fallback): nada novo a
    # validar - a colisão já existia e é justamente o que o admin vai
    # resolver cadastrando uma sigla. Sigla digitada é SEMPRE validada.
    efetiva_inalterada = unidade_atual is not None and efetiva == unidade_atual.sigla_exibicao
    if sigla is None and efetiva_inalterada:
        return None

    outras = Unidade.query.filter_by(empresa_id=empresa.id)
    if unidade_atual is not None:
        outras = outras.filter(Unidade.id != unidade_atual.id)
    for outra in outras.all():
        if chave_colisao(outra.sigla_exibicao) != chave_colisao(efetiva):
            continue
        if sigla is not None:
            if outra.sigla:
                return f"A sigla {sigla} já é usada pela unidade {outra.nome}."
            return (
                f"A sigla {sigla} já é usada (automaticamente) pela unidade {outra.nome}. "
                f"Cadastre primeiro uma sigla para {outra.nome}."
            )
        if unidade_atual is not None and unidade_atual.sigla:
            return (
                f"Não é possível remover a sigla: sem ela, esta unidade usaria a sigla automática "
                f"{efetiva}, que já é usada pela unidade {outra.nome}."
            )
        return (
            f"Sem sigla, esta unidade usaria a sigla automática {efetiva}, que já é usada pela "
            f"unidade {outra.nome}. Cadastre uma sigla para esta unidade."
        )
    return None


def _commit_sigla_unica(dados):
    """Commit protegido pela UniqueConstraint (empresa_id, sigla) - só dispara
    numa corrida entre dois admins salvando ao mesmo tempo (a validação acima
    já cobre o caso normal)."""
    try:
        db.session.commit()
        return True
    except IntegrityError:
        db.session.rollback()
        flash(f"A sigla {dados['sigla']} acabou de ser cadastrada em outra unidade desta empresa.", "error")
        return False


def _locais_com_prefixo(unidade, prefixo):
    inicio = f"[{prefixo}] "
    return [
        local for local in Local.query.filter_by(unidade_id=unidade.id).order_by(Local.identificador_admin).all()
        if local.identificador_admin.startswith(inicio)
    ]


def _plano_prefixo_locais(unidade, prefixo_antigo):
    """Lista de {local, antes, depois, conflito} para trocar "[prefixo_antigo] "
    por "[sigla efetiva atual] " nos Locais da unidade - calculada do banco a
    cada chamada (prévia e confirmação).

    - Só o prefixo exato muda; o texto depois dele nunca é tocado (há
      identificadores editados à mão).
    - O sufixo " (<id do próprio local>)" - desempate gerado pela migração do
      10.32 - sai só se o resultado sem ele continuar único na empresa; qualquer
      outro parêntese no fim fica.
    - `conflito`: o que impediria gravar (outro local da empresa, ou outro item
      desta mesma lista, com o mesmo identificador - comparação case/accent-
      insensitive, a mesma da constraint do banco)."""
    prefixo_novo = unidade.sigla_exibicao
    if not prefixo_antigo or prefixo_antigo == prefixo_novo:
        return []
    alvo = _locais_com_prefixo(unidade, prefixo_antigo)
    if not alvo:
        return []

    ids_alvo = {local.id for local in alvo}
    ocupados = {
        chave_colisao(local.identificador_admin): local.identificador_admin
        for local in Local.query.filter_by(empresa_id=unidade.empresa_id).all()
        if local.id not in ids_alvo
    }
    inicio = f"[{prefixo_antigo}] "
    depois = {local.id: f"[{prefixo_novo}] " + local.identificador_admin[len(inicio):] for local in alvo}

    for local in alvo:
        sufixo = f" ({local.id})"
        if not depois[local.id].endswith(sufixo):
            continue
        sem_sufixo = depois[local.id][: -len(sufixo)]
        chaves_da_lista = {chave_colisao(v) for k, v in depois.items() if k != local.id}
        if chave_colisao(sem_sufixo) not in ocupados and chave_colisao(sem_sufixo) not in chaves_da_lista:
            depois[local.id] = sem_sufixo

    repetidos = Counter(chave_colisao(v) for v in depois.values())
    itens = []
    for local in alvo:
        chave = chave_colisao(depois[local.id])
        if chave in ocupados:
            conflito = f"pelo local '{ocupados[chave]}'"
        elif repetidos[chave] > 1:
            conflito = "por outro local desta mesma lista"
        else:
            conflito = None
        itens.append({"local": local, "antes": local.identificador_admin, "depois": depois[local.id], "conflito": conflito})
    return itens


def _locais_com_prefixo_divergente(empresa_id):
    """Aviso passivo (adendo 10.71): {unidade_id: [(prefixo, [locais])]} dos
    Locais cujo identificador_admin começa com "[X] " e X não é a sigla
    efetiva atual da unidade - o que sobrou de um "Agora não" na prévia, ou de
    uma sigla trocada direto no banco."""
    pendencias = defaultdict(lambda: defaultdict(list))
    for local in Local.query.filter_by(empresa_id=empresa_id).order_by(Local.identificador_admin).all():
        m = PREFIXO_IDENTIFICADOR.match(local.identificador_admin)
        if m and m.group(1) != local.unidade.sigla_exibicao:
            pendencias[local.unidade_id][m.group(1)].append(local)
    return {unidade_id: sorted(grupos.items()) for unidade_id, grupos in pendencias.items()}
