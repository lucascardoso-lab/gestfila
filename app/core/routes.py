from flask import Blueprint, g, jsonify, render_template
from flask_login import current_user, login_required

from app.admin.helpers import resolver_empresa_contexto, resolver_unidade_contexto
from app.models import Empresa, Senha

from .dashboard_queries import (
    LIMITE_DIAS_DASHBOARD,
    TEMPOS_DASHBOARD,
    areas_visiveis_dashboard,
    calcular_kpis,
    fila_ids_da_area,
    filas_com_status,
    primeiro_nome,
    resolver_periodo_dashboard,
    senhas_geradas_no_periodo,
    unidades_visiveis_dashboard,
)

core_bp = Blueprint("core", __name__)


@core_bp.route("/")
@login_required
def index():
    return render_template(
        "core/index.html",
        usuario=current_user,
        empresa_id=g.empresa_id,
        is_super_admin=g.is_super_admin,
    )


def _empresa_do_dashboard():
    """Mesmo padrão já usado em admin/relatorios (resolver_empresa_contexto) - super
    admin escolhe a empresa (com a mesma persistência em sessão da barra de
    contexto, seção 10.22); qualquer outro perfil sempre vê a própria empresa."""
    empresa = resolver_empresa_contexto()
    if empresa is None:
        return None, render_template(
            "admin/selecionar_empresa.html",
            empresas=Empresa.query.order_by(Empresa.nome).all(),
            destino="core.dashboard",
        )
    return empresa, None


def _pode_filtrar_unidade():
    # Mesma regra de visibilidade da barra de contexto (seção 10.22): só quem pode
    # efetivamente estreitar por unidade (admin/gerencial/super admin) vê o select -
    # atendente/executor não tem escolha nenhuma aqui (unidades_visiveis_dashboard
    # nunca chama resolver_unidade_contexto pra esse grupo).
    return current_user.is_super_admin or current_user.tem_perfil("admin", "gerencial")


@core_bp.route("/dashboard")
@login_required
def dashboard():
    """Dashboard operacional (adendo 10.12.2, revisto no 10.86) - tela padrão
    pós-login pra admin/gerencial/super admin (login.py::_deve_ir_direto_para_turno
    manda os perfis puramente operacionais direto pra 'Iniciar Turno' em vez daqui,
    seção 10.19/10.23 - mas nada impede um atendente/executor de visitar /dashboard
    manualmente, com o escopo mais restrito descrito em unidades_visiveis_dashboard)."""
    empresa, selecao = _empresa_do_dashboard()
    if empresa is None:
        return selecao

    unidades_visiveis = unidades_visiveis_dashboard(empresa)
    pode_filtrar_unidade = _pode_filtrar_unidade()
    unidade_escolhida = resolver_unidade_contexto(empresa) if pode_filtrar_unidade else None

    periodo = resolver_periodo_dashboard(empresa, unidades_visiveis)
    filtros = periodo.filtros
    area_ids = fila_ids_da_area(filtros)

    from app.utils.rotulos import rotulos_areas, rotulos_filas

    rotulos = rotulos_filas(empresa.id)
    filas_status = filas_com_status(empresa, unidades_visiveis, area_ids)
    for item in filas_status:
        item["rotulo"] = rotulos[item["fila"].id]

    # Mesmos filtros, explícitos, no link "ver detalhes" (Tempos por senha) e no
    # polling (adendo 10.86): a unidade da sessão nunca fica implícita - o
    # relatório não lê a sessão, e trocar a unidade em outra aba não pode mudar
    # o que o polling desta aba busca.
    querystring = {"data_inicio": filtros.data_inicio_str, "data_fim": filtros.data_fim_str}
    if filtros.area_fila_id:
        querystring["area_fila_id"] = filtros.area_fila_id
    if current_user.is_super_admin:
        querystring["empresa_id"] = empresa.id
    querystring_relatorio = dict(querystring)
    if unidade_escolhida is not None:
        querystring_relatorio["unidade_id"] = unidade_escolhida.id
    querystring_polling = dict(querystring)
    if pode_filtrar_unidade:
        querystring_polling["unidade_id"] = unidade_escolhida.id if unidade_escolhida is not None else ""

    return render_template(
        "core/dashboard.html",
        empresa=empresa,
        unidade=unidade_escolhida,
        pode_filtrar_unidade=pode_filtrar_unidade,
        pode_ver_relatorios=current_user.is_super_admin or current_user.tem_perfil("admin", "gerencial"),
        periodo=periodo,
        filtros=filtros,
        limite_dias=LIMITE_DIAS_DASHBOARD,
        areas=areas_visiveis_dashboard(empresa, unidades_visiveis),
        rotulos_areas=rotulos_areas(empresa.id),
        kpis=calcular_kpis(empresa, unidades_visiveis, periodo, com_tempos=True),
        tempos_dashboard=TEMPOS_DASHBOARD,
        filas_status=filas_status,
        filas_ativas=sum(1 for item in filas_status if item["fila"].ativo),
        chamadas=_ultimos_atendimentos_enriquecidos(empresa, unidades_visiveis, filtros, area_ids),
        grafico=senhas_geradas_no_periodo(periodo),
        querystring_relatorio=querystring_relatorio,
        querystring_polling=querystring_polling,
    )


@core_bp.route("/api/dashboard/kpis")
@login_required
def api_dashboard_kpis():
    """Endpoint JSON pro polling de 30s dos cartões (adendo 10.12.2, revisto no
    10.86) - os MESMOS filtros da página (datas, área, unidade explícita), o
    mesmo escopo por perfil. Contagens sempre; cartões de tempo e Prioritários x
    Normal só quando o período é exatamente hoje (tempos_por_senha de vários
    dias é caro demais para repetir a cada 30 s - adendo 10.86)."""
    empresa, selecao = _empresa_do_dashboard()
    if empresa is None:
        return jsonify({"erro": "Selecione uma empresa antes."}), 400

    unidades_visiveis = unidades_visiveis_dashboard(empresa, persistir=False)
    periodo = resolver_periodo_dashboard(empresa, unidades_visiveis)
    return jsonify(calcular_kpis(empresa, unidades_visiveis, periodo, com_tempos=periodo.exatamente_hoje))


def _ultimos_atendimentos_enriquecidos(empresa, unidades_visiveis, filtros, area_ids, limite=15):
    """Tabela de últimos atendimentos (adendo 10.12.2; 10.86) - as `limite`
    últimas chamadas DO PERÍODO e da Área, com fila/local do retrato do log
    (onde a senha foi chamada, 10.62). Colunas novas: Tipo (macro do 10.84) e
    Área. Nome só o primeiro; Observações (10.85) nunca entra."""
    from app.relatorios.helpers import mapas_nomes, rotulo_area
    from app.relatorios.queries import ultimas_chamadas

    chamadas = ultimas_chamadas(
        empresa, unidades_visiveis, limite=limite, data_inicio=filtros.data_inicio,
        data_fim_exclusivo=filtros.data_fim_exclusivo, fila_ids=area_ids, retrato_do_log=True,
    )
    mapas = mapas_nomes(empresa)
    senhas = {s.id: s for s in Senha.query.filter(Senha.id.in_({c["senha_id"] for c in chamadas}))} if chamadas else {}
    for chamada in chamadas:
        chamada["fila"] = mapas.filas.get(chamada["fila_id"], "—")
        chamada["area"] = rotulo_area(mapas, mapas.fila_area.get(chamada["fila_id"]))
        # Só identificador_admin - a coluna "Fila" já existe ao lado (BLUEPRINT.md
        # seção 10.32); um formato combinado "[fila] · [identificador_admin]" foi
        # tentado e descartado depois de conferência visual real: repetia o nome
        # da fila (já mostrado na coluna vizinha) e quebrava em 2 linhas na
        # maioria das linhas da tabela.
        chamada["local"] = mapas.locais.get(chamada["local_id"], "—") if chamada["local_id"] else "—"
        chamada["nome_paciente"] = primeiro_nome(chamada["nome_paciente"])
        chamada["senha"] = senhas.get(chamada["senha_id"])  # só para a célula Tipo
    return chamadas
