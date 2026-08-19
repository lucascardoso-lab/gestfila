from datetime import time as dtime

from flask import current_app, flash, redirect, request, session, url_for
from flask_login import current_user, logout_user

from app.extensions import db
from app.utils.tempo import agora_na_clinica


def _horario_encerramento():
    """Lido do config a cada request (não cacheado no import) - deixa
    HORARIO_ENCERRAMENTO_SESSOES configurável via .env (seção 10.18) e também
    testável (um teste pode sobrescrever app.config e ver efeito imediato,
    sem precisar mockar datetime.now())."""
    bruto = current_app.config["HORARIO_ENCERRAMENTO_SESSOES"]
    horas, minutos = (int(parte) for parte in bruto.split(":"))
    return dtime(hour=horas, minute=minutos)


def cancelar_senhas_em_andamento():
    """Encerramento automático (seção 10.18): cancela toda senha ainda
    'chamada'/'em_atendimento' - a clínica fecha antes do horário configurado,
    então nunca há atendimento genuíno em curso nesse ponto. `status='cancelada'`
    (nunca DELETE, seção 8) + log de auditoria próprio.

    Idempotente e sem agendador externo (Celery/APScheduler) por design: rodar
    de novo quando não sobrou nenhuma senha aberta é um no-op barato (a query
    já filtra pelos dois status) - não precisa de flag "já rodei hoje". Basta
    QUALQUER request chegar ao servidor depois do horário de encerramento pra
    disparar (chamado a partir de um before_request global, não de uma rota
    específica) - cobre o caso de a aba do operador estar em background e o
    redirecionamento client-side (atendimento/_layout.html) não ter disparado.

    Roda para TODAS as empresas de uma vez (não é uma ação por-tenant) - é
    faxina de fim de expediente do sistema como um todo, não uma ação
    disparada por um usuário de uma empresa específica.
    """
    # import local evita ciclo de import no carregamento do app (mesmo motivo
    # já documentado em middleware/tenant.py para o import de Unidade)
    from app.models import LogAtendimento, Senha

    abertas = Senha.query.filter(Senha.status.in_(("chamada", "em_atendimento"))).all()
    if not abertas:
        return

    for senha in abertas:
        senha.status = "cancelada"
        db.session.add(
            LogAtendimento(
                senha_id=senha.id,
                evento="encerramento_automatico_sistema",
                usuario_id=None,  # ação do sistema, não de um operador logado
                unidade_id=senha.unidade_id,
                setor=senha.setor,
                fila_id=senha.fila_id,
                local_id=senha.local_id,
            )
        )
    db.session.commit()


def register_encerramento_middleware(app):
    @app.before_request
    def _encerramento_automatico():
        # Gate p/ hermeticidade da suíte (config.py): desligado em testing, para
        # uma rodada depois do horário (ex.: CI em UTC às 23:xx) não cancelar
        # senhas e derrubar sessões em TODO teste autenticado. Ligado em prod/dev.
        if not current_app.config.get("ENCERRAMENTO_SESSOES_ATIVO", True):
            return None
        # hora da CLÍNICA (fuso explícito, app/utils/tempo.py), não a hora do SO -
        # senão um servidor em UTC encerraria 3h cedo (ver 10.36).
        if agora_na_clinica().time() < _horario_encerramento():
            return None  # ainda não é hora - nenhum custo extra por request

        cancelar_senhas_em_andamento()

        # Backup server-side do redirecionamento client-side (atendimento/
        # _layout.html) - só afeta as telas de Atendimento (item 4 do pedido:
        # totens/TVs são públicos, sem sessão de operador, e continuam
        # funcionando normalmente; admin também não é derrubado, já que
        # configurar o sistema depois das 23h não é "atendimento em curso").
        if request.blueprint == "atendimento" and current_user.is_authenticated:
            # import local evita ciclo de import no carregamento do app (mesmo
            # motivo já documentado acima para o import de LogAtendimento/Senha)
            from app.models import TurnoAtivo

            TurnoAtivo.query.filter_by(usuario_id=current_user.id, ativo=True).update({"ativo": False})
            current_user.local_atual_id = None
            current_user.local_desde = None
            db.session.commit()
            logout_user()
            session.clear()
            flash("O sistema foi encerrado automaticamente (fim do expediente).", "info")
            return redirect(url_for("auth.login"))

        return None
