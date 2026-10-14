from datetime import time as dtime
from datetime import timedelta

from flask import current_app, flash, redirect, request, session, url_for
from flask_login import current_user, logout_user
from sqlalchemy import update

from app.extensions import db
from app.utils.tempo import agora_na_clinica

# Varredura diária de senhas de dias anteriores (adendo 10.77) - estado por
# processo em `app.extensions`: o dia já varrido (memo) e o instante da última
# falha (intervalo mínimo entre tentativas).
_CHAVE_DIA_VARRIDO = "encerramento_dia_varrido"
_CHAVE_ULTIMA_FALHA = "encerramento_ultima_falha_varredura"
INTERVALO_RETENTATIVA_VARREDURA = timedelta(minutes=5)


def _horario_encerramento():
    """Lido do config a cada request (não cacheado no import) - deixa
    HORARIO_ENCERRAMENTO_SESSOES configurável via .env (seção 10.18) e também
    testável (um teste pode sobrescrever app.config e ver efeito imediato,
    sem precisar mockar datetime.now())."""
    bruto = current_app.config["HORARIO_ENCERRAMENTO_SESSOES"]
    horas, minutos = (int(parte) for parte in bruto.split(":"))
    return dtime(hour=horas, minute=minutos)


def _cancelar_se_status_ainda_for(senha, status_lido, evento):
    """Cancela `senha` só se o status no banco ainda for `status_lido` - UPDATE
    condicional (adendo 10.77): duas execuções concorrentes (greenlets do
    eventlet na virada do dia, ou o comando `flask encerrar-pendentes` rodando
    junto com o servidor) que leram a mesma senha não gravam o log de auditoria
    em dobro - só quem de fato mudou a linha (rowcount == 1) grava. `status=
    'cancelada'`, nunca DELETE (seção 8). Não commita - quem chama decide a
    transação."""
    from app.models import LogAtendimento, Senha

    resultado = db.session.execute(
        update(Senha)
        .where(Senha.id == senha.id, Senha.status == status_lido)
        .values(status="cancelada")
        .execution_options(synchronize_session=False)
    )
    if resultado.rowcount != 1:
        return False
    senha.status = "cancelada"  # mantém o objeto da sessão coerente com o banco
    db.session.add(
        LogAtendimento(
            senha_id=senha.id,
            evento=evento,
            usuario_id=None,  # ação do sistema, não de um operador logado
            unidade_id=senha.unidade_id,
            setor=senha.setor,
            fila_id=senha.fila_id,
            local_id=senha.local_id,
        )
    )
    return True


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
    Se NENHUM request chegar até a meia-noite, as senhas que sobraram são
    encerradas no dia seguinte por `encerrar_senhas_de_dias_anteriores`
    (adendo 10.77).

    Roda para TODAS as empresas de uma vez (não é uma ação por-tenant) - é
    faxina de fim de expediente do sistema como um todo, não uma ação
    disparada por um usuário de uma empresa específica.
    """
    # import local evita ciclo de import no carregamento do app (mesmo motivo
    # já documentado em middleware/tenant.py para o import de Unidade)
    from app.models import Senha

    abertas = Senha.query.filter(Senha.status.in_(("chamada", "em_atendimento"))).all()
    if not abertas:
        return

    for senha in abertas:
        _cancelar_se_status_ainda_for(senha, senha.status, "encerramento_automatico_sistema")
    db.session.commit()


def encerrar_senhas_de_dias_anteriores():
    """Encerramento por DIA da clínica (adendo 10.77) - complementa o das 23h
    (10.18), que é por relógio e só roda se algum request chegar entre o
    horário de encerramento e a meia-noite. Cancela toda senha aberta criada
    antes de hoje: 'aguardando' -> evento 'expirada_aguardando';
    'chamada'/'em_atendimento' -> 'encerramento_automatico_sistema' (mesma
    semântica do 10.18). Uma transação só (um commit no fim). Idempotente:
    rodar de novo não acha nada. Devolve a quantidade de senhas encerradas."""
    from app.models import Senha
    from app.models.senha import senhas_abertas_de_dias_anteriores

    encerradas = 0
    for senha in senhas_abertas_de_dias_anteriores().order_by(Senha.id).all():
        status_lido = senha.status
        evento = "expirada_aguardando" if status_lido == "aguardando" else "encerramento_automatico_sistema"
        if _cancelar_se_status_ainda_for(senha, status_lido, evento):
            encerradas += 1
    db.session.commit()
    return encerradas


def _varrer_dias_anteriores_uma_vez_por_dia():
    """Roda `encerrar_senhas_de_dias_anteriores` no 1º request de cada dia da
    clínica (por processo) - a qualquer hora, não só depois das 23h. O memo
    (dia já varrido, em `app.extensions`) evita uma query a cada request (TV,
    polling, estáticos); é seguro porque, depois da varredura, nenhuma senha
    de antes de hoje volta a ficar aberta ('cancelada' não reabre e senha nova
    nasce com `criada_em` = agora). O memo só é gravado depois do commit.

    Falha da varredura NÃO derruba o request (roda num before_request global -
    uma exceção aqui viraria 500 em todo o sistema, totem e TV inclusive): vai
    para o log e as senhas que sobrarem aparecem no alerta "senhas abertas de
    dias anteriores" do dashboard de Relatórios. Depois de uma falha, a próxima
    tentativa só acontece após `INTERVALO_RETENTATIVA_VARREDURA` - TV e totem
    fazem polling, e sem o intervalo uma falha persistente (ex.: banco
    recusando o UPDATE) repetiria a query e o stack trace a cada request."""
    agora = agora_na_clinica()
    hoje = agora.date()
    if current_app.extensions.get(_CHAVE_DIA_VARRIDO) == hoje:
        return
    ultima_falha = current_app.extensions.get(_CHAVE_ULTIMA_FALHA)
    if ultima_falha is not None and agora - ultima_falha < INTERVALO_RETENTATIVA_VARREDURA:
        return
    try:
        encerrar_senhas_de_dias_anteriores()
    except Exception:
        db.session.rollback()
        current_app.extensions[_CHAVE_ULTIMA_FALHA] = agora
        current_app.logger.exception(
            "Falha ao encerrar senhas de dias anteriores (adendo 10.77) - nova tentativa em %s",
            INTERVALO_RETENTATIVA_VARREDURA,
        )
        return
    current_app.extensions.pop(_CHAVE_ULTIMA_FALHA, None)
    current_app.extensions[_CHAVE_DIA_VARRIDO] = hoje


def register_encerramento_middleware(app):
    @app.before_request
    def _encerramento_automatico():
        # Gate p/ hermeticidade da suíte (config.py): desligado em testing, para
        # uma rodada depois do horário (ex.: CI em UTC às 23:xx) não cancelar
        # senhas e derrubar sessões em TODO teste autenticado. Ligado em prod/dev.
        if not current_app.config.get("ENCERRAMENTO_SESSOES_ATIVO", True):
            return None
        # Senhas abertas de DIAS ANTERIORES (adendo 10.77): a qualquer hora, no
        # 1º request do dia - sem isso, um dia em que ninguém acessou entre
        # 23:00 e 23:59 deixava senhas abertas para sempre (o gatilho abaixo é
        # só por relógio e às 07:00 já é "antes das 23:00" de novo).
        _varrer_dias_anteriores_uma_vez_por_dia()

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
            # motivo já documentado acima para o import de Senha)
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
