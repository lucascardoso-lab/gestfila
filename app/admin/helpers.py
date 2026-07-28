from flask import abort, g, request, session

from app.models import Empresa

# Seção "Filtro de contexto" (UX do painel admin): a empresa/unidade escolhida na
# barra de contexto fica salva na sessão, pra não precisar reselecionar a cada
# troca de página - só some ao trocar de empresa (unidade de outra empresa não
# faz sentido) ou ao escolher explicitamente "Todas as unidades"/trocar de empresa.
CHAVE_SESSAO_EMPRESA_CONTEXTO = "admin_contexto_empresa_id"
CHAVE_SESSAO_UNIDADE_CONTEXTO = "admin_contexto_unidade_id"


def resolver_empresa_contexto():
    """Resolve a empresa administrada nesta requisição (usada em listagens/criação).

    Super admin escolhe via querystring/form `empresa_id`; retorna None se ainda
    não tiver escolhido (o caller deve exibir o seletor de empresas). Admin de
    empresa sempre administra a própria empresa, ignorando qualquer `empresa_id`
    vindo do cliente.

    A escolha do super admin fica salva na sessão (`CHAVE_SESSAO_EMPRESA_CONTEXTO`)
    - uma requisição SEM `empresa_id` na querystring/form reaproveita a última
    empresa escolhida, em vez de sempre cair no seletor de novo (o que forçava
    reselecionar a cada clique de nav que não carregasse o parâmetro). Um
    `empresa_id` explícito sempre atualiza a sessão e, se for diferente do que
    já estava salvo, também limpa a unidade de contexto (uma unidade escolhida
    pra uma empresa não faz sentido pra outra).
    """
    if not g.is_super_admin:
        return Empresa.query.get_or_404(g.empresa_id)

    if "empresa_id" in request.values:
        empresa_id = request.values.get("empresa_id", type=int)
        if empresa_id != session.get(CHAVE_SESSAO_EMPRESA_CONTEXTO):
            session.pop(CHAVE_SESSAO_UNIDADE_CONTEXTO, None)
        session[CHAVE_SESSAO_EMPRESA_CONTEXTO] = empresa_id
        if empresa_id is None:
            return None
        return Empresa.query.get_or_404(empresa_id)

    empresa_id = session.get(CHAVE_SESSAO_EMPRESA_CONTEXTO)
    if empresa_id is None:
        return None
    empresa = Empresa.query.get(empresa_id)
    if empresa is None:
        session.pop(CHAVE_SESSAO_EMPRESA_CONTEXTO, None)
        return None
    return empresa


def resolver_unidade_contexto(empresa):
    """Resolve a unidade de contexto (opcional) dentro da `empresa` já resolvida -
    usada por telas de listagem pra restringir a uma única unidade (locais, filas,
    totens, TVs, usuários, botões do totem). None = 'Todas as unidades' (sem
    filtro), o padrão.

    Mesma persistência em sessão de `resolver_empresa_contexto` - um
    `unidade_id` explícito (mesmo vazio, ao escolher "Todas as unidades" de
    volta) sempre atualiza a sessão; sem o parâmetro, reaproveita a última
    escolha. Disponível tanto pra super admin quanto pra admin de empresa (a
    diferença entre os dois é só a visibilidade do seletor de EMPRESA - ver
    `admin/_layout.html`).
    """
    from app.models import Unidade

    if "unidade_id" in request.values:
        unidade_id = request.values.get("unidade_id", type=int)
        session[CHAVE_SESSAO_UNIDADE_CONTEXTO] = unidade_id
    else:
        unidade_id = session.get(CHAVE_SESSAO_UNIDADE_CONTEXTO)

    if not unidade_id:
        return None

    unidade = Unidade.query.filter_by(id=unidade_id, empresa_id=empresa.id).first()
    if unidade is None:
        session.pop(CHAVE_SESSAO_UNIDADE_CONTEXTO, None)
        return None
    return unidade


def autorizar_objeto(objeto):
    """Aborta com 404 se `objeto.empresa_id` não pertencer à empresa do usuário logado.

    Usa 404 (não 403) para não revelar a um atacante que o ID existe em outro tenant.
    Super admin tem acesso irrestrito.
    """
    if not g.is_super_admin and objeto.empresa_id != g.empresa_id:
        abort(404)


def unidades_da_empresa(empresa):
    from app.models import Unidade

    return Unidade.query.filter_by(empresa_id=empresa.id).order_by(Unidade.nome).all()


def filas_da_empresa(empresa):
    from app.models import Fila

    return Fila.query.filter_by(empresa_id=empresa.id).order_by(Fila.nome).all()


def totens_da_empresa(empresa):
    from app.models import Totem

    return Totem.query.filter_by(empresa_id=empresa.id).order_by(Totem.nome).all()


def locais_da_empresa(empresa):
    from app.models import Local

    return Local.query.filter_by(empresa_id=empresa.id).order_by(Local.nome).all()


def tipos_senha_da_empresa(empresa):
    from app.models import TipoSenha

    return TipoSenha.query.filter_by(empresa_id=empresa.id).order_by(TipoSenha.nome).all()
