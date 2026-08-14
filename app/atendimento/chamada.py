from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user

from app.extensions import db
from app.models import Senha

from . import atendimento_bp
from .helpers import (
    emitir_atualizacao_fila,
    exigir_acesso_setor,
    fila_acessivel_ou_404,
    fila_ids_do_local,
    filas_destino,
    local_acessivel_ou_404,
    local_atual,
    registrar_chamada_senha,
    registrar_fim_atendimento,
    registrar_realocacao,
    registrar_toque,
    registrar_volta_espera,
    selecionar_proxima_fila_e_senha,
    selecionar_proxima_senha,
    senha_acessivel_ou_404,
    senha_atual_no_local,
    senhas_aguardando,
    senhas_aguardando_local,
    senhas_atendidas_no_local,
    talvez_atualizar_nome,
    travar_fila,
)


def _exigir_local_ou_redirecionar(proximo_endpoint, **proximo_valores):
    """Seção 10.3: sem Local escolhido na sessão, nenhuma ação de atendimento roda -
    manda o operador escolher um antes, e volta exatamente para onde ele estava."""
    local = local_atual()
    if local is not None:
        return local, None
    destino = url_for("atendimento." + proximo_endpoint, **proximo_valores)
    return None, redirect(url_for("atendimento.escolher_local", next=destino))


def _serializar_espera(senha):
    return {
        "id": senha.id,
        "codigo": senha.codigo,
        "criada_em": senha.criada_em.isoformat(),
        "nome_paciente": senha.nome_paciente,
        "observacoes": senha.observacoes,
        "prioridade": senha.prioridade,
    }


def _serializar_atendida(senha):
    tempo_espera_segundos = None
    if senha.chamada_em is not None:
        tempo_espera_segundos = round((senha.chamada_em - senha.criada_em).total_seconds())
    return {
        "id": senha.id,
        "codigo": senha.codigo,
        "tempo_espera_segundos": tempo_espera_segundos,
        "chamada_em": senha.chamada_em.isoformat() if senha.chamada_em else None,
        "local": senha.local.nome_exibicao if senha.local else None,
    }


def _serializar_senha_atual(senha):
    if senha is None:
        return None
    return {
        "id": senha.id,
        "codigo": senha.codigo,
        "nome_paciente": senha.nome_paciente,
        "local": senha.local.nome_exibicao if senha.local else None,
        "chamada_em": senha.chamada_em.isoformat() if senha.chamada_em else None,
    }


def _serializar_espera_local(senha):
    """Mesmo formato de _serializar_espera, mais o nome da fila - necessário na tela
    por-Local (adendo 10.8) porque a lista agrega candidatas de várias filas, ao
    contrário da tela por-fila (que já sabe de qual fila é, sem precisar repetir).

    `tempo_espera_maximo_min` também vai por linha aqui (diferente da tela por-fila,
    que usa uma constante JS única - todas as linhas são da MESMA fila lá) - o
    destaque de tempo de espera (escala de 3 níveis) precisa do limite da fila
    ESPECÍFICA de cada senha, já que o rodízio agrega candidatas de várias filas
    com limites potencialmente diferentes."""
    dados = _serializar_espera(senha)
    dados["fila"] = senha.fila.nome
    dados["tempo_espera_maximo_min"] = senha.fila.tempo_espera_maximo_min
    return dados


def _serializar_atendida_local(senha):
    dados = _serializar_atendida(senha)
    dados["fila"] = senha.fila.nome
    return dados


def _serializar_senha_atual_local(senha):
    """Mesmo formato de _serializar_senha_atual, mais o nome da fila - a tela por-fila
    já sabe de qual fila é (fixa na URL); a tela por-Local (adendo 10.8) precisa saber
    qual das N filas do rodízio foi a chamada, tanto no primeiro render quanto após
    cada refresh via WebSocket (Fase 10.8-E)."""
    dados = _serializar_senha_atual(senha)
    if dados is not None:
        dados["fila"] = senha.fila.nome
    return dados


@atendimento_bp.route("/<any(recepcao,coleta):setor>/local/<int:local_id>")
def painel_local(setor, local_id):
    """Tela unificada de Atendimento por Local (adendo 10.8) - agrega 'Senha chamada'/
    'Senhas em espera'/'Senhas atendidas' de TODAS as filas do rodízio configurado no
    Local (Local.rodizio_ordenado), em vez de uma única fila (seção 10.2). Coexiste
    com painel_fila - nenhuma mudança lá. Local sem rodízio configurado
    (rodizio_ordenado vazio) renderiza a tela vazia, sem erro.

    Fase 10.8-E: bloco Redirecionar usa filas_destino(senha_atual.fila, local) - a
    fila DE ORIGEM da senha chamada no momento, que pode ser qualquer uma do rodízio
    (por isso calculado aqui, não fixo como na tela por-fila). Sem senha chamada,
    fica vazio (nada pra redirecionar ainda).
    """
    exigir_acesso_setor(setor)
    local = local_acessivel_ou_404(local_id)

    senha_atual = senha_atual_no_local(local, current_user)
    filas_destino_atual = filas_destino(senha_atual.fila, local) if senha_atual else []

    return render_template(
        "atendimento/painel_local.html",
        setor=setor,
        local=local,
        senha_atual=senha_atual,
        filas_destino=filas_destino_atual,
        espera=senhas_aguardando_local(local, current_user),
        atendidas=senhas_atendidas_no_local(local),
        fila_ids=fila_ids_do_local(local, current_user),
    )


@atendimento_bp.route("/<any(recepcao,coleta):setor>/local/<int:local_id>/dados")
def dados_local(setor, local_id):
    """Endpoint JSON equivalente a dados_fila, mas agregado por Local (adendo 10.8) -
    consumido pelo JS da tela (Fase 10.8-E) após qualquer evento 'fila_atualizada'
    recebido em qualquer uma das N salas do rodízio, mesmo padrão de dados_fila."""
    exigir_acesso_setor(setor)
    local = local_acessivel_ou_404(local_id)

    senha_atual = senha_atual_no_local(local, current_user)

    return jsonify(
        {
            "senha_atual": _serializar_senha_atual_local(senha_atual),
            "espera": [_serializar_espera_local(s) for s in senhas_aguardando_local(local, current_user)],
            "atendidas": [_serializar_atendida_local(s) for s in senhas_atendidas_no_local(local)],
        }
    )


@atendimento_bp.route("/<any(recepcao,coleta):setor>/local/<int:local_id>/chamar", methods=["POST"])
def chamar_local(setor, local_id):
    """Botão 'Chamar' da tela por-Local (adendo 10.8, Fase 10.8-E): mesma dupla de
    caminhos do 'chamar' por-fila (busca manual por código, ou seleção automática) -
    só a SELEÇÃO muda (percorre todas as filas do rodízio via
    selecionar_proxima_fila_e_senha, em vez de uma única fila fixa). A mutação em si
    (registrar_chamada_senha) é a mesma função de sempre, sem duplicação de lógica.

    Bloqueio de chamada sem finalização: se este operador já tem uma senha em
    andamento (status 'chamada'/'em_atendimento') neste Local, 'Chamar' é recusado
    ANTES de qualquer seleção - a mesma senha_atual_no_local(local, current_user)
    que a tela usa pra exibir "Senha chamada" é o que decide o bloqueio, então o
    que está bloqueando sempre está visível na própria tela."""
    exigir_acesso_setor(setor)
    local = local_acessivel_ou_404(local_id)
    fila_ids = fila_ids_do_local(local, current_user)

    if senha_atual_no_local(local, current_user) is not None:
        flash("Finalize o atendimento atual antes de chamar o próximo.", "error")
        return redirect(url_for("atendimento.painel_local", setor=setor, local_id=local.id))

    codigo = request.form.get("codigo", "").strip().upper()
    if codigo:
        senha = Senha.query.filter(
            Senha.fila_id.in_(fila_ids), Senha.status == "aguardando", Senha.codigo == codigo
        ).first()
        if senha is None:
            flash(f"Código '{codigo}' não encontrado ou já atendido nas filas deste Local.", "error")
            return redirect(url_for("atendimento.painel_local", setor=setor, local_id=local.id))
    else:
        _, senha = selecionar_proxima_fila_e_senha(local, current_user)
        if senha is None:
            flash("Não há senhas aguardando nas filas deste Local.", "error")
            return redirect(url_for("atendimento.painel_local", setor=setor, local_id=local.id))

    registrar_chamada_senha(senha, local, current_user, setor)
    flash(f"Senha {senha.codigo} chamada para {local.nome_exibicao}.", "info")
    return redirect(url_for("atendimento.painel_local", setor=setor, local_id=local.id))


@atendimento_bp.route("/<any(recepcao,coleta):setor>/local/<int:local_id>/tocar", methods=["POST"])
def tocar_local(setor, local_id):
    """Botão 'Tocar' da tela por-Local (Fase 10.8-E) - mesma ação de sempre
    (registrar_toque, sem alterar nada no banco), agora buscando a senha atual entre
    QUALQUER fila do rodízio deste Local (senha_atual_no_local sem fila_ids)."""
    exigir_acesso_setor(setor)
    local = local_acessivel_ou_404(local_id)

    senha = senha_atual_no_local(local, current_user)
    if senha is None:
        flash("Nenhuma senha chamada neste Local.", "error")
        return redirect(url_for("atendimento.painel_local", setor=setor, local_id=local.id))

    registrar_toque(senha)
    flash(f"Chamada de {senha.codigo} repetida.", "info")
    return redirect(url_for("atendimento.painel_local", setor=setor, local_id=local.id))


@atendimento_bp.route("/<any(recepcao,coleta):setor>/local/<int:local_id>/realocar", methods=["POST"])
def realocar_local(setor, local_id):
    """Botão 'Realocar' da tela por-Local (Fase 10.8-E) - move a senha atual para
    outra fila (bloco Redirecionar), delegando para registrar_realocacao já existente,
    sem mudança. filas_destino(senha.fila, local) usa a fila DE ORIGEM da senha (que
    pode ser qualquer uma do rodízio), não uma fila fixa da rota."""
    exigir_acesso_setor(setor)
    local = local_acessivel_ou_404(local_id)

    senha = senha_atual_no_local(local, current_user)
    if senha is None:
        flash("Nenhuma senha chamada neste Local.", "error")
        return redirect(url_for("atendimento.painel_local", setor=setor, local_id=local.id))

    fila_destino_id = request.form.get("fila_destino_id", type=int)
    destino = next((f for f in filas_destino(senha.fila, local) if f.id == fila_destino_id), None)
    if destino is None:
        flash("Selecione uma fila de destino válida em Redirecionar.", "error")
        return redirect(url_for("atendimento.painel_local", setor=setor, local_id=local.id))

    talvez_atualizar_nome(
        senha, request.form.get("nome"), current_user, setor, emitir_tv=False, registrar_como_chamada=False
    )
    registrar_realocacao(senha, destino, current_user, setor)
    flash(f"Senha {senha.codigo} realocada para '{destino.nome}'.", "info")
    return redirect(url_for("atendimento.painel_local", setor=setor, local_id=local.id))


@atendimento_bp.route("/<any(recepcao,coleta):setor>/local/<int:local_id>/finalizar", methods=["POST"])
def finalizar_local(setor, local_id):
    """Botão 'Finalizar' da tela por-Local (Fase 10.8-E) - conclui o atendimento da
    senha atual, delegando para registrar_fim_atendimento já existente, sem mudança."""
    exigir_acesso_setor(setor)
    local = local_acessivel_ou_404(local_id)

    senha = senha_atual_no_local(local, current_user)
    if senha is None:
        flash("Nenhuma senha chamada neste Local.", "error")
        return redirect(url_for("atendimento.painel_local", setor=setor, local_id=local.id))

    talvez_atualizar_nome(senha, request.form.get("nome"), current_user, setor)
    registrar_fim_atendimento(senha, current_user, setor)
    flash(f"Atendimento da senha {senha.codigo} finalizado.", "info")
    return redirect(url_for("atendimento.painel_local", setor=setor, local_id=local.id))


@atendimento_bp.route("/<any(recepcao,coleta):setor>/local/<int:local_id>/aguardar", methods=["POST"])
def aguardar_local(setor, local_id):
    """Botão 'Aguardar' da tela por-Local (Fase 10.8-E) - devolve a senha atual para a
    espera, delegando para registrar_volta_espera já existente, sem mudança."""
    exigir_acesso_setor(setor)
    local = local_acessivel_ou_404(local_id)

    senha = senha_atual_no_local(local, current_user)
    if senha is None:
        flash("Nenhuma senha chamada neste Local.", "error")
        return redirect(url_for("atendimento.painel_local", setor=setor, local_id=local.id))

    talvez_atualizar_nome(senha, request.form.get("nome"), current_user, setor)
    registrar_volta_espera(senha, current_user, setor)
    flash(f"Senha {senha.codigo} voltou para a espera.", "info")
    return redirect(url_for("atendimento.painel_local", setor=setor, local_id=local.id))


@atendimento_bp.route("/<any(recepcao,coleta):setor>/filas/<int:fila_id>")
def painel_fila(setor, fila_id):
    exigir_acesso_setor(setor)
    fila = fila_acessivel_ou_404(fila_id)

    local, redirecionamento = _exigir_local_ou_redirecionar("painel_fila", setor=setor, fila_id=fila_id)
    if redirecionamento is not None:
        return redirecionamento

    senha_atual = senha_atual_no_local(local, current_user, [fila.id])
    filas_destino_atual = filas_destino(fila, local)

    return render_template(
        "atendimento/painel_fila.html",
        setor=setor,
        fila=fila,
        local=local,
        senha_atual=senha_atual,
        filas_destino=filas_destino_atual,
        espera=senhas_aguardando(fila),
        atendidas=senhas_atendidas_no_local(local, [fila.id]),
    )


@atendimento_bp.route("/<any(recepcao,coleta):setor>/filas/<int:fila_id>/dados")
def dados_fila(setor, fila_id):
    exigir_acesso_setor(setor)
    fila = fila_acessivel_ou_404(fila_id)

    local = local_atual()
    senha_atual = senha_atual_no_local(local, current_user, [fila.id])

    return jsonify(
        {
            "senha_atual": _serializar_senha_atual(senha_atual),
            "espera": [_serializar_espera(s) for s in senhas_aguardando(fila)],
            "atendidas": [_serializar_atendida(s) for s in senhas_atendidas_no_local(local, [fila.id])],
        }
    )


@atendimento_bp.route("/<any(recepcao,coleta):setor>/filas/<int:fila_id>/chamar", methods=["POST"])
def chamar(setor, fila_id):
    """Botão 'Chamar' (seção 10.2): chama a próxima senha da fila, ou a do campo
    Código se preenchido (busca manual, modo de contingência da seção 8).

    Trava a linha da Fila (adendo 10.8, Fase 10.8-E) antes de selecionar a próxima
    senha - fecha o gap identificado depois que uma fila pode participar do rodízio
    de um Local (`selecionar_proxima_fila_e_senha`, que trava a mesma linha da fila
    candidata) e, ao mesmo tempo, continuar acessível por esta URL antiga. Sem essa
    trava aqui, os dois caminhos nunca disputariam a mesma linha (a trava do Local
    usada por `chamar_local` não bloqueia a linha da Fila) e poderiam chamar a
    mesma senha duas vezes ao rodar ao mesmo tempo. Ver `travar_fila` para detalhes.

    Bloqueio de chamada sem finalização: se este operador já tem uma senha em
    andamento (status 'chamada'/'em_atendimento') nesta fila+local, 'Chamar' é
    recusado antes de travar/selecionar nada - a mesma senha_atual_no_local(local,
    current_user, [fila.id]) que a tela usa pra exibir "Senha chamada" decide o
    bloqueio, então o que está bloqueando sempre está visível na própria tela.
    Some sozinho quando o operador usa Finalizar/Aguardar/Realocar (o status deixa
    de ser 'chamada'/'em_atendimento') - nenhuma lógica extra de "exceção" precisa
    existir além do que o próprio estado da senha já garante.
    """
    exigir_acesso_setor(setor)
    fila = fila_acessivel_ou_404(fila_id)

    local, redirecionamento = _exigir_local_ou_redirecionar("painel_fila", setor=setor, fila_id=fila_id)
    if redirecionamento is not None:
        return redirecionamento

    if senha_atual_no_local(local, current_user, [fila.id]) is not None:
        flash("Finalize o atendimento atual antes de chamar o próximo.", "error")
        return redirect(url_for("atendimento.painel_fila", setor=setor, fila_id=fila.id))

    travar_fila(fila)

    codigo = request.form.get("codigo", "").strip().upper()
    if codigo:
        senha = Senha.query.filter_by(fila_id=fila.id, status="aguardando", codigo=codigo).first()
        if senha is None:
            flash(f"Código '{codigo}' não encontrado ou já atendido nesta fila.", "error")
            return redirect(url_for("atendimento.painel_fila", setor=setor, fila_id=fila.id))
    else:
        senha = selecionar_proxima_senha(fila)
        if senha is None:
            flash("Não há senhas aguardando nesta fila.", "error")
            return redirect(url_for("atendimento.painel_fila", setor=setor, fila_id=fila.id))

    registrar_chamada_senha(senha, local, current_user, setor)
    flash(f"Senha {senha.codigo} chamada para {local.nome_exibicao}.", "info")
    return redirect(url_for("atendimento.painel_fila", setor=setor, fila_id=fila.id))


@atendimento_bp.route("/<any(recepcao,coleta):setor>/filas/<int:fila_id>/tocar", methods=["POST"])
def tocar(setor, fila_id):
    """Botão 'Tocar' (seção 10.2): repete a chamada atual sem alterar nada no banco."""
    exigir_acesso_setor(setor)
    fila = fila_acessivel_ou_404(fila_id)

    local, redirecionamento = _exigir_local_ou_redirecionar("painel_fila", setor=setor, fila_id=fila_id)
    if redirecionamento is not None:
        return redirecionamento

    senha = senha_atual_no_local(local, current_user, [fila.id])
    if senha is None:
        flash("Nenhuma senha chamada neste local.", "error")
        return redirect(url_for("atendimento.painel_fila", setor=setor, fila_id=fila.id))

    registrar_toque(senha)
    flash(f"Chamada de {senha.codigo} repetida.", "info")
    return redirect(url_for("atendimento.painel_fila", setor=setor, fila_id=fila.id))


@atendimento_bp.route("/<any(recepcao,coleta):setor>/filas/<int:fila_id>/realocar", methods=["POST"])
def realocar(setor, fila_id):
    """Botão 'Realocar' (seção 10.2): move a senha atual para outra fila (bloco
    Redirecionar), sem finalizar o atendimento."""
    exigir_acesso_setor(setor)
    fila = fila_acessivel_ou_404(fila_id)

    local, redirecionamento = _exigir_local_ou_redirecionar("painel_fila", setor=setor, fila_id=fila_id)
    if redirecionamento is not None:
        return redirecionamento

    senha = senha_atual_no_local(local, current_user, [fila.id])
    if senha is None:
        flash("Nenhuma senha chamada neste local.", "error")
        return redirect(url_for("atendimento.painel_fila", setor=setor, fila_id=fila.id))

    fila_destino_id = request.form.get("fila_destino_id", type=int)
    destino = next((f for f in filas_destino(fila, local) if f.id == fila_destino_id), None)
    if destino is None:
        flash("Selecione uma fila de destino válida em Redirecionar.", "error")
        return redirect(url_for("atendimento.painel_fila", setor=setor, fila_id=fila.id))

    talvez_atualizar_nome(
        senha, request.form.get("nome"), current_user, setor, emitir_tv=False, registrar_como_chamada=False
    )
    registrar_realocacao(senha, destino, current_user, setor)
    flash(f"Senha {senha.codigo} realocada para '{destino.nome}'.", "info")
    return redirect(url_for("atendimento.painel_fila", setor=setor, fila_id=fila.id))


@atendimento_bp.route("/<any(recepcao,coleta):setor>/filas/<int:fila_id>/finalizar", methods=["POST"])
def finalizar(setor, fila_id):
    """Botão 'Finalizar' (seção 10.2): conclui o atendimento da senha atual."""
    exigir_acesso_setor(setor)
    fila = fila_acessivel_ou_404(fila_id)

    local, redirecionamento = _exigir_local_ou_redirecionar("painel_fila", setor=setor, fila_id=fila_id)
    if redirecionamento is not None:
        return redirecionamento

    senha = senha_atual_no_local(local, current_user, [fila.id])
    if senha is None:
        flash("Nenhuma senha chamada neste local.", "error")
        return redirect(url_for("atendimento.painel_fila", setor=setor, fila_id=fila.id))

    talvez_atualizar_nome(senha, request.form.get("nome"), current_user, setor)
    registrar_fim_atendimento(senha, current_user, setor)
    flash(f"Atendimento da senha {senha.codigo} finalizado.", "info")
    return redirect(url_for("atendimento.painel_fila", setor=setor, fila_id=fila.id))


@atendimento_bp.route("/<any(recepcao,coleta):setor>/filas/<int:fila_id>/aguardar", methods=["POST"])
def aguardar(setor, fila_id):
    """Botão 'Aguardar' (seção 10.2): devolve a senha atual para a espera, sem finalizar."""
    exigir_acesso_setor(setor)
    fila = fila_acessivel_ou_404(fila_id)

    local, redirecionamento = _exigir_local_ou_redirecionar("painel_fila", setor=setor, fila_id=fila_id)
    if redirecionamento is not None:
        return redirecionamento

    senha = senha_atual_no_local(local, current_user, [fila.id])
    if senha is None:
        flash("Nenhuma senha chamada neste local.", "error")
        return redirect(url_for("atendimento.painel_fila", setor=setor, fila_id=fila.id))

    talvez_atualizar_nome(senha, request.form.get("nome"), current_user, setor)
    registrar_volta_espera(senha, current_user, setor)
    flash(f"Senha {senha.codigo} voltou para a espera.", "info")
    return redirect(url_for("atendimento.painel_fila", setor=setor, fila_id=fila.id))


@atendimento_bp.route(
    "/<any(recepcao,coleta):setor>/senhas/<int:senha_id>/observacoes", methods=["POST"]
)
def salvar_observacoes(setor, senha_id):
    """Edição inline da coluna Observações na tabela 'Senhas em espera' (seção 10.2) -
    ação independente dos 5 botões principais, sem mudar status nem emitir 'chamada'."""
    exigir_acesso_setor(setor)
    senha = senha_acessivel_ou_404(senha_id)

    senha.observacoes = (request.form.get("observacoes") or "").strip() or None
    db.session.commit()
    emitir_atualizacao_fila(senha.fila_id)

    flash(f"Observações da senha {senha.codigo} salvas.", "info")
    return redirect(url_for("atendimento.painel_fila", setor=setor, fila_id=senha.fila_id))
