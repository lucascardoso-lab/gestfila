"""
Teste de regressão: senhas abertas de DIAS ANTERIORES (adendo 10.77 do BLUEPRINT).

Problema real em produção: ~450 senhas 'aguardando' de dias anteriores nunca
finalizadas - o encerramento das 23h (10.18) só cancelava 'chamada'/
'em_atendimento' e só rodava se algum request chegasse entre 23:00 e 23:59; as
telas/contagens/seleção de 'aguardando' não filtravam por dia (o FIFO por
`criada_em` punha a senha velha na FRENTE, e o BSG003 de ontem coexistia com o
de hoje no "Chamar por código").

Relógio simulado (patch de app.utils.tempo._instante_utc, mesmo padrão de
test_regressao_fuso_horario.py): "hoje" = 15/10/2026 07:00 de Brasília (10:00
UTC). Nenhum request às 23h em nenhum ponto do teste.

Execução: python -m tests.test_regressao_senhas_dias_anteriores (a partir da
raiz do projeto, com o virtualenv ativado).
"""
import os
import re

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app import create_app
from app.atendimento.helpers import (
    selecionar_proxima_fila_e_senha,
    selecionar_proxima_senha,
    senhas_aguardando,
    senhas_aguardando_local,
)
from app.core.dashboard_queries import calcular_kpis, filas_com_status
from app.extensions import db
from app.middleware import encerramento
from app.models import Empresa, Fila, Local, LocalRodizioFila, LogAtendimento, Perfil, Senha, Unidade, Usuario
from app.relatorios.helpers import Filtros
from app.relatorios.queries import (
    SITUACAO_CANCELADA,
    SITUACAO_EXPIRADA_APOS_ESPERA,
    SITUACAO_EXPIRADA_NUNCA_CHAMADA,
    _segmentos_espera_por_senha,
    contar_senhas_abertas_dias_anteriores,
    senhas_aguardando_por_fila,
    tempos_por_senha,
)

UTC = ZoneInfo("UTC")
SP = ZoneInfo("America/Sao_Paulo")

app = create_app("testing")

passos = []


def ok(msg):
    passos.append(msg)
    print("OK:", msg)


def em_brasilia(dia, hora, minuto=0):
    """Instante de Brasília (out/2026) -> UTC naive, como `criada_em` é gravado."""
    return datetime(2026, 10, dia, hora, minuto, tzinfo=SP).astimezone(UTC).replace(tzinfo=None)


def relogio(dia, hora, minuto=0):
    """Servidor no instante de Brasília dado (via o seam de UTC)."""
    return patch(
        "app.utils.tempo._instante_utc",
        return_value=datetime(2026, 10, dia, hora, minuto, tzinfo=SP).astimezone(UTC),
    )


HOJE_7H = (15, 7)

with app.app_context():
    db.create_all()
    perfil_atendente = Perfil(nome="atendente")
    perfil_admin = Perfil(nome="admin")
    db.session.add_all([perfil_atendente, perfil_admin])
    empresa = Empresa(nome="Clinica A")
    db.session.add(empresa)
    db.session.commit()
    unidade = Unidade(empresa_id=empresa.id, nome="Unidade A1")
    db.session.add(unidade)
    db.session.commit()
    fila = Fila(empresa_id=empresa.id, unidade_id=unidade.id, nome="Fila A", sigla="FA")
    db.session.add(fila)
    db.session.commit()
    local = Local(
        empresa_id=empresa.id, unidade_id=unidade.id, nome="Guiche 01",
        nome_exibicao="Guiche 01", identificador_admin="[UNI] Guiche 01",
    )
    db.session.add(local)
    db.session.commit()
    db.session.add(LocalRodizioFila(local_id=local.id, fila_id=fila.id, ordem=0, chamadas_seguidas=1))

    def novo_usuario(login, perfis):
        u = Usuario(empresa_id=empresa.id, nome=login, login=login)
        u.set_senha("senha123")
        u.perfis = perfis
        u.unidades = [unidade]
        db.session.add(u)
        db.session.commit()
        return u.id

    ids = dict(
        empresa=empresa.id, unidade=unidade.id, fila=fila.id, local=local.id,
        operador=novo_usuario("op_dias", [perfil_atendente]),
        admin=novo_usuario("admin_dias", [perfil_admin]),
    )


def nova_senha(codigo, status, criada_em, chamada_antes=False):
    """Senha + logs da jornada com snapshot (10.62). `chamada_antes`: gerada ->
    chamada_senha (+1 min) -> voltou_aguardar (+3 min), status 'aguardando'."""
    with app.app_context():
        em_andamento = status in ("chamada", "em_atendimento")
        s = Senha(
            empresa_id=ids["empresa"], unidade_id=ids["unidade"], fila_id=ids["fila"],
            codigo=codigo, sequencial=int(codigo[2:]), prioridade=False, status=status, criada_em=criada_em,
            local_id=ids["local"] if em_andamento else None,
            # em andamento fica com OUTRO usuário: uma 'chamada' de ontem no nome
            # do operador do teste bloquearia o Chamar dele ("Finalize o atendimento")
            operador_id=ids["admin"] if em_andamento else None,
            setor="recepcao" if em_andamento else None,
        )
        db.session.add(s)
        db.session.flush()
        logs = [("gerada", criada_em, None)]
        if em_andamento:  # toda senha em andamento real tem o log da chamada
            logs.append(("chamada_senha", criada_em.replace(minute=criada_em.minute + 1), ids["admin"]))
        if chamada_antes:
            logs += [
                ("chamada_senha", criada_em.replace(minute=criada_em.minute + 1), ids["operador"]),
                ("voltou_aguardar", criada_em.replace(minute=criada_em.minute + 3), ids["operador"]),
            ]
        for evento, quando, usuario in logs:
            db.session.add(LogAtendimento(
                senha_id=s.id, evento=evento, timestamp=quando, usuario_id=usuario,
                unidade_id=ids["unidade"], fila_id=ids["fila"],
                local_id=ids["local"] if usuario else None,
            ))
        db.session.commit()
        return s.id


def status_de(id_):
    with app.app_context():
        return db.session.get(Senha, id_).status


def logs_de(id_, evento):
    with app.app_context():
        return LogAtendimento.query.filter_by(senha_id=id_, evento=evento).all()


def login_como(login):
    c = app.test_client()
    resp = c.post("/login", data={"login": login, "senha": "senha123"}, follow_redirects=True)
    assert resp.status_code == 200, (login, resp.status_code)
    with c.session_transaction() as sess:
        sess["atendimento_local_id"] = ids["local"]
    return c


# ---------------------------------------------------------------------------
# Senhas: ontem (14/10) e hoje (15/10), dia civil de Brasília.
# ---------------------------------------------------------------------------
id_ontem_nunca = nova_senha("FA003", "aguardando", em_brasilia(14, 15))  # mesmo código da de hoje
id_ontem_voltou = nova_senha("FA007", "aguardando", em_brasilia(14, 16), chamada_antes=True)
id_ontem_2150 = nova_senha("FA008", "aguardando", em_brasilia(14, 21, 50))  # já é 15/10 em UTC
id_ontem_chamada = nova_senha("FA005", "chamada", em_brasilia(14, 17))
id_ontem_em_atend = nova_senha("FA006", "em_atendimento", em_brasilia(14, 18))
id_ontem_finalizada = nova_senha("FA004", "finalizada", em_brasilia(14, 10))
id_hoje_0005 = nova_senha("FA001", "aguardando", em_brasilia(15, 0, 5))
id_hoje_0630 = nova_senha("FA003", "aguardando", em_brasilia(15, 6, 30))
with app.app_context():
    assert em_brasilia(14, 21, 50).date() == datetime(2026, 10, 15).date()  # a armadilha UTC/clínica

ids_hoje = [id_hoje_0005, id_hoje_0630]
ids_abertas_ontem = [id_ontem_nunca, id_ontem_voltou, id_ontem_2150, id_ontem_chamada, id_ontem_em_atend]

# ===========================================================================
# A) Defesa em profundidade - varredura DESLIGADA (gate de TestingConfig): as
#    senhas de ontem continuam abertas no banco, e mesmo assim nenhum dos 9
#    pontos as lista, conta ou chama.
# ===========================================================================
assert app.config["ENCERRAMENTO_SESSOES_ATIVO"] is False

with relogio(*HOJE_7H), app.test_request_context():
    from flask import g

    g.is_super_admin, g.empresa_id, g.unidades_permitidas = False, ids["empresa"], [ids["unidade"]]
    f = db.session.get(Fila, ids["fila"])
    loc = db.session.get(Local, ids["local"])
    emp = db.session.get(Empresa, ids["empresa"])

    assert [s.id for s in senhas_aguardando(f)] == ids_hoje
    assert [s.id for s in senhas_aguardando_local(loc)] == ids_hoje
    ok("'Senhas em espera' (tela por fila e por Local): só as 2 de hoje, as de ontem não aparecem")

    assert selecionar_proxima_senha(f).id == id_hoje_0005
    f.prioridade_rodizio = 2
    assert selecionar_proxima_senha(f).id == id_hoje_0005
    f.prioridade_rodizio = 0
    assert selecionar_proxima_fila_e_senha(loc, None)[1].id == id_hoje_0005
    db.session.rollback()
    ok("Chamar automático (FIFO, rodízio de prioridade e rodízio por Local): chama a 1ª de HOJE, nunca a de ontem")

    kpis = calcular_kpis(emp, {ids["unidade"]})
    assert kpis["aguardando"] == 2, kpis
    contagem_status = {item["fila"].id: item["aguardando"] for item in filas_com_status(emp, {ids["unidade"]})}
    assert contagem_status == {ids["fila"]: 2}, contagem_status
    assert senhas_aguardando_por_fila(emp, [ids["unidade"]]) == {ids["fila"]: 2}
    ok("KPI 'Senhas aguardando agora', status das filas e 'aguardando por fila' (Relatórios): 2, não 5")

    assert contar_senhas_abertas_dias_anteriores(emp, [ids["unidade"]]) == 5
    ok("Contagem do alerta: 5 senhas abertas de dias anteriores (3 aguardando + chamada + em atendimento)")

with relogio(*HOJE_7H):
    c_op = login_como("op_dias")
    resp = c_op.get("/atendimento/filas")
    assert resp.status_code == 200
    texto = resp.data.decode("utf-8")
    contagem = re.search(r"<td>Fila A</td>\s*<td>\s*(\d+)", texto)
    assert contagem and contagem.group(1) == "2", texto
    ok("Lista de filas do atendimento: contagem 'aguardando' = 2 (só hoje)")

    # Chamar por código, tela por fila: FA003 existe ontem E hoje.
    resp = c_op.post(f"/atendimento/recepcao/filas/{ids['fila']}/chamar", data={"codigo": "FA003"})
    assert resp.status_code == 302
    assert status_de(id_hoje_0630) == "chamada" and status_de(id_ontem_nunca) == "aguardando"
    ok("Chamar por código (tela por fila): 'FA003' chama a de HOJE, a de ontem fica intocada")

    with app.app_context():  # devolve a de hoje para a fila (cenário seguinte)
        s = db.session.get(Senha, id_hoje_0630)
        s.status, s.chamada_em, s.local_id, s.operador_id, s.setor = "aguardando", None, None, None, None
        db.session.commit()

    resp = c_op.post(f"/atendimento/recepcao/local/{ids['local']}/chamar", data={"codigo": "FA003"})
    assert resp.status_code == 302
    assert status_de(id_hoje_0630) == "chamada" and status_de(id_ontem_nunca) == "aguardando"
    ok("Chamar por código (tela por Local): 'FA003' chama a de HOJE, a de ontem fica intocada")

    with app.app_context():
        s = db.session.get(Senha, id_hoje_0630)
        s.status, s.chamada_em, s.local_id, s.operador_id, s.setor = "aguardando", None, None, None, None
        LogAtendimento.query.filter_by(senha_id=id_hoje_0630, evento="chamada_senha").delete()
        db.session.commit()

    # Alerta do dashboard de Relatórios - a varredura "falhou" (está desligada)
    c_admin = login_como("admin_dias")
    texto = c_admin.get("/relatorios/dashboard").data.decode("utf-8")
    assert "5 senha(s) aberta(s) de dias anteriores" in texto
    assert "flask encerrar-pendentes --antes-de-hoje" in texto
    ok("Dashboard de Relatórios mostra o alerta '5 senha(s) aberta(s) de dias anteriores' (falha não fica mascarada)")

# ===========================================================================
# B) 1º request de HOJE às 07:00 - ninguém acessou às 23h de ontem. A varredura
#    por dia encerra as abertas de ontem; as de hoje ficam intactas.
# ===========================================================================
app.config["ENCERRAMENTO_SESSOES_ATIVO"] = True
app.config["HORARIO_ENCERRAMENTO_SESSOES"] = "23:00"
app.extensions.pop(encerramento._CHAVE_DIA_VARRIDO, None)

with relogio(*HOJE_7H):
    assert app.test_client().get("/login").status_code == 200  # request anônimo já dispara

for id_ in ids_abertas_ontem:
    assert status_de(id_) == "cancelada", id_
assert status_de(id_ontem_finalizada) == "finalizada"
for id_ in ids_hoje:
    assert status_de(id_) == "aguardando", id_
ok("1º request do dia às 07:00 (sem ninguém às 23h): as 5 abertas de ontem -> 'cancelada'; finalizada e as 2 de hoje intactas")

for id_ in (id_ontem_nunca, id_ontem_voltou, id_ontem_2150):
    logs = logs_de(id_, "expirada_aguardando")
    assert len(logs) == 1, (id_, logs)
    log = logs[0]
    assert log.usuario_id is None and log.fila_id == ids["fila"] and log.unidade_id == ids["unidade"]
    assert not logs_de(id_, "encerramento_automatico_sistema")
ok("'aguardando' de ontem: 1 log 'expirada_aguardando' cada, usuario_id NULL, snapshot de fila/unidade")

assert logs_de(id_ontem_2150, "expirada_aguardando")
ok("Senha das 21:50 de ontem (já 'hoje' em UTC) também expirou - dia civil da clínica, não de UTC")

for id_ in (id_ontem_chamada, id_ontem_em_atend):
    logs = logs_de(id_, "encerramento_automatico_sistema")
    assert len(logs) == 1 and logs[0].usuario_id is None and logs[0].local_id == ids["local"]
    assert not logs_de(id_, "expirada_aguardando")
ok("'chamada'/'em_atendimento' de ontem: 'encerramento_automatico_sistema' (semântica do 10.18), snapshot de Local")

with app.app_context():
    total_logs = LogAtendimento.query.count()
with relogio(15, 9):
    app.test_client().get("/login")
with app.app_context():
    assert LogAtendimento.query.count() == total_logs
    assert encerramento.encerrar_senhas_de_dias_anteriores() == 0
    assert LogAtendimento.query.count() == total_logs
ok("Idempotente: novo request no mesmo dia e chamada direta da função não gravam nenhum log a mais")

with relogio(*HOJE_7H), app.app_context():
    emp = db.session.get(Empresa, ids["empresa"])
    assert contar_senhas_abertas_dias_anteriores(emp, [ids["unidade"]]) == 0
with relogio(*HOJE_7H):
    texto = login_como("admin_dias").get("/relatorios/dashboard").data.decode("utf-8")
    assert "de dias anteriores" not in texto
ok("Depois da varredura o alerta some do dashboard de Relatórios")

# ===========================================================================
# C) UPDATE condicional (rowcount == 1): quem leu a senha "aberta" mas perdeu a
#    corrida para outra execução não grava log duplicado - nas duas funções.
# ===========================================================================
id_corrida = nova_senha("FA030", "chamada", em_brasilia(15, 8))
with app.app_context():
    senha = db.session.get(Senha, id_corrida)  # leitura "antiga": status 'chamada'
    db.session.execute(  # outra execução cancela antes
        Senha.__table__.update().where(Senha.__table__.c.id == id_corrida).values(status="cancelada")
    )
    assert encerramento._cancelar_se_status_ainda_for(senha, "chamada", "encerramento_automatico_sistema") is False
    db.session.commit()
assert not logs_de(id_corrida, "encerramento_automatico_sistema")
ok("Corrida: quem perdeu o UPDATE condicional (rowcount 0) não grava log")

id_23h = nova_senha("FA031", "em_atendimento", em_brasilia(15, 8))
with app.app_context():
    encerramento.cancelar_senhas_em_andamento()
    encerramento.cancelar_senhas_em_andamento()
assert status_de(id_23h) == "cancelada" and len(logs_de(id_23h, "encerramento_automatico_sistema")) == 1
ok("cancelar_senhas_em_andamento (23h) usa o mesmo UPDATE condicional: 1 log só, mesmo rodando 2x")

# ===========================================================================
# D) Virada para o dia seguinte: o memo "dia já varrido" não impede a
#    varredura de amanhã - uma senha de hoje que sobrar expira às 07:00 de 16/10.
# ===========================================================================
with relogio(16, 7):
    app.test_client().get("/login")
for id_ in ids_hoje:
    assert status_de(id_) == "cancelada" and len(logs_de(id_, "expirada_aguardando")) == 1
ok("Dia seguinte (16/10 07:00): as 'aguardando' de 15/10 expiram no 1º request - o memo é por dia")

# ===========================================================================
# E) Falha da varredura não derruba o sistema (before_request global) e só é
#    tentada de novo depois de INTERVALO_RETENTATIVA_VARREDURA (5 min) - TV e
#    totem fazem polling; sem o intervalo, cada request repetiria a falha.
# ===========================================================================
assert encerramento.INTERVALO_RETENTATIVA_VARREDURA.total_seconds() == 300
app.extensions.pop(encerramento._CHAVE_DIA_VARRIDO, None)
id_falha = nova_senha("FA050", "aguardando", em_brasilia(16, 10))
with patch.object(
    encerramento, "encerrar_senhas_de_dias_anteriores", side_effect=RuntimeError("banco fora")
) as varredura_quebrada:
    with relogio(17, 7):
        assert app.test_client().get("/login").status_code == 200
        assert encerramento._CHAVE_DIA_VARRIDO not in app.extensions
        assert varredura_quebrada.call_count == 1
    ok("Falha na varredura: request responde 200 normalmente, erro vai pro log, memo não é gravado")

    with relogio(17, 7, 4):
        for _ in range(3):  # polling de TV/totem dentro dos 5 min
            assert app.test_client().get("/login").status_code == 200
        assert varredura_quebrada.call_count == 1
    ok("Dentro de 5 min da falha: 3 requests seguidos NÃO tentam a varredura de novo")

with relogio(17, 7, 6):  # passou o intervalo, e o "banco" voltou
    app.test_client().get("/login")
assert status_de(id_falha) == "cancelada" and len(logs_de(id_falha, "expirada_aguardando")) == 1
assert app.extensions[encerramento._CHAVE_DIA_VARRIDO] == datetime(2026, 10, 17).date()
assert encerramento._CHAVE_ULTIMA_FALHA not in app.extensions
ok("Depois de 5 min: tenta de novo, encerra a senha pendente, grava o memo e limpa a falha")

# ===========================================================================
# F) Comando `flask encerrar-pendentes --antes-de-hoje [--dry-run]`.
# ===========================================================================
# Gate do middleware DESLIGADO: o comando não depende dele (só o gatilho
# automático depende) - daqui em diante só o comando age.
app.config["ENCERRAMENTO_SESSOES_ATIVO"] = False
id_cli_ontem = nova_senha("FA040", "aguardando", em_brasilia(17, 9))
id_cli_ontem_chamada = nova_senha("FA041", "chamada", em_brasilia(17, 10))
id_cli_hoje = nova_senha("FA042", "aguardando", em_brasilia(18, 6))
runner = app.test_cli_runner()

with relogio(18, 7):
    resultado = runner.invoke(args=["encerrar-pendentes"])
    assert resultado.exit_code != 0 and "--antes-de-hoje" in resultado.output, resultado.output
    assert status_de(id_cli_ontem) == "aguardando"
    ok("Comando sem --antes-de-hoje: recusa (exit != 0) e não altera nada")

    resultado = runner.invoke(args=["encerrar-pendentes", "--antes-de-hoje", "--dry-run"])
    assert resultado.exit_code == 0, resultado.output
    assert "Senhas abertas de dias anteriores: 2" in resultado.output, resultado.output
    assert "17/10/2026 | Clinica A | Unidade A1 | Fila A | aguardando | 1" in resultado.output, resultado.output
    assert "nada foi alterado" in resultado.output
    assert status_de(id_cli_ontem) == "aguardando" and status_de(id_cli_ontem_chamada) == "chamada"
    ok("--dry-run: lista por dia/empresa/unidade/fila/status e não altera nada")

    resultado = runner.invoke(args=["encerrar-pendentes", "--antes-de-hoje"])
    assert resultado.exit_code == 0 and "Encerradas: 2" in resultado.output, resultado.output
    assert status_de(id_cli_ontem) == "cancelada" and len(logs_de(id_cli_ontem, "expirada_aguardando")) == 1
    assert status_de(id_cli_ontem_chamada) == "cancelada"
    assert status_de(id_cli_hoje) == "aguardando"
    ok("Execução real (com ENCERRAMENTO_SESSOES_ATIVO=False): encerra as 2 de ontem com os eventos certos; a de hoje fica intacta")

    resultado = runner.invoke(args=["encerrar-pendentes", "--antes-de-hoje"])
    assert "Encerradas: 0" in resultado.output and len(logs_de(id_cli_ontem, "expirada_aguardando")) == 1
    ok("Comando rodado de novo: 0 encerradas, nenhum log duplicado")

# ===========================================================================
# G) Tempos por senha (10.73): situação decidida pelo histórico, e a espera
#    que terminou em expiração não entra em nenhum tempo/média.
# ===========================================================================
with app.app_context():
    filtros = Filtros(
        empresa_id=ids["empresa"], unidades_permitidas=None, unidade_id=None, fila_id=None, operador_id=None,
        data_inicio=em_brasilia(14, 0), data_fim_exclusivo=em_brasilia(15, 0),
        data_inicio_str="2026-10-14", data_fim_str="2026-10-14",
    )
    linhas, _etapas = tempos_por_senha(filtros)
    por_id = {l["codigo"]: l for l in linhas}
    assert por_id["FA003"]["situacao"] == SITUACAO_EXPIRADA_NUNCA_CHAMADA, por_id["FA003"]
    assert por_id["FA008"]["situacao"] == SITUACAO_EXPIRADA_NUNCA_CHAMADA
    assert por_id["FA007"]["situacao"] == SITUACAO_EXPIRADA_APOS_ESPERA, por_id["FA007"]
    assert por_id["FA005"]["situacao"] == SITUACAO_CANCELADA, por_id["FA005"]  # chamada de ontem: 10.18 intacto
    ok("Tempos por senha: 'Expirada (nunca chamada)' e 'Expirada (após realocação/volta à espera)' pelo histórico; 'chamada' encerrada segue 'Cancelada'")

    assert por_id["FA003"]["espera_inicial_segundos"] is None and por_id["FA003"]["espera_total_segundos"] is None
    assert por_id["FA007"]["espera_total_segundos"] == 60, por_id["FA007"]  # só a 1ª espera (1 min)
    segmentos = [seg for seg in _segmentos_espera_por_senha(filtros) if seg[0].senha_id == id_ontem_voltou]
    assert len(segmentos) == 1
    ok("Espera que terminou em expiração não entra nos tempos: expirada nunca chamada sem espera; a outra só com a 1ª (60 s)")

with relogio(*HOJE_7H):
    texto = login_como("admin_dias").get(
        "/relatorios/tempos-por-senha?data_inicio=2026-10-14&data_fim=2026-10-14"
    ).data.decode("utf-8")
    assert "Expirada (nunca chamada)" in texto and "Expirada (após realocação/volta à espera)" in texto
ok("Tela 'Tempos por senha' mostra as duas situações novas (com dica)")

print(f"\n{len(passos)} verificações OK.")
