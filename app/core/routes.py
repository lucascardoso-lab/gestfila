from flask import Blueprint, g, jsonify, render_template
from flask_login import current_user, login_required

from app.admin.helpers import resolver_empresa_contexto, resolver_unidade_contexto
from app.models import Empresa

from .dashboard_queries import (
    calcular_kpis,
    chamadas_por_hora_hoje,
    filas_com_status,
    primeiro_nome,
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


@core_bp.route("/dashboard")
@login_required
def dashboard():
    """Dashboard operacional (adendo 10.12.2) - tela padrão pós-login pra
    admin/gerencial/super admin (login.py::_deve_ir_direto_para_turno manda os
    perfis puramente operacionais direto pra 'Iniciar Turno' em vez daqui, seção
    10.19/10.23 - mas nada impede um atendente/executor de visitar /dashboard
    manualmente, com o escopo mais restrito descrito em unidades_visiveis_dashboard)."""
    empresa, selecao = _empresa_do_dashboard()
    if empresa is None:
        return selecao

    unidades_visiveis = unidades_visiveis_dashboard(empresa)

    # Mesma regra de visibilidade da barra de contexto (seção 10.22): só quem pode
    # efetivamente estreitar por unidade (admin/gerencial/super admin) vê o select -
    # atendente/executor não tem escolha nenhuma aqui (unidades_visiveis_dashboard
    # nunca chama resolver_unidade_contexto pra esse grupo).
    pode_filtrar_unidade = current_user.is_super_admin or current_user.tem_perfil("admin", "gerencial")
    unidade_escolhida = resolver_unidade_contexto(empresa) if pode_filtrar_unidade else None

    chamadas = _ultimos_atendimentos_enriquecidos(empresa, unidades_visiveis)

    from app.utils.rotulos import rotulos_filas

    rotulos = rotulos_filas(empresa.id)
    filas_status = filas_com_status(empresa, unidades_visiveis)
    for item in filas_status:
        item["rotulo"] = rotulos[item["fila"].id]

    return render_template(
        "core/dashboard.html",
        empresa=empresa,
        unidade=unidade_escolhida,
        pode_filtrar_unidade=pode_filtrar_unidade,
        kpis=calcular_kpis(empresa, unidades_visiveis),
        filas_status=filas_status,
        chamadas=chamadas,
        timestamps_chamadas_hoje=chamadas_por_hora_hoje(unidades_visiveis),
    )


@core_bp.route("/api/dashboard/kpis")
@login_required
def api_dashboard_kpis():
    """Endpoint JSON pro polling de 30s dos 4 cartões de KPI (adendo 10.12.2, item
    3) - só os KPIs, não a página inteira; o mesmo escopo por perfil de /dashboard."""
    empresa, selecao = _empresa_do_dashboard()
    if empresa is None:
        return jsonify({"erro": "Selecione uma empresa antes."}), 400

    unidades_visiveis = unidades_visiveis_dashboard(empresa)
    return jsonify(calcular_kpis(empresa, unidades_visiveis))


def _ultimos_atendimentos_enriquecidos(empresa, unidades_visiveis, limite=15):
    """Tabela de últimos atendimentos (adendo 10.12.2) - reaproveita `ultimas_chamadas`
    já existente em relatorios/queries.py (sem alterar nada lá), só enriquecendo com
    nome de fila/local (mesmo padrão de relatorios/routes.py::dashboard) e truncando
    o nome do paciente pro primeiro nome (item 5 do pedido)."""
    from app.relatorios.helpers import mapas_nomes
    from app.relatorios.queries import ultimas_chamadas

    chamadas = ultimas_chamadas(empresa, unidades_visiveis, limite=limite)
    mapas = mapas_nomes(empresa)
    for chamada in chamadas:
        chamada["fila"] = mapas.filas.get(chamada["fila_id"], "—")
        # Só identificador_admin - a coluna "Fila" já existe ao lado (BLUEPRINT.md
        # seção 10.32); um formato combinado "[fila] · [identificador_admin]" foi
        # tentado e descartado depois de conferência visual real: repetia o nome
        # da fila (já mostrado na coluna vizinha) e quebrava em 2 linhas na
        # maioria das linhas da tabela.
        chamada["local"] = mapas.locais.get(chamada["local_id"], "—") if chamada["local_id"] else "—"
        chamada["nome_paciente"] = primeiro_nome(chamada["nome_paciente"])
    return chamadas
