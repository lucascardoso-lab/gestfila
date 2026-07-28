from flask import redirect, render_template, url_for

from app.extensions import limiter
from app.models import Tv

from . import painel_bp


@painel_bp.route("/tv/<int:tv_id>")
@limiter.limit("30 per minute")
def exibir_tv_por_id(tv_id):
    """URL antiga por ID (pré-slug) - mantida como redirect permanente por segurança
    operacional: TVs físicas já configuradas com esse link continuam funcionando sem
    precisar reconfigurar o equipamento manualmente."""
    tv = Tv.query.get_or_404(tv_id)
    return redirect(url_for("painel.exibir_tv", slug=tv.slug), code=302)


@painel_bp.route("/tv/<slug>")
@limiter.limit("30 per minute")
def exibir_tv(slug):
    """Painel público de uma TV (seção 5.6/6 do BLUEPRINT) - sem login, acesso por URL fixa.

    Esta fase só renderiza a tela e conecta ao WebSocket (infraestrutura da Fase 5); a
    emissão real do evento 'chamada' a partir de uma ação de operador é a Fase 7.
    """
    tv = Tv.query.filter_by(slug=slug).first_or_404()
    if not tv.ativo:
        return render_template("painel/indisponivel.html", tv=tv)

    return render_template("painel/tv.html", tv=tv, empresa=tv.empresa)
