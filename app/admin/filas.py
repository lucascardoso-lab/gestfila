import re

from flask import flash, redirect, render_template, request, url_for

from app.admin import admin_bp
from app.admin.helpers import (
    autorizar_objeto,
    resolver_empresa_contexto,
    resolver_unidade_contexto,
    tipos_senha_da_empresa,
    totens_da_empresa,
    unidades_da_empresa,
)
from app.extensions import db
from app.models import Empresa, Fila, LocalRodizioFila, Senha, TotemBotao, Tv
from app.models.associations import local_filas_destino
from app.models.fila import DIAS_SEMANA

SIGLA_RE = re.compile(r"^[A-Z]{2,4}$")


def _tvs_da_empresa(empresa):
    return Tv.query.filter_by(empresa_id=empresa.id).order_by(Tv.nome).all()


@admin_bp.route("/filas")
def listar_filas():
    empresa = resolver_empresa_contexto()
    if empresa is None:
        return render_template(
            "admin/selecionar_empresa.html",
            empresas=Empresa.query.order_by(Empresa.nome).all(),
            destino="admin.listar_filas",
        )

    unidade = resolver_unidade_contexto(empresa)
    query = Fila.query.filter_by(empresa_id=empresa.id)
    if unidade is not None:
        query = query.filter_by(unidade_id=unidade.id)
    filas = query.order_by(Fila.nome).all()
    return render_template("admin/filas/listar.html", empresa=empresa, unidade=unidade, filas=filas)


@admin_bp.route("/filas/nova", methods=["GET", "POST"])
def nova_fila():
    empresa = resolver_empresa_contexto()
    if empresa is None:
        flash("Selecione uma empresa antes de cadastrar uma fila.", "error")
        return redirect(url_for("admin.listar_filas"))

    unidades = unidades_da_empresa(empresa)
    if not unidades:
        flash("Cadastre ao menos uma unidade antes de criar uma fila.", "error")
        return redirect(url_for("admin.listar_filas", empresa_id=empresa.id))

    totens = totens_da_empresa(empresa)
    tvs = _tvs_da_empresa(empresa)
    tipos_senha = tipos_senha_da_empresa(empresa)

    if request.method == "POST":
        dados = _ler_formulario()
        erro = _validar_fila(dados, unidades, totens, tvs, fila_atual=None)
        if erro:
            flash(erro, "error")
            return render_template(
                "admin/filas/form.html", empresa=empresa, fila=None, unidades=unidades,
                totens=totens, tvs=tvs, tipos_senha=tipos_senha, valores=dados, dias_semana=DIAS_SEMANA,
            )

        fila = Fila(
            empresa_id=empresa.id,
            unidade_id=dados["unidade_id"],
            nome=dados["nome"],
            sigla=dados["sigla"],
            digitos_sequencial=dados["digitos_sequencial"],
            zera_diariamente=dados["zera_diariamente"],
            prioridade_padrao=dados["prioridade_padrao"],
            prioridade_rodizio=dados["prioridade_rodizio"],
            totem_id=dados["totem_id"],
            horario_funcionamento=dados["horario_funcionamento"],
            tempo_espera_maximo_min=dados["tempo_espera_maximo_min"],
            tempo_atendimento_maximo_min=dados["tempo_atendimento_maximo_min"],
            alerta_email=dados["alerta_email"],
            exibir_prefixo_tipo_quando_normal=dados["exibir_prefixo_tipo_quando_normal"],
        )
        db.session.add(fila)
        db.session.flush()  # garante fila.id para o atalho de TV abaixo
        _sincronizar_tv_atalho(fila, tv_id_anterior=None, tv_id_novo=dados["tv_id"], tvs=tvs)
        fila.tv_id = dados["tv_id"]
        _sincronizar_tipos_senha(fila, dados["tipo_senha_ids"], tipos_senha)
        db.session.commit()
        flash(f"Fila '{fila.nome}' criada.", "info")
        return redirect(url_for("admin.listar_filas", empresa_id=empresa.id))

    return render_template(
        "admin/filas/form.html", empresa=empresa, fila=None, unidades=unidades,
        totens=totens, tvs=tvs, tipos_senha=tipos_senha, valores=None, dias_semana=DIAS_SEMANA,
    )


@admin_bp.route("/filas/<int:fila_id>/editar", methods=["GET", "POST"])
def editar_fila(fila_id):
    fila = Fila.query.get_or_404(fila_id)
    autorizar_objeto(fila)
    empresa = Empresa.query.get_or_404(fila.empresa_id)
    unidades = unidades_da_empresa(empresa)
    totens = totens_da_empresa(empresa)
    tvs = _tvs_da_empresa(empresa)
    tipos_senha = tipos_senha_da_empresa(empresa)

    if request.method == "POST":
        dados = _ler_formulario()
        erro = _validar_fila(dados, unidades, totens, tvs, fila_atual=fila)
        if erro:
            flash(erro, "error")
            return render_template(
                "admin/filas/form.html", empresa=empresa, fila=fila, unidades=unidades,
                totens=totens, tvs=tvs, tipos_senha=tipos_senha, valores=dados, dias_semana=DIAS_SEMANA,
            )

        tv_id_anterior = fila.tv_id
        fila.unidade_id = dados["unidade_id"]
        fila.nome = dados["nome"]
        fila.sigla = dados["sigla"]
        fila.digitos_sequencial = dados["digitos_sequencial"]
        fila.zera_diariamente = dados["zera_diariamente"]
        fila.prioridade_padrao = dados["prioridade_padrao"]
        fila.prioridade_rodizio = dados["prioridade_rodizio"]
        fila.totem_id = dados["totem_id"]
        fila.horario_funcionamento = dados["horario_funcionamento"]
        fila.tempo_espera_maximo_min = dados["tempo_espera_maximo_min"]
        fila.tempo_atendimento_maximo_min = dados["tempo_atendimento_maximo_min"]
        fila.alerta_email = dados["alerta_email"]
        fila.exibir_prefixo_tipo_quando_normal = dados["exibir_prefixo_tipo_quando_normal"]
        _sincronizar_tv_atalho(fila, tv_id_anterior=tv_id_anterior, tv_id_novo=dados["tv_id"], tvs=tvs)
        fila.tv_id = dados["tv_id"]
        _sincronizar_tipos_senha(fila, dados["tipo_senha_ids"], tipos_senha)
        db.session.commit()
        flash("Fila atualizada.", "info")
        return redirect(url_for("admin.listar_filas", empresa_id=empresa.id))

    return render_template(
        "admin/filas/form.html", empresa=empresa, fila=fila, unidades=unidades,
        totens=totens, tvs=tvs, tipos_senha=tipos_senha, valores=None, dias_semana=DIAS_SEMANA,
    )


@admin_bp.route("/filas/<int:fila_id>/toggle-ativo", methods=["POST"])
def alternar_fila(fila_id):
    fila = Fila.query.get_or_404(fila_id)
    autorizar_objeto(fila)

    fila.ativo = not fila.ativo
    db.session.commit()
    flash(f"Fila {'ativada' if fila.ativo else 'desativada'}.", "info")
    return redirect(url_for("admin.listar_filas", empresa_id=fila.empresa_id))


@admin_bp.route("/filas/<int:fila_id>/excluir", methods=["POST"])
def excluir_fila(fila_id):
    """Exclusão segura (não confundir com desativar, acima): só apaga de verdade quando
    não há NENHUM vínculo/histórico - nunca apaga Senha/LogAtendimento (seção 8 do
    BLUEPRINT: "nunca apagar senhas/logs_atendimento"). Uma fila que já teve qualquer
    senha gerada fica permanentemente protegida por esse motivo; use Desativar nesse caso."""
    fila = Fila.query.get_or_404(fila_id)
    autorizar_objeto(fila)

    bloqueios = []
    if Senha.query.filter_by(fila_id=fila.id).first():
        bloqueios.append("senhas já geradas nesta fila (histórico)")
    if TotemBotao.query.filter_by(fila_id=fila.id).first():
        bloqueios.append("botões do totem vinculados a ela")

    if bloqueios:
        flash(
            f"Não é possível excluir '{fila.nome}': existem {' e '.join(bloqueios)}. "
            "Desative a fila em vez de excluir.", "error",
        )
        return redirect(url_for("admin.listar_filas", empresa_id=fila.empresa_id))

    empresa_id, nome = fila.empresa_id, fila.nome
    # vínculos puramente de configuração (não são histórico/auditoria) - limpos antes
    # de excluir, já que nenhuma FK do projeto tem ondelete=CASCADE no banco.
    fila.tvs = []
    fila.tipos_senha = []
    db.session.execute(local_filas_destino.delete().where(local_filas_destino.c.fila_id == fila.id))
    LocalRodizioFila.query.filter_by(fila_id=fila.id).delete()
    db.session.delete(fila)
    db.session.commit()
    flash(f"Fila '{nome}' excluída.", "info")
    return redirect(url_for("admin.listar_filas", empresa_id=empresa_id))


def _sincronizar_tv_atalho(fila, tv_id_anterior, tv_id_novo, tvs):
    """`tv_id` é um atalho de cadastro rápido (seção 10.1) que popula `tv_filas` por
    trás - manter o relacionamento N:N coerente quando o campo muda, sem afetar
    outras associações feitas diretamente na tela de TVs."""
    if tv_id_anterior == tv_id_novo:
        return
    if tv_id_anterior is not None:
        tv_antiga = next((t for t in tvs if t.id == tv_id_anterior), None)
        if tv_antiga is not None and fila in tv_antiga.filas:
            tv_antiga.filas.remove(fila)
    if tv_id_novo is not None:
        tv_nova = next((t for t in tvs if t.id == tv_id_novo), None)
        if tv_nova is not None and fila not in tv_nova.filas:
            tv_nova.filas.append(fila)


def _sincronizar_tipos_senha(fila, tipo_senha_ids, tipos_senha):
    """Sincroniza `fila.tipos_senha` (M2M, seção 10.4) com o conjunto marcado no
    formulário - substitui a lista inteira a cada salvamento, mais simples do que
    calcular diff de adições/remoções, e o volume de tipos por fila é pequeno."""
    validos = {t.id for t in tipos_senha}
    fila.tipos_senha = [t for t in tipos_senha if t.id in (tipo_senha_ids & validos)]


def _ler_formulario():
    horario = {
        dia: {
            "manha": request.form.get(f"horario_{dia}_manha") == "on",
            "tarde": request.form.get(f"horario_{dia}_tarde") == "on",
        }
        for dia in DIAS_SEMANA
    }
    return {
        "nome": request.form.get("nome", "").strip(),
        "unidade_id": request.form.get("unidade_id", type=int),
        "sigla": request.form.get("sigla", "").strip().upper(),
        "digitos_sequencial": request.form.get("digitos_sequencial", type=int) or 3,
        "zera_diariamente": request.form.get("zera_diariamente") == "on",
        "prioridade_padrao": request.form.get("prioridade_padrao", type=int) or 0,
        "prioridade_rodizio": request.form.get("prioridade_rodizio", type=int) or 0,
        "totem_id": request.form.get("totem_id", type=int) or None,
        "tv_id": request.form.get("tv_id", type=int) or None,
        "horario_funcionamento": horario,
        "tempo_espera_maximo_min": request.form.get("tempo_espera_maximo_min", type=int) or None,
        "tempo_atendimento_maximo_min": request.form.get("tempo_atendimento_maximo_min", type=int) or None,
        "alerta_email": request.form.get("alerta_email") == "on",
        "exibir_prefixo_tipo_quando_normal": request.form.get("exibir_prefixo_tipo_quando_normal") == "on",
        "tipo_senha_ids": {int(v) for v in request.form.getlist("tipo_senha_ids")},
    }


def _validar_fila(dados, unidades, totens, tvs, fila_atual):
    if not dados["nome"]:
        return "Informe o nome da fila."

    if dados["unidade_id"] not in {u.id for u in unidades}:
        return "Selecione uma unidade válida."

    if not SIGLA_RE.match(dados["sigla"]):
        return "A sigla deve ter de 2 a 4 letras (A-Z)."

    if not (1 <= dados["digitos_sequencial"] <= 4):
        return "Dígitos do sequencial deve ser um número entre 1 e 4."

    if not (0 <= dados["prioridade_padrao"] <= 9):
        return "Prioridade padrão deve ser um número entre 0 e 9."

    if not (0 <= dados["prioridade_rodizio"] <= 20):
        return "Quantidade de chamadas seguidas (rodízio) deve ser um número entre 0 e 20."

    query = Fila.query.filter_by(unidade_id=dados["unidade_id"], sigla=dados["sigla"])
    if fila_atual:
        query = query.filter(Fila.id != fila_atual.id)
    if query.first():
        return "Já existe uma fila com essa sigla nessa unidade."

    if dados["totem_id"] is not None:
        totem = next((t for t in totens if t.id == dados["totem_id"]), None)
        if totem is None:
            return "Selecione um totem válido (ou nenhum, para disponibilizar em todos)."
        if totem.unidade_id != dados["unidade_id"]:
            return "O totem selecionado pertence a outra unidade."

    if dados["tv_id"] is not None and dados["tv_id"] not in {t.id for t in tvs}:
        return "Selecione uma TV válida (ou nenhuma)."

    for campo in ("tempo_espera_maximo_min", "tempo_atendimento_maximo_min"):
        if dados[campo] is not None and dados[campo] <= 0:
            return "Tempos máximos, quando informados, devem ser maiores que zero (em minutos)."

    return None
