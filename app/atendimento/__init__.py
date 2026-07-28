from flask import Blueprint, current_app

atendimento_bp = Blueprint("atendimento", __name__, url_prefix="/atendimento")


@atendimento_bp.context_processor
def _injetar_config_encerramento():
    """Disponibiliza o horário de encerramento automático (seção 10.18) pra
    TODOS os templates deste blueprint sem precisar passar via render_template
    em cada rota - atendimento/_layout.html usa isso pro banner/contagem
    regressiva client-side."""
    return {
        "horario_encerramento_sessoes": current_app.config["HORARIO_ENCERRAMENTO_SESSOES"],
        "aviso_encerramento_minutos": current_app.config["AVISO_ENCERRAMENTO_MINUTOS"],
    }


from . import chamada, filas, local, turno  # noqa: E402  (registram rotas no atendimento_bp)

__all__ = ["atendimento_bp"]
