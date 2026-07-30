from datetime import datetime

from flask import abort, redirect, render_template, request, url_for
from sqlalchemy import func, update

from app.atendimento.helpers import emitir_atualizacao_fila
from app.extensions import db, limiter
from app.models import Fila, LogAtendimento, Senha, Totem, TotemBotao

from . import totem_bp

# app/totem/impressao.py (win32print/ESC-POS, adendo 10.6) não é mais chamado
# a partir daqui - substituído por prévia + window.print() do navegador na
# própria tela totem/senha_gerada.html (ver adendo que supersede a 10.6 no
# BLUEPRINT). O módulo continua existindo no repositório (não apagado, código
# legado) até a nova abordagem estar validada em produção real.

QTD_ACOMPANHANTES_MAXIMO = 6


def _totem_por_slug_ou_404(slug):
    """O slug é único globalmente (todas as empresas) - identifica o totem e, por
    tabela, sua unidade/empresa, sem precisar de um unidade_id separado na URL (ver
    app/utils/slug.py)."""
    totem = Totem.query.filter_by(slug=slug).first_or_404()
    return totem, totem.unidade


def _validar_botao(botao_id, unidade):
    """Garante que o botão pertence à unidade/empresa do totem acessado.

    Mesmo sem login, a tela é pública por unidade - um botão de outra empresa/unidade
    nunca deve ser exposto via manipulação do ID na URL (404, não 403, para não revelar
    que o ID existe em outro tenant).
    """
    botao = TotemBotao.query.get_or_404(botao_id)
    if botao.unidade_id != unidade.id or botao.empresa_id != unidade.empresa_id or not botao.ativo:
        abort(404)
    return botao


def _decorar_botoes(botoes):
    """Classifica cada botão para o template: gera senha, abre submenu ou é um beco sem saída.

    Para os que geram senha, já resolve se a fila precisa de uma tela de escolha de
    tipo (2+ tipos ativos) ou não - permite ao menu.html decidir, sem tela
    intermediária nenhuma, entre linkar para a escolha de tipo ou já disparar a
    geração direto (POST) com o único tipo possível (ou nenhum) - pedido do
    cliente pra tirar o toque extra de "confirmar" do fluxo do totem. A validação
    de verdade (fila aberta, pertence à unidade, etc.) continua só em
    _fila_do_botao_ou_404/gerar_senha - isto aqui é só decisão de UI.
    """
    decorados = []
    for botao in botoes:
        tem_filhos_ativos = any(filho.ativo for filho in botao.filhos)
        gera_senha = botao.fila_id is not None
        tipos_ativos = []
        if gera_senha:
            fila = Fila.query.get(botao.fila_id)
            if fila is not None:
                tipos_ativos = _tipos_ativos(fila)
        decorados.append(
            {
                "botao": botao,
                "gera_senha": gera_senha,
                "tem_filhos": tem_filhos_ativos,
                "precisa_escolher_tipo": len(tipos_ativos) >= 2,
                "tipo_unico": tipos_ativos[0] if len(tipos_ativos) == 1 else None,
            }
        )
    return decorados


def _fila_do_botao_ou_404(botao, unidade, totem):
    if botao.fila_id is None:
        abort(400)
    fila = Fila.query.get_or_404(botao.fila_id)
    if fila.unidade_id != unidade.id or not fila.ativo:
        abort(404)
    # uma fila pode ser restrita a 1 totem específico (fila.totem_id) - se for o caso
    # e não for ESTE totem, ela não pode ser gerada por aqui (NULL = todos os totens).
    if fila.totem_id is not None and fila.totem_id != totem.id:
        abort(404)
    return fila


def _tipos_ativos(fila):
    return sorted((t for t in fila.tipos_senha if t.ativo), key=lambda t: t.nome)


@totem_bp.route("/<int:unidade_id>/<int:totem_id>")
@limiter.limit("60 per minute")
def tela_raiz_por_id(unidade_id, totem_id):
    """URL antiga por ID (pré-slug) - mantida como redirect permanente por segurança
    operacional: totens físicos já configurados com esse link continuam funcionando
    sem precisar reconfigurar o equipamento manualmente."""
    totem = Totem.query.get_or_404(totem_id)
    if totem.unidade_id != unidade_id:
        abort(404)
    return redirect(url_for("totem.tela_raiz", slug=totem.slug), code=302)


@totem_bp.route("/<int:unidade_id>/<int:totem_id>/menu/<int:botao_id>")
@limiter.limit("60 per minute")
def submenu_por_id(unidade_id, totem_id, botao_id):
    totem = Totem.query.get_or_404(totem_id)
    if totem.unidade_id != unidade_id:
        abort(404)
    return redirect(url_for("totem.submenu", slug=totem.slug, botao_id=botao_id), code=302)


@totem_bp.route("/<int:unidade_id>/<int:totem_id>/confirmar/<int:botao_id>")
@limiter.limit("60 per minute")
def confirmar_geracao_por_id(unidade_id, totem_id, botao_id):
    totem = Totem.query.get_or_404(totem_id)
    if totem.unidade_id != unidade_id:
        abort(404)
    return redirect(url_for("totem.confirmar_geracao", slug=totem.slug, botao_id=botao_id), code=302)


@totem_bp.route("/<int:unidade_id>/<int:totem_id>/gerar/<int:botao_id>", methods=["POST"])
@limiter.limit("20 per minute")
def gerar_senha_por_id(unidade_id, totem_id, botao_id):
    totem = Totem.query.get_or_404(totem_id)
    if totem.unidade_id != unidade_id:
        abort(404)
    # 307 (não 302/303): preserva o método POST e o corpo do form no redirect - um
    # 302 aqui faria a maioria dos clientes reenviar como GET, perdendo os dados do form.
    return redirect(url_for("totem.gerar_senha", slug=totem.slug, botao_id=botao_id), code=307)


@totem_bp.route("/<slug>")
@limiter.limit("60 per minute")
def tela_raiz(slug):
    totem, unidade = _totem_por_slug_ou_404(slug)
    if not unidade.ativo:
        return render_template("totem/indisponivel.html", unidade=unidade)
    if not totem.ativo:
        return render_template("totem/indisponivel.html", unidade=unidade)

    botoes = (
        TotemBotao.query.filter_by(
            empresa_id=unidade.empresa_id, unidade_id=unidade.id, parent_id=None, ativo=True
        )
        .order_by(TotemBotao.ordem, TotemBotao.label)
        .all()
    )
    return render_template(
        "totem/menu.html", unidade=unidade, totem=totem, botoes=_decorar_botoes(botoes), botao_pai=None
    )


@totem_bp.route("/<slug>/menu/<int:botao_id>")
@limiter.limit("60 per minute")
def submenu(slug, botao_id):
    totem, unidade = _totem_por_slug_ou_404(slug)
    if not unidade.ativo:
        return render_template("totem/indisponivel.html", unidade=unidade)
    if not totem.ativo:
        return render_template("totem/indisponivel.html", unidade=unidade)

    botao_pai = _validar_botao(botao_id, unidade)
    botoes = (
        TotemBotao.query.filter_by(parent_id=botao_pai.id, ativo=True)
        .order_by(TotemBotao.ordem, TotemBotao.label)
        .all()
    )
    return render_template(
        "totem/menu.html", unidade=unidade, totem=totem, botoes=_decorar_botoes(botoes), botao_pai=botao_pai
    )


@totem_bp.route("/<slug>/confirmar/<int:botao_id>")
@limiter.limit("60 per minute")
def confirmar_geracao(slug, botao_id):
    """Etapa intermediária entre tocar um botão-folha e gerar a senha (seções 10.4/10.5):

    1) se a fila tiver 2+ tipos de senha ativos configurados e nenhum ainda escolhido,
       mostra a tela de escolha de tipo;
    2) senão (0 tipos, ou só 1 - escolhido automaticamente -, ou tipo já escolhido na
       etapa 1), mostra a tela de confirmação, que efetivamente gera a senha ao
       confirmar (pergunta de quantidade de acompanhantes removida do fluxo do totem -
       o campo Senha.qtd_acompanhantes/QTD_ACOMPANHANTES_MAXIMO continua existindo e
       sendo validado em gerar_senha(), só não é mais perguntado ao paciente aqui;
       toda geração passa a gravar 0).
    """
    totem, unidade = _totem_por_slug_ou_404(slug)
    if not unidade.ativo:
        return render_template("totem/indisponivel.html", unidade=unidade)
    if not totem.ativo:
        return render_template("totem/indisponivel.html", unidade=unidade)

    botao = _validar_botao(botao_id, unidade)
    fila = _fila_do_botao_ou_404(botao, unidade, totem)

    if not fila.aberta_em():
        return render_template(
            "totem/erro_configuracao.html", unidade=unidade, totem=totem,
            mensagem="Este atendimento está fora do horário de funcionamento. Procure a recepção.",
        )

    tipos_ativos = _tipos_ativos(fila)

    tipo_escolhido = None
    tipo_senha_id_param = request.args.get("tipo_senha_id", type=int)
    if tipo_senha_id_param is not None:
        tipo_escolhido = next((t for t in tipos_ativos if t.id == tipo_senha_id_param), None)
        if tipo_escolhido is None:
            abort(400)
    elif len(tipos_ativos) == 1:
        tipo_escolhido = tipos_ativos[0]

    if tipo_escolhido is None and len(tipos_ativos) >= 2:
        return render_template(
            "totem/escolher_tipo.html", unidade=unidade, totem=totem, botao=botao, tipos=tipos_ativos,
        )

    return render_template(
        "totem/confirmar_geracao.html",
        unidade=unidade, totem=totem, botao=botao, tipo_escolhido=tipo_escolhido,
    )


@totem_bp.route("/<slug>/gerar/<int:botao_id>", methods=["POST"])
@limiter.limit("20 per minute")
def gerar_senha(slug, botao_id):
    totem, unidade = _totem_por_slug_ou_404(slug)
    if not unidade.ativo:
        return render_template("totem/indisponivel.html", unidade=unidade)
    if not totem.ativo:
        return render_template("totem/indisponivel.html", unidade=unidade)

    botao = _validar_botao(botao_id, unidade)
    fila = _fila_do_botao_ou_404(botao, unidade, totem)

    if not fila.aberta_em():
        return render_template(
            "totem/erro_configuracao.html", unidade=unidade, totem=totem,
            mensagem="Este atendimento está fora do horário de funcionamento. Procure a recepção.",
        )

    tipos_ativos = _tipos_ativos(fila)
    tipo_senha_id = request.form.get("tipo_senha_id", type=int)
    tipo_escolhido = None
    if tipo_senha_id is not None:
        tipo_escolhido = next((t for t in tipos_ativos if t.id == tipo_senha_id), None)
        if tipo_escolhido is None:
            abort(400)

    qtd_acompanhantes = request.form.get("qtd_acompanhantes", type=int) or 0
    if not (0 <= qtd_acompanhantes <= QTD_ACOMPANHANTES_MAXIMO):
        abort(400)

    senha = _emitir_senha(fila, unidade, tipo_senha=tipo_escolhido, qtd_acompanhantes=qtd_acompanhantes)

    # Impressão física (antes: win32print/ESC-POS em background, seção 10.6) -
    # agora é responsabilidade da própria tela (totem/senha_gerada.html), via
    # prévia + window.print() do navegador, disparada só quando o usuário toca
    # em "Imprimir". A senha já está gravada e válida acima, independente de
    # alguém confirmar a impressão ou não.

    return render_template("totem/senha_gerada.html", unidade=unidade, totem=totem, senha=senha, fila=fila)


def _compor_codigo(fila, tipo_senha, proximo_sequencial):
    """Regra de composição do código (seção 10.4): sigla da fila + prefixo do tipo
    escolhido (se houver) + sequencial. O prefixo de um tipo "padrão" (peso_prioridade
    == 0, ex.: "Normal") só aparece se a fila estiver configurada para exibi-lo
    (`exibir_prefixo_tipo_quando_normal`) - evita redundância tipo "LABN001" por
    padrão, mas sempre mostra o prefixo de tipos com peso > 0 (ex.: "Idoso" -> LABI001)."""
    prefixo_efetivo = ""
    if tipo_senha is not None and tipo_senha.prefixo:
        if tipo_senha.peso_prioridade > 0 or fila.exibir_prefixo_tipo_quando_normal:
            prefixo_efetivo = tipo_senha.prefixo
    return f"{fila.sigla}{prefixo_efetivo}{proximo_sequencial:0{fila.digitos_sequencial}d}"


def _emitir_senha(fila, unidade, tipo_senha=None, qtd_acompanhantes=0):
    """Calcula o próximo sequencial e grava a senha + log 'gerada', tudo em uma única transação.

    Trava a linha da fila antes de ler o maior sequencial já emitido - sob MySQL/InnoDB
    isso serializa toques simultâneos na mesma fila/unidade, evitando que dois pacientes
    recebam o mesmo código (seção 8 do BLUEPRINT). A trava é liberada no commit ao final
    desta função.

    Usamos um `UPDATE` real (em vez de um simples `SELECT ... FOR UPDATE`) porque o
    SQLite - usado nos testes deste projeto por não haver um servidor MySQL disponível
    no ambiente de desenvolvimento - trata `FOR UPDATE` como no-op (SQLAlchemy descarta
    a cláusula silenciosamente), então um `SELECT` sozinho não trava nada de fato ali.
    Um `UPDATE` força a aquisição de um lock de escrita real tanto no SQLite quanto no
    MySQL/InnoDB, com o mesmo efeito de serialização em ambos.
    """
    db.session.execute(update(Fila).where(Fila.id == fila.id).values(ativo=Fila.ativo))

    consulta_sequencial = db.session.query(func.max(Senha.sequencial)).filter(
        Senha.fila_id == fila.id,
        Senha.unidade_id == unidade.id,
    )
    if fila.zera_diariamente:
        # `criada_em` é gravado em UTC (default=datetime.utcnow no model) - comparar com a
        # data UTC atual, nunca com a data local, para não zerar o sequencial no horário errado
        # perto da meia-noite.
        consulta_sequencial = consulta_sequencial.filter(
            func.date(Senha.criada_em) == datetime.utcnow().date()
        )

    ultimo_sequencial = consulta_sequencial.scalar() or 0
    proximo_sequencial = ultimo_sequencial + 1
    codigo = _compor_codigo(fila, tipo_senha, proximo_sequencial)

    # seção 10.4: o peso_prioridade do tipo escolhido alimenta o rodízio de prioridade já
    # existente - sem tipo escolhido, mantém o comportamento original (prioridade_padrao).
    prioridade = (tipo_senha.peso_prioridade > 0) if tipo_senha is not None else (fila.prioridade_padrao > 0)

    senha = Senha(
        empresa_id=unidade.empresa_id,
        unidade_id=unidade.id,
        fila_id=fila.id,
        codigo=codigo,
        sequencial=proximo_sequencial,
        prioridade=prioridade,
        status="aguardando",
        tipo_senha_id=tipo_senha.id if tipo_senha is not None else None,
        qtd_acompanhantes=qtd_acompanhantes,
    )
    db.session.add(senha)
    db.session.flush()  # garante senha.id antes de gravar o log

    db.session.add(LogAtendimento(senha_id=senha.id, evento="gerada", unidade_id=unidade.id))
    db.session.commit()

    # Bug corrigido: gerar a senha aqui nunca avisava as telas de atendimento -
    # só as ações do próprio atendente (Chamar/Realocar/Finalizar/Aguardar,
    # helpers.py) disparavam 'fila_atualizada'. Sem isso, uma senha nova só
    # aparecia em 'Senhas em espera' depois de um F5 manual, tanto na tela
    # por-fila (recepção) quanto na tela por-Local (coleta/rodízio, adendo 10.8) -
    # ambas escutam o mesmo evento, então um único emit aqui cobre as duas.
    emitir_atualizacao_fila(fila.id)

    return senha
