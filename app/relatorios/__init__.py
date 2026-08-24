from flask import Blueprint

from app.utils.decorators import roles_required

relatorios_bp = Blueprint("relatorios", __name__, url_prefix="/relatorios")

PERFIS_RELATORIOS = ("gerencial", "admin")


@relatorios_bp.before_request
@roles_required(*PERFIS_RELATORIOS)
def proteger_relatorios():
    """Único ponto de RBAC desta área: só 'gerencial' e 'admin' têm acesso a
    relatórios (seção 3 do BLUEPRINT - recepção/coleta nunca acessam relatórios)."""
    return None


@relatorios_bp.context_processor
def _contexto_padrao_relatorios():
    """Defaults pros 4 templates de relatório (tempo_espera.html/tempo_
    atendimento.html/volume.html/ranking.html), que continuam com {% extends
    layout_relatorio %} em vez de um caminho fixo (adendo 10.67, exportação em
    PDF): renderização normal em TELA usa relatorios/_layout.html (chrome
    completo - nav, filtros interativos); a exportação em PDF passa
    layout_relatorio="relatorios/_layout_impressao.html" explicitamente em
    render_template() (app/relatorios/routes.py::_exportar_relatorio) - um
    kwarg explícito sempre vence o default de um context_processor, então
    isso é seguro sem precisar tocar nos 4 `render_template()` de tela."""
    return {"layout_relatorio": "relatorios/_layout.html", "modo_impressao": False}


from . import routes  # noqa: E402  (registra rotas no relatorios_bp)

__all__ = ["relatorios_bp"]
