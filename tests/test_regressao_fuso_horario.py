"""
Teste de regressão: decisões de "relógio de parede" da clínica usam o FUSO DA
CLÍNICA (app/utils/tempo.py, config FUSO_HORARIO = America/Sao_Paulo por padrão),
NÃO a hora do SO do servidor (BLUEPRINT.md, 10.36).

Risco real que motivou: o encerramento automático (10.18) e o horário de
funcionamento das filas (Fila.aberta_em, 10.1) usavam datetime.now() (naive, fuso
do SO). Um deploy Linux em UTC (default comum) dispararia o encerramento das
23:00 às 20:00 de Brasília e avaliaria filas abertas/fechadas 3h fora. Como
config de fuso do SO é frágil (redeploy/reconfiguração reseta sem avisar), o fuso
foi resolvido no CÓDIGO.

Cada teste SIMULA o relógio do servidor num instante UTC (patch de
app.utils.tempo._instante_utc) e confirma que a decisão sai no horário de
Brasília, não 3h adiantada. Com o código antigo (datetime.now() naive num
servidor UTC), estes casos dariam o resultado ERRADO - é exatamente essa
diferença que o teste trava.

Execução: python -m tests.test_regressao_fuso_horario (a partir da raiz do
projeto, com o virtualenv ativado).
"""
import os

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app import create_app
from app.extensions import db
from app.models import Empresa, Fila, Local, Perfil, Senha, Unidade, Usuario
from app.models.fila import DIAS_SEMANA
from app.utils.tempo import agora_na_clinica

app = create_app("testing")

UTC = ZoneInfo("UTC")

passos = []


def ok(msg):
    passos.append(msg)
    print("OK:", msg)


def servidor_em_utc(ano, mes, dia, hora, minuto):
    """Contexto que finge que o relógio do servidor está NESSE instante UTC -
    simula um deploy Linux em UTC. O código sob teste, se correto, converte pro
    fuso da clínica antes de decidir."""
    return patch("app.utils.tempo._instante_utc", return_value=datetime(ano, mes, dia, hora, minuto, tzinfo=UTC))


with app.app_context():
    db.create_all()


# ===========================================================================
# A) Conversão base: America/Sao_Paulo é UTC-3 (sem horário de verão desde 2019).
#    02:30 UTC = 23:30 em Brasília; 01:30 UTC = 22:30 em Brasília.
# ===========================================================================
with app.app_context():
    with servidor_em_utc(2026, 6, 15, 2, 30):
        agora = agora_na_clinica()
        assert agora.hour == 23 and agora.minute == 30, agora
        assert agora.utcoffset().total_seconds() == -3 * 3600  # UTC-3
    with servidor_em_utc(2026, 6, 15, 1, 30):
        assert agora_na_clinica().time().strftime("%H:%M") == "22:30"
ok("agora_na_clinica(): servidor em UTC 02:30 -> 23:30 Brasília (UTC-3), independente do fuso do SO")

# ===========================================================================
# B) Fila.aberta_em() usa o fuso da clínica: fila aberta SÓ à tarde (12:00-23:59).
#    Às 23:30 Brasília (02:30 UTC) está ABERTA; às 10:30 Brasília (13:30 UTC),
#    FECHADA. Com datetime.now() naive num servidor UTC, 02:30 cairia em "manhã"
#    e a fila apareceria FECHADA (errado) - é o bug que este caso trava.
# ===========================================================================
with app.app_context():
    empresa = Empresa(nome="Clinica Fuso")
    db.session.add(empresa)
    db.session.commit()
    unidade = Unidade(empresa_id=empresa.id, nome="Unidade F")
    db.session.add(unidade)
    db.session.commit()

    so_a_tarde = {dia: {"manha": False, "tarde": True} for dia in DIAS_SEMANA}
    fila_tarde = Fila(
        empresa_id=empresa.id, unidade_id=unidade.id, nome="Fila Tarde", sigla="FT",
        horario_funcionamento=so_a_tarde,
    )
    db.session.add(fila_tarde)
    db.session.commit()

    with servidor_em_utc(2026, 6, 15, 2, 30):  # 23:30 Brasília -> tarde
        assert fila_tarde.aberta_em() is True
    with servidor_em_utc(2026, 6, 15, 13, 30):  # 10:30 Brasília -> manhã
        assert fila_tarde.aberta_em() is False

    EMPRESA_ID = empresa.id
    UNIDADE_ID = unidade.id
ok("Fila.aberta_em(): 'tarde' avaliada em 23:30 Brasília=ABERTA / 10:30 Brasília=FECHADA, mesmo com servidor em UTC")


# ===========================================================================
# C) Encerramento automático (end-to-end) com HORÁRIO PADRÃO "23:00" e servidor
#    em UTC: dispara às 23:30 Brasília (02:30 UTC), NÃO às 22:30 Brasília. Com o
#    código antigo, 02:30 UTC naive seria < 23:00 e NÃO encerraria (3h de erro).
# ===========================================================================
with app.app_context():
    perfil = Perfil(nome="atendente")
    db.session.add(perfil)
    db.session.commit()

    unidade = db.session.get(Unidade, UNIDADE_ID)  # re-bind ao contexto atual
    local = Local(
        empresa_id=EMPRESA_ID, unidade_id=UNIDADE_ID, nome="Guiche F",
        nome_exibicao="Guiche F", identificador_admin="[UF] Guiche F",
    )
    fila_op = Fila(empresa_id=EMPRESA_ID, unidade_id=UNIDADE_ID, nome="Fila Op", sigla="FO")
    db.session.add_all([local, fila_op])
    db.session.commit()

    def novo_operador(login):
        u = Usuario(empresa_id=EMPRESA_ID, nome=login, login=login)
        u.set_senha("senha123")
        u.perfis = [perfil]
        u.unidades = [unidade]
        db.session.add(u)
        db.session.commit()
        return u.id

    id_op1 = novo_operador("op_fuso_1")
    id_op2 = novo_operador("op_fuso_2")
    ids = dict(empresa=EMPRESA_ID, unidade=UNIDADE_ID, fila_op=fila_op.id, local=local.id, op1=id_op1, op2=id_op2)

# encerramento ligado (gate off por padrão em testing) + horário PADRÃO real
app.config["ENCERRAMENTO_SESSOES_ATIVO"] = True
app.config["HORARIO_ENCERRAMENTO_SESSOES"] = "23:00"

URL_FILA = f"/atendimento/recepcao/filas/{ids['fila_op']}"


def login_op(login, local_id):
    c = app.test_client()
    resp = c.post("/login", data={"login": login, "senha": "senha123"}, follow_redirects=True)
    assert resp.status_code == 200, (login, resp.status_code)
    with c.session_transaction() as sess:
        sess["atendimento_local_id"] = local_id
    return c


def nova_senha_chamada(codigo, operador_id):
    with app.app_context():
        s = Senha(
            empresa_id=ids["empresa"], unidade_id=ids["unidade"], fila_id=ids["fila_op"],
            codigo=codigo, sequencial=1, prioridade=False, status="chamada",
            local_id=ids["local"], operador_id=operador_id, setor="recepcao",
        )
        db.session.add(s)
        db.session.commit()
        return s.id


# C.1) 22:30 Brasília (01:30 UTC) - ANTES das 23:00: NÃO encerra.
id_senha_antes = nova_senha_chamada("FO001", ids["op1"])
c1 = login_op("op_fuso_1", ids["local"])
with servidor_em_utc(2026, 6, 15, 1, 30):
    resp = c1.get(URL_FILA, follow_redirects=False)
assert resp.status_code == 200, resp.status_code
with app.app_context():
    assert db.session.get(Senha, id_senha_antes).status == "chamada"  # não cancelada
ok("Encerramento: 22:30 Brasília (servidor 01:30 UTC) NÃO encerra - tela abre normal, senha intacta")

# C.2) 23:30 Brasília (02:30 UTC) - APÓS as 23:00: encerra. (Código antigo naive
#      leria 02:30 < 23:00 e NÃO encerraria - erro de 3h que este caso prova.)
id_senha_apos = nova_senha_chamada("FO002", ids["op2"])
c2 = login_op("op_fuso_2", ids["local"])
with servidor_em_utc(2026, 6, 15, 2, 30):
    resp = c2.get(URL_FILA, follow_redirects=False)
assert resp.status_code == 302 and resp.headers["Location"].startswith("/login"), (resp.status_code, resp.headers.get("Location"))
with app.app_context():
    assert db.session.get(Senha, id_senha_apos).status == "cancelada"  # encerrada
ok("Encerramento: 23:30 Brasília (servidor 02:30 UTC) ENCERRA - redireciona pro login e cancela a senha (sem os 3h de erro)")

print()
print(f"TODOS OS {len(passos)} TESTES DE FUSO HORÁRIO PASSARAM")
