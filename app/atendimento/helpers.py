from datetime import datetime

from flask import abort, g, session
from flask_login import current_user
from sqlalchemy import func, update

from app.extensions import db, socketio
from app.models import Fila, Local, LogAtendimento, Senha, TipoSenha, TurnoAtivo, Usuario

PERFIS_POR_SETOR = {
    "recepcao": ("atendente", "atendente_executor", "admin"),
    "coleta": ("executor", "atendente_executor", "admin"),
}

CHAVE_SESSAO_LOCAL = "atendimento_local_id"


def exigir_acesso_setor(setor):
    """RBAC das telas de chamada: as chaves deste dict ('recepcao'/'coleta') são o
    SETOR (área de trabalho, usada na URL - <any(recepcao,coleta):setor> -, em
    Local.tipo e Senha.setor; nunca renomeada). Os valores são os PERFIS (papel do
    usuário) que podem acessar cada setor: 'atendente' e 'executor' aceitam o
    perfil próprio, 'atendente_executor' (união dos dois) ou 'admin' - nunca 'gerencial'
    (só relatórios, seção 3 do BLUEPRINT). Segue o mesmo padrão de roles_required
    (abort, não redirect)."""
    if not current_user.is_authenticated:
        abort(401)
    if not (current_user.is_super_admin or current_user.tem_perfil(*PERFIS_POR_SETOR[setor])):
        abort(403)


def fila_acessivel_ou_404(fila_id):
    """Carrega a fila garantindo tenant + permissão por unidade (g.unidades_permitidas).

    404 (não 403) em caso de violação, para não revelar a um atacante que o ID existe
    em outra empresa/unidade - mesma convenção já usada em admin/helpers.py e no totem.
    """
    fila = Fila.query.get_or_404(fila_id)
    if not g.is_super_admin and fila.empresa_id != g.empresa_id:
        abort(404)
    if g.unidades_permitidas is not None and fila.unidade_id not in g.unidades_permitidas:
        abort(404)
    return fila


def senha_acessivel_ou_404(senha_id):
    senha = Senha.query.get_or_404(senha_id)
    if not g.is_super_admin and senha.empresa_id != g.empresa_id:
        abort(404)
    if g.unidades_permitidas is not None and senha.unidade_id not in g.unidades_permitidas:
        abort(404)
    return senha


def local_acessivel_ou_404(local_id):
    """Carrega o Local garantindo tenant + permissão por unidade (g.unidades_permitidas)
    - mesma convenção de fila_acessivel_ou_404/senha_acessivel_ou_404 (404, não 403,
    para não revelar a um atacante que o ID existe em outra empresa/unidade). Usada
    pela tela por-Local (adendo 10.8, Fase 10.8-D)."""
    local = Local.query.get_or_404(local_id)
    if not g.is_super_admin and local.empresa_id != g.empresa_id:
        abort(404)
    if g.unidades_permitidas is not None and local.unidade_id not in g.unidades_permitidas:
        abort(404)
    return local


def filas_acessiveis():
    query = Fila.query.filter_by(empresa_id=g.empresa_id, ativo=True)
    if g.unidades_permitidas is not None:
        query = query.filter(Fila.unidade_id.in_(g.unidades_permitidas))
    return query.order_by(Fila.nome).all()


def locais_acessiveis():
    """Locais (seção 10.3) que o operador pode escolher como o posto do seu turno -
    das unidades que ele tem permissão de acessar (g.unidades_permitidas), igual ao
    filtro já usado em filas_acessiveis(). Super admin vê os de toda a empresa."""
    query = Local.query.filter_by(empresa_id=g.empresa_id, ativo=True)
    if g.unidades_permitidas is not None:
        query = query.filter(Local.unidade_id.in_(g.unidades_permitidas))
    return query.order_by(Local.nome).all()


def local_atual():
    """O Local escolhido para este turno/sessão (seção 10.3) - None se o operador
    ainda não escolheu, ou se o local escolhido deixou de ser válido (desativado,
    ou o operador perdeu acesso àquela unidade) desde a escolha."""
    local_id = session.get(CHAVE_SESSAO_LOCAL)
    if local_id is None:
        return None
    local = Local.query.get(local_id)
    if local is None or not local.ativo:
        return None
    if not g.is_super_admin:
        if local.empresa_id != g.empresa_id:
            return None
        if g.unidades_permitidas is not None and local.unidade_id not in g.unidades_permitidas:
            return None
    return local


def turno_ativo_do_usuario(usuario):
    """O turno em andamento deste operador (seção 10.19, tela 'Iniciar Turno') -
    None se ele nunca iniciou um, ou se o último foi encerrado (ativo=False, seja
    pelo botão 'Encerrar turno' ou pelo encerramento automático às 23h). Usada tanto
    pela própria tela (GET: pula direto pro painel_local se já houver um turno ativo
    - recuperação de estado após reload/crash, adendo 10.11) quanto pelo motor de
    rodízio (ver `_itens_rotacao`), que prioriza a configuração do turno sobre o
    rodízio admin-configurado do Local quando o operador tem um turno ativo para
    aquele Local especificamente."""
    return TurnoAtivo.query.filter_by(usuario_id=usuario.id, ativo=True).first()


def fila_ids_do_local(local, operador=None):
    """Filas do rodízio deste Local válidas AGORA para este operador (seção 10.19) -
    usa a configuração do turno ativo dele para ESTE Local quando existir (filas
    escolhidas por ele mesmo ao iniciar o turno); cai no rodízio admin-configurado
    (`Local.rodizio_ordenado`, adendo 10.8) quando não há turno ativo (`operador`
    None, sem turno, ou turno de outro Local) - mesmo comportamento de sempre, sem
    quebrar Locais que nunca usaram a tela de turno."""
    turno = turno_ativo_do_usuario(operador) if operador is not None else None
    if turno is not None and turno.local_id == local.id:
        return [item["fila_id"] for item in turno.filas_config]
    return [item.fila_id for item in local.rodizio_ordenado]


class _ItemRotacaoTurno:
    """Espelha a interface de `LocalRodizioFila` (`.fila_id`/`.chamadas_seguidas`/
    `.fila`) usada por `_proximo_item_na_rotacao`, só que construída a partir do JSON
    de um `TurnoAtivo` em vez de linhas do banco. Sem `__eq__`/`__hash__` próprios
    (identidade padrão do Python) de propósito: `filas_config` já chega sem
    `fila_id` duplicado (mesclados ao salvar o turno - ver `turno.py`), então cada
    instância aqui já é univocamente identificável pelo seu `fila_id`."""

    __slots__ = ("fila_id", "chamadas_seguidas", "fila")

    def __init__(self, fila_id, chamadas_seguidas, fila):
        self.fila_id = fila_id
        self.chamadas_seguidas = chamadas_seguidas
        self.fila = fila


def _itens_rotacao(local, operador):
    """Lista de itens do rodízio (mesma interface de `Local.rodizio_ordenado`) a
    usar neste Local para este operador - ver `fila_ids_do_local` para a regra de
    prioridade (turno ativo do operador > rodízio admin-configurado do Local)."""
    turno = turno_ativo_do_usuario(operador) if operador is not None else None
    if turno is None or turno.local_id != local.id:
        return list(local.rodizio_ordenado)

    fila_ids = [item["fila_id"] for item in turno.filas_config]
    filas_por_id = {f.id: f for f in Fila.query.filter(Fila.id.in_(fila_ids)).all()}
    itens = []
    for item in turno.filas_config:
        fila = filas_por_id.get(item["fila_id"])
        if fila is not None:
            itens.append(_ItemRotacaoTurno(item["fila_id"], item["chamadas_seguidas"], fila))
    return itens


def usuarios_ativos_por_local(unidade_id):
    """Operadores com um Local escolhido agora nesta unidade (Usuario.local_atual_id/
    local_desde, persistidos no servidor - diagnóstico item 1: antes esse dado só
    existia na sessão do navegador, sem nenhuma query capaz de enxergá-lo).

    Uso futuro (tela/dashboard administrativo) - não implementado ainda, só a query.
    Não filtra por empresa/g.unidades_permitidas: quem chama já deve passar um
    unidade_id que o usuário logado tem permissão de ver, mesmo padrão de
    fila_acessivel_ou_404/senha_acessivel_ou_404 (a checagem de posse é do chamador).
    """
    usuarios = (
        Usuario.query.join(Local, Usuario.local_atual_id == Local.id)
        .filter(Local.unidade_id == unidade_id)
        .order_by(Local.nome, Usuario.nome)
        .all()
    )
    return [
        {"local": usuario.local_atual, "usuario": usuario, "desde": usuario.local_desde}
        for usuario in usuarios
    ]


def usuarios_ativos_da_empresa(empresa_id):
    """Mesma ideia de usuarios_ativos_por_local, mas para a empresa inteira (todas as
    unidades de uma vez) - usada pela tela administrativa de ocupação de locais
    (diagnóstico item 1, "uso futuro" que virou concreto): o admin quer ver o cenário
    inteiro da empresa, não precisar escolher unidade por unidade."""
    usuarios = (
        Usuario.query.join(Local, Usuario.local_atual_id == Local.id)
        .filter(Local.empresa_id == empresa_id)
        .order_by(Local.nome, Usuario.nome)
        .all()
    )
    return [
        {"local": usuario.local_atual, "usuario": usuario, "desde": usuario.local_desde}
        for usuario in usuarios
    ]


def filas_destino(fila, local):
    """Filas candidatas no bloco "Redirecionar" (seção 10.2).

    Se `local` tiver ao menos uma fila configurada em `Local.filas_destino_permitidas`
    (restrição opcional - diagnóstico item 2), retorna só essas (nunca a própria fila
    de origem). Sem nenhuma configuração (lista vazia, ou `local` None), mantém o
    comportamento histórico: todas as filas ativas da mesma unidade, independente de o
    operador atual também atender nelas (quem vai atender lá é outra pessoa, em outro
    posto) - essencial para não quebrar quem já usa o sistema sem essa configuração.
    """
    permitidas = [
        f for f in (local.filas_destino_permitidas if local is not None else [])
        if f.ativo and f.id != fila.id
    ]
    if permitidas:
        return sorted(permitidas, key=lambda f: f.nome)

    return (
        Fila.query.filter_by(empresa_id=fila.empresa_id, unidade_id=fila.unidade_id, ativo=True)
        .filter(Fila.id != fila.id)
        .order_by(Fila.nome)
        .all()
    )


def filas_rapidas_redirecionar(local, operador, filas_destino_atual):
    """'Minhas filas' (acesso rápido no bloco Redirecionar, seção 10.2) - as filas
    configuradas no turno ativo do operador (seção 10.19) para ESTE Local, na ordem
    em que ele mesmo as organizou ao iniciar o turno.

    Sempre um SUBCONJUNTO de `filas_destino_atual` - nunca amplia quem pode ser
    destino do redirecionamento (mesma autorização/isolamento multi-tenant já
    aplicada por `filas_destino()`, incluindo a restrição opcional de
    `Local.filas_destino_permitidas`); só reordena/destaca, pra acesso de um clique,
    as que o próprio operador já escolheu pro seu turno.

    Sem turno ativo (ou turno de outro Local) retorna lista vazia - degradação
    elegante: o bloco Redirecionar mostra só a busca livre nesse caso.
    """
    turno = turno_ativo_do_usuario(operador) if operador is not None else None
    if turno is None or local is None or turno.local_id != local.id:
        return []
    destino_por_id = {f.id: f for f in filas_destino_atual}
    ordem_turno = [item["fila_id"] for item in turno.filas_config]
    return [destino_por_id[fila_id] for fila_id in ordem_turno if fila_id in destino_por_id]


def senhas_aguardando(fila):
    return (
        Senha.query.filter_by(fila_id=fila.id, status="aguardando")
        .order_by(Senha.criada_em.asc())
        .all()
    )


def senhas_aguardando_local(local, operador=None):
    """'Senhas em espera' agregadas de TODAS as filas do rodízio deste Local (adendo
    10.8, Fase 10.8-C) - usada pela futura tela por-Local (Fase 10.8-D), reunindo
    candidatas de várias filas na mesma lista. Diferente de senhas_aguardando(fila),
    que continua escopada a uma única fila (tela por-fila, seção 10.2, sem mudança).
    Local sem rodízio configurado (lista vazia) retorna lista vazia.

    `operador` (seção 10.19, opcional): quando informado, usa fila_ids_do_local para
    priorizar a configuração do turno ativo dele sobre o rodízio admin-configurado do
    Local - sem operador (compatibilidade com chamadas antigas), comportamento
    idêntico a antes."""
    fila_ids = fila_ids_do_local(local, operador)
    if not fila_ids:
        return []
    return (
        Senha.query.filter(Senha.fila_id.in_(fila_ids), Senha.status == "aguardando")
        .order_by(Senha.criada_em.asc())
        .all()
    )


def senha_atual_no_local(local, operador, fila_ids=None):
    """A 'Senha chamada' daquele posto - a senha mais recente em andamento
    (status 'chamada' ou 'em_atendimento') deste OPERADOR neste Local (seção
    10.2/10.3/controle de atendimento: cada operador só pode ter uma senha em
    andamento por vez, mesmo quando vários postos atendem a mesma fila).

    Escopado por `operador_id` (antes só por `local_id`) - fecha um caso latente:
    sem essa checagem, uma senha deixada "chamada" por um operador anterior que
    nunca finalizou (ex.: caiu a conexão) podia aparecer como "a senha atual" de
    um operador NOVO que passou a ocupar o mesmo Local depois, mesmo sem ele ter
    chamado nada ainda. Usada tanto pra popular a tela quanto pra decidir se o
    botão "Chamar" fica bloqueado (mesma query - o que aparece na tela é
    exatamente o que bloqueia a próxima chamada).

    `fila_ids` (adendo 10.8, Fase 10.8-C) restringe opcionalmente a busca a um
    subconjunto de filas - a tela por-fila (seção 10.2) sempre passa `fila_ids=[fila.id]`,
    preservando exatamente o filtro fixo por uma única fila que já existia antes desta
    generalização. A tela por-Local (Fase 10.8-D) não passa `fila_ids`, abrangendo
    qualquer fila do rodízio configurado naquele Local.
    """
    if local is None:
        return None
    query = Senha.query.filter(
        Senha.local_id == local.id,
        Senha.operador_id == operador.id,
        Senha.status.in_(("chamada", "em_atendimento")),
    )
    if fila_ids is not None:
        query = query.filter(Senha.fila_id.in_(fila_ids))
    return query.order_by(Senha.chamada_em.desc()).first()


def senhas_atendidas_no_local(local, fila_ids=None, limite=20):
    """Histórico recente "Senhas atendidas" (seção 10.2) - escopado por local, não só
    por fila, já que cada posto quer ver o que ELE chamou.

    Mesma generalização de `senha_atual_no_local` (adendo 10.8, Fase 10.8-C):
    `fila_ids=None` abrange qualquer fila do Local; a tela por-fila continua passando
    `fila_ids=[fila.id]`, comportamento idêntico ao filtro fixo anterior.
    """
    if local is None:
        return []
    query = Senha.query.filter(Senha.local_id == local.id, Senha.chamada_em.isnot(None))
    if fila_ids is not None:
        query = query.filter(Senha.fila_id.in_(fila_ids))
    return query.order_by(Senha.chamada_em.desc()).limit(limite).all()


def selecionar_proxima_senha(fila):
    """Decide qual senha 'aguardando' deve ser chamada a seguir nesta fila.

    Sem rodízio configurado (prioridade_rodizio == 0), a ordem é estritamente FIFO por
    criada_em, ignorando o sinalizador de prioridade. Com rodízio > 0 (campo
    prioridade_rodizio da fila, tela "Alocar fila" - seção 5.4), intercala até N
    chamadas prioritárias seguidas antes de forçar 1 chamada normal. A sequência atual
    é recalculada a partir do histórico real de chamadas (chamada_em, já existente no
    schema) em vez de um contador persistido novo - evita qualquer alteração de schema
    só para guardar o estado do rodízio.
    """
    if fila.prioridade_rodizio <= 0:
        return (
            Senha.query.filter_by(fila_id=fila.id, status="aguardando")
            .order_by(Senha.criada_em.asc())
            .first()
        )

    # seção 10.4: dentro do grupo de prioritárias, o peso_prioridade do tipo de senha
    # escolhido no totem desempata a favor de quem pesa mais (ex.: "Idoso" antes de
    # "Gestante" se configurados com pesos diferentes) - sem tipo, peso 0 (FIFO puro
    # entre elas, igual ao comportamento já existente antes da seção 10.4).
    proxima_prioritaria = (
        Senha.query.filter_by(fila_id=fila.id, status="aguardando", prioridade=True)
        .outerjoin(TipoSenha, Senha.tipo_senha_id == TipoSenha.id)
        .order_by(func.coalesce(TipoSenha.peso_prioridade, 0).desc(), Senha.criada_em.asc())
        .first()
    )
    proxima_normal = (
        Senha.query.filter_by(fila_id=fila.id, status="aguardando", prioridade=False)
        .order_by(Senha.criada_em.asc())
        .first()
    )

    chamadas_recentes = (
        Senha.query.filter(Senha.fila_id == fila.id, Senha.chamada_em.isnot(None))
        .order_by(Senha.chamada_em.desc())
        .limit(fila.prioridade_rodizio)
        .all()
    )
    sequencia_prioritaria_atual = 0
    for chamada in chamadas_recentes:
        if not chamada.prioridade:
            break
        sequencia_prioritaria_atual += 1

    if sequencia_prioritaria_atual < fila.prioridade_rodizio and proxima_prioritaria is not None:
        return proxima_prioritaria
    return proxima_normal or proxima_prioritaria


def travar_fila(fila):
    """Trava a linha da Fila (UPDATE no-op) para serializar concorrência de 'Chamar'
    sobre a MESMA fila - usada tanto pelo caminho por-fila antigo (`chamar()`)
    quanto pelo rodízio por-Local (`selecionar_proxima_fila_e_senha`, adiante).

    Motivo de existir como trava própria de Fila, além da trava de Local já usada em
    `selecionar_proxima_fila_e_senha`: uma fila pode participar do rodízio de um
    Local e, ao mesmo tempo, continuar acessível pela URL antiga `/filas/<id>`
    (seção 5 do adendo 10.8, coexistência deliberada). Travar só a linha do Local
    não impede essa fila de ser chamada ao mesmo tempo pelo caminho antigo - são
    linhas de tabelas diferentes (`locais`/`filas`), que não se bloqueiam entre si.
    Travar a linha da Fila em AMBOS os caminhos os faz disputar a mesma linha
    quando a fila é compartilhada, fechando esse gap.

    Mesmo princípio já usado em `totem/routes.py::_emitir_senha` - UPDATE real, não
    um simples `SELECT ... FOR UPDATE`, porque o SQLite usado nos testes trata
    `FOR UPDATE` como no-op (a cláusula é silenciosamente descartada pelo SQLAlchemy).
    """
    db.session.execute(update(Fila).where(Fila.id == fila.id).values(ativo=Fila.ativo))


def _proximo_item_na_rotacao(itens, ultimas_chamadas):
    """Decide qual fila é a vez dentro do rodízio de um Local (adendo 10.8, Fase
    10.8-C). `itens` é a lista de `LocalRodizioFila` já ordenada por `ordem`
    (`Local.rodizio_ordenado`, já filtrada às filas ativas/abertas agora).

    Conta, a partir da chamada mais recente pra trás, quantas foram seguidas da
    MESMA fila; se esse número ainda for menor que o `chamadas_seguidas` configurado
    pra ela, repete a mesma fila; senão avança pra próxima da lista, ciclicamente ao
    chegar no fim. Sem chamada anterior neste Local, começa pela primeira da lista.
    """
    if not ultimas_chamadas:
        return itens[0]

    fila_mais_recente = ultimas_chamadas[0].fila_id
    sequencia_atual = 0
    for chamada in ultimas_chamadas:
        if chamada.fila_id != fila_mais_recente:
            break
        sequencia_atual += 1

    item_atual = next((item for item in itens if item.fila_id == fila_mais_recente), None)
    if item_atual is None:
        # a fila da última chamada não está mais no rodízio configurado (removida/
        # desativada/fechada desde então) - recomeça do início da lista
        return itens[0]

    if sequencia_atual < item_atual.chamadas_seguidas:
        return item_atual

    indice_atual = itens.index(item_atual)
    return itens[(indice_atual + 1) % len(itens)]


def selecionar_proxima_fila_e_senha(local, operador=None):
    """Rodízio automático entre múltiplas filas de um Local (adendo 10.8, Fase
    10.8-C) - decide TANTO qual fila é a vez QUANTO qual senha chamar dentro dela.

    `operador` (seção 10.19, opcional): quando ele tem um turno ativo para ESTE
    Local, a lista de filas candidatas vem da configuração desse turno
    (`_itens_rotacao`), não do rodízio admin-configurado do Local - permite ao
    próprio operador escolher/pesar as filas do seu turno sem precisar de um admin
    pré-configurar `Local.rodizio_ordenado`. Sem operador (compatibilidade com
    chamadas antigas) ou sem turno ativo, comportamento idêntico a antes.

    Camada externa (esta função, nova): qual fila é a vez, dentre as N filas do
    rodízio configurado no Local (`Local.rodizio_ordenado`).
    Camada interna (já existente, sem nenhuma mudança): dentro da fila escolhida,
    `selecionar_proxima_senha(fila)` decide prioritário-vs-normal exatamente como
    hoje - é aqui que os dois rodízios se encontram, um dentro do outro, nunca
    concorrendo. Nenhuma fila muda de comportamento só por esta função existir.

    Sem contador persistido - mesmo princípio de design já usado em
    `prioridade_rodizio`/`selecionar_proxima_senha`: a posição do rodízio é
    recalculada a partir do histórico real de `Senha.chamada_em` a cada chamada.

    A soma usada como janela do histórico (quantas últimas chamadas deste Local
    olhar pra trás) é DINÂMICA por Local - a soma de `chamadas_seguidas` só das
    filas candidatas DESTE Local especificamente, recalculada a cada chamada.
    Nunca uma constante fixa global (locais diferentes, configurados de forma
    diferente, têm essa soma diferente).

    Concorrência (Fase 10.8-E): trava a linha do Local antes de ler o histórico de
    chamadas - serializa duas tentativas concorrentes de "Chamar" no MESMO Local
    (cenário real: login compartilhado, ou dois operadores no mesmo posto físico),
    evitando que ambas leiam o mesmo estado do rodízio e escolham a mesma senha. A
    trava fica retida até o commit de `registrar_chamada_senha` (chamado logo depois,
    na mesma transação) - a segunda chamada concorrente só prossegue depois que a
    primeira já commitou, e nesse ponto já enxerga o `chamada_em` novo no histórico.

    Mesmo princípio já usado em `totem/routes.py::_emitir_senha` (UPDATE real, não só
    SELECT, porque SQLite nos testes trata `FOR UPDATE` como no-op).

    Trava adicional por Fila (reforço adicionado após a revisão da Fase 10.8-E): a
    trava do Local, sozinha, só serializa concorrência DENTRO do rodízio do mesmo
    Local - não protege contra a URL antiga `/filas/<id>/chamar` (`chamar()`) sendo
    chamada ao mesmo tempo para uma fila que também participa deste rodízio, já que
    travar a linha de `locais` não bloqueia nem é bloqueado por travar a linha de
    `filas` (tabelas/linhas diferentes). Por isso, além da trava do Local, também
    travamos (via `travar_fila`) a linha da fila candidata no momento em que ela é
    escolhida - a mesma trava que `chamar()` agora também toma no início. Os dois
    caminhos disputando a mesma linha de `filas` é o que de fato fecha o gap.
    """
    db.session.execute(update(Local).where(Local.id == local.id).values(ativo=Local.ativo))

    itens = [item for item in _itens_rotacao(local, operador) if item.fila.ativo and item.fila.aberta_em()]
    if not itens:
        return None, None

    soma_chamadas_seguidas = sum(item.chamadas_seguidas for item in itens)

    ultimas_chamadas = (
        Senha.query.filter(Senha.local_id == local.id, Senha.chamada_em.isnot(None))
        .order_by(Senha.chamada_em.desc())
        .limit(soma_chamadas_seguidas)
        .all()
    )

    item_da_vez = _proximo_item_na_rotacao(itens, ultimas_chamadas)
    fila_da_vez = item_da_vez.fila

    travar_fila(fila_da_vez)
    senha = selecionar_proxima_senha(fila_da_vez)

    # fila da vez está sem ninguém esperando - percorre as demais, na ordem, até
    # achar uma com gente esperando (não trava o rodízio numa fila vazia).
    # Nota de baixo impacto (registrada na revisão da Fase 10.8-C, não corrigida):
    # o percurso é sempre a partir do início de `itens`, não a partir da posição
    # seguinte a item_da_vez - viés sutil de preferência pela fila de `ordem` mais
    # baixa em cenários raros de múltiplas filas vazias simultâneas.
    if senha is None:
        for item in itens:
            if item.fila_id == item_da_vez.fila_id:
                continue
            travar_fila(item.fila)
            senha = selecionar_proxima_senha(item.fila)
            if senha is not None:
                fila_da_vez = item.fila
                break

    return fila_da_vez, senha


def emitir_chamada(fila, payload):
    socketio.emit("chamada", payload, room=f"fila_{fila.id}", namespace="/painel")


def emitir_atualizacao_fila(fila_id):
    """Sinal leve para a própria tela de atendimento (seção 10.2, distinto do evento
    'chamada' que alimenta a TV) - quem recebe, refaz um GET no endpoint /dados e
    re-renderiza as tabelas de espera/atendidas, em vez de o servidor empurrar o
    payload completo (mais simples e robusto que sincronizar DOM via diff)."""
    socketio.emit("fila_atualizada", {"fila_id": fila_id}, room=f"fila_{fila_id}", namespace="/painel")


def historico_chamadas_tv(tv, limite=8):
    """Reconstrói o histórico de chamadas 'hoje' de uma TV, a partir de Senha.chamada_em -
    bug corrigido: antes o histórico só existia na memória do navegador (array JS), então
    um reload/reconexão (Smart TVs ficam dias ligadas, seção 6/8) apagava tudo e a lista
    voltava vazia. Usado por `entrar_tv` (app/sockets/painel.py) a cada (re)conexão, então
    também resolve o caso de a TV perder rede por alguns minutos e "pular" chamadas.

    'hoje' = mesma janela usada no reset diário do sequencial (totem/routes.py) - compara
    a data em UTC, não a data local da clínica, para ficar consistente com o resto do
    sistema (criada_em/chamada_em são sempre UTC "naive" no banco).

    Limitação aceita (mesmo espírito da seção 7 do BLUEPRINT sobre logs_atendimento não
    guardar snapshot por evento): reflete o estado ATUAL de cada Senha, não um retrato de
    cada chamada - uma senha rechamada mais tarde no dia (ex.: depois de Realocar) aparece
    só pela chamada mais recente, não duas vezes.
    """
    fila_ids = [f.id for f in tv.filas]
    if not fila_ids:
        return [], 0

    hoje = datetime.utcnow().date()
    consulta = Senha.query.filter(
        Senha.fila_id.in_(fila_ids),
        Senha.chamada_em.isnot(None),
        func.date(Senha.chamada_em) == hoje,
    )
    total_hoje = consulta.count()
    recentes = consulta.order_by(Senha.chamada_em.desc()).limit(limite).all()
    historico = [_payload_chamada(s) for s in reversed(recentes)]
    return historico, total_hoje


def _nome_local(senha):
    return senha.local.nome if senha.local is not None else "—"


def _payload_chamada(senha):
    if senha.nome_paciente:
        payload = {"tipo": "nome", "local": _nome_local(senha), "nome_paciente": senha.nome_paciente}
    else:
        payload = {"tipo": "senha", "local": _nome_local(senha), "senha": senha.codigo}

    # seção 10.4: a cor do tipo de senha (quando houver) viaja no payload para o
    # painel/TV destacar visualmente a chamada (ex.: prioritárias com fundo
    # diferente) - omitida quando a senha não tem tipo, para não alterar o
    # contrato já existente do evento 'chamada' nesse caso.
    #
    # Bug corrigido (correção pós-redesign do painel de TV): `senha.prioridade`
    # também é exigido aqui - antes, qualquer TipoSenha com `cor` cadastrada
    # destacava o card, mesmo em tipos não-prioritários (ex.: um tipo chamado
    # "Normal" com cor cadastrada por engano/preferência visual do admin,
    # peso_prioridade=0). Isso pintava chamadas comuns de vermelho, violando a
    # regra "vermelho reservado pra prioridade". `Senha.prioridade` é a mesma
    # flag que já decide FIFO-vs-rodízio em selecionar_proxima_senha() - fonte
    # única de verdade pra "esta chamada é prioritária", sem introduzir critério
    # novo.
    if senha.prioridade and senha.tipo_senha is not None and senha.tipo_senha.cor:
        payload["cor"] = senha.tipo_senha.cor
    return payload


def talvez_atualizar_nome(senha, nome_form, operador, setor, emitir_tv=True, registrar_como_chamada=True):
    """Salva o campo Nome (seção 10.2) se o operador preencheu algo diferente do que já
    estava gravado - chamado a partir de Realocar/Finalizar/Aguardar, nunca de Chamar
    (que sempre passa a tratar de uma senha NOVA) nem de Tocar (que não grava nada).

    Consolida o que a seção 5.3 original chamava de "chamada por nome": ao gravar o
    nome, anuncia na TV pelo nome no MESMO local em que a senha já está (o paciente já
    chegou ali) - precisa rodar antes de qualquer limpeza de local_id feita pelo
    chamador (realocar/aguardar limpam esse campo depois de chamar esta função).

    `emitir_tv=False` (usado por Realocar): grava o nome normalmente, mas NÃO anuncia
    na TV da fila de origem - Realocar só move a senha para outra fila, sem chamada
    nenhuma ali; quem vai chamar (por nome ou senha) é o destino, depois.

    `registrar_como_chamada=False` (usado por Realocar): grava nome_paciente/
    chamada_nome_em normalmente (estado da senha), mas NÃO cria o
    LogAtendimento(evento="chamada_nome") - essa edição de nome é o fechamento do
    segmento de atendimento da fila de origem (já auditado pelo evento "realocado"
    gravado logo em seguida por registrar_realocacao), não uma chamada nova. Se
    fosse gravado, os relatórios de tempo de atendimento (_pares_inicio_realocado
    em app/relatorios/queries.py) veriam esse log como o "início" mais recente
    (colado ao "realocado" que vem logo depois) em vez do chamada_senha genuíno
    que de fato abriu o atendimento na origem, medindo o segmento como ~0s.
    Também evita contar essa mesma visita duas vezes em ranking_operadores()
    (volume) e no feed de ultimas_chamadas() do dashboard.
    """
    nome = (nome_form or "").strip()
    if not nome or nome == senha.nome_paciente:
        return
    senha.nome_paciente = nome
    senha.chamada_nome_em = datetime.utcnow()
    if registrar_como_chamada:
        db.session.add(
            LogAtendimento(
                senha_id=senha.id, evento="chamada_nome", usuario_id=operador.id,
                unidade_id=senha.unidade_id, setor=setor,
            )
        )
    db.session.commit()
    if emitir_tv:
        emitir_chamada(senha.fila, {"tipo": "nome", "local": _nome_local(senha), "nome_paciente": nome})


def registrar_chamada_senha(senha, local, operador, setor):
    senha.status = "chamada"
    senha.chamada_em = datetime.utcnow()
    senha.local_id = local.id
    senha.operador_id = operador.id
    senha.setor = setor
    db.session.add(
        LogAtendimento(
            senha_id=senha.id,
            evento="chamada_senha",
            usuario_id=operador.id,
            unidade_id=senha.unidade_id,
            setor=setor,
        )
    )
    db.session.commit()

    # se a senha já chega com nome_paciente conhecido (ex.: foi realocada com nome já
    # registrado), anuncia pelo nome em vez do código - mais amigável e mantém a
    # identificação consistente com o que já foi dito ao paciente antes.
    emitir_chamada(senha.fila, _payload_chamada(senha))
    emitir_atualizacao_fila(senha.fila_id)


def registrar_toque(senha):
    """'Tocar' (seção 10.2): repete o áudio/exibição da senha já chamada, sem alterar
    NENHUM status ou timestamp e sem gravar log - é uma repetição pura.

    `repetir: True` no payload (bug corrigido - antes ausente): sinaliza pro
    painel/tv.html que este NÃO é um novo atendimento, é o mesmo sendo
    reanunciado - sem isso, a TV tratava cada Tocar como uma chamada nova e
    empilhava mais uma linha idêntica na lista (duplicava a mesma senha),
    além de inflar o contador "chamadas hoje" sem uma chamada real ter
    acontecido."""
    payload = dict(_payload_chamada(senha))
    payload["repetir"] = True
    emitir_chamada(senha.fila, payload)


def registrar_realocacao(senha, fila_destino, operador, setor):
    """'Realocar' (seção 10.2): manda a senha para outra fila, de volta para
    'aguardando' nela (vai ser chamada de novo, agora por quem atende a fila destino) -
    preserva criada_em/nome_paciente/observacoes, limpa os dados do atendimento atual."""
    fila_origem_id = senha.fila_id
    senha.fila_id = fila_destino.id
    senha.status = "aguardando"
    senha.chamada_em = None
    senha.local_id = None
    senha.operador_id = None
    senha.setor = None
    db.session.add(
        LogAtendimento(
            senha_id=senha.id, evento="realocado", usuario_id=operador.id,
            unidade_id=senha.unidade_id, setor=setor,
        )
    )
    db.session.commit()

    emitir_atualizacao_fila(fila_origem_id)
    emitir_atualizacao_fila(fila_destino.id)


def registrar_volta_espera(senha, operador, setor):
    """'Aguardar' (seção 10.2): pausa o atendimento e devolve a senha para a fila de
    espera da MESMA fila, preservando criada_em (não perde a posição/idade na fila)."""
    senha.status = "aguardando"
    senha.chamada_em = None
    senha.local_id = None
    senha.operador_id = None
    senha.setor = None
    db.session.add(
        LogAtendimento(
            senha_id=senha.id, evento="voltou_aguardar", usuario_id=operador.id,
            unidade_id=senha.unidade_id, setor=setor,
        )
    )
    db.session.commit()

    emitir_atualizacao_fila(senha.fila_id)


def registrar_fim_atendimento(senha, operador, setor):
    """Ação mínima de 'finalizar atendimento' (Fase 10): fecha o ciclo gerada → chamada
    → [chamada por nome] → fim, alimentando os relatórios de tempo de atendimento e
    ranking de operadores (seção 7) que antes ficavam vazios por falta deste evento.

    Não emite 'chamada' (a TV não exibe "finalizado") - só o sinal de atualização da
    própria tela de atendimento (seção 10.2).
    """
    senha.status = "finalizada"
    senha.finalizada_em = datetime.utcnow()
    db.session.add(
        LogAtendimento(
            senha_id=senha.id,
            evento="fim_atendimento",
            usuario_id=operador.id,
            unidade_id=senha.unidade_id,
            setor=setor,
        )
    )
    db.session.commit()

    emitir_atualizacao_fila(senha.fila_id)
