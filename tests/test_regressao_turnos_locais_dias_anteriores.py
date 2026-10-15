"""
Teste de regressão: turnos e Locais de DIAS ANTERIORES (adendo 10.79 do BLUEPRINT).

Problema real em produção (15/10/2026): 11 usuários com `local_atual_id` de
dias anteriores (o mais antigo desde 18/09) e 13 `TurnoAtivo` ainda `ativo=1`
de dias anteriores (o mais antigo desde 01/09). O backup das 23h (10.18) só
encerra o turno do `current_user` que fizer um request em `atendimento` depois
das 23h - os demais ficavam abertos, e um `local_atual_id` de ontem bloqueava
o Local no dia seguinte ("já está em uso por ..."). 2 dos 13 turnos eram de
usuários SEM local: o "Desconectar" da tela de ocupação liberava o Local mas
não desativava o turno (bug também corrigido no 10.79).

Relógio simulado (patch de app.utils.tempo._instante_utc, mesmo padrão de
test_regressao_senhas_dias_anteriores.py): "hoje" = 15/10/2026 07:00 de
Brasília (10:00 UTC).

Execução: python -m tests.test_regressao_turnos_locais_dias_anteriores (a
partir da raiz do projeto, com o virtualenv ativado).
"""
import os

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app import create_app
from app.extensions import db
from app.middleware import encerramento
from app.models import Empresa, Fila, Local, Perfil, Senha, TurnoAtivo, Unidade, Usuario

UTC = ZoneInfo("UTC")
SP = ZoneInfo("America/Sao_Paulo")

app = create_app("testing")

passos = []


def ok(msg):
    passos.append(msg)
    print("OK:", msg)


def em_brasilia(mes, dia, hora, minuto=0):
    """Instante de Brasília (2026) -> UTC naive, como `iniciado_em`/`local_desde` são gravados."""
    return datetime(2026, mes, dia, hora, minuto, tzinfo=SP).astimezone(UTC).replace(tzinfo=None)


def relogio(dia, hora, minuto=0):
    """Servidor no instante de Brasília dado (outubro/2026, via o seam de UTC)."""
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
    locais = []
    for n in range(1, 7):
        locais.append(Local(
            empresa_id=empresa.id, unidade_id=unidade.id, nome=f"Guiche 0{n}",
            nome_exibicao=f"Guiche 0{n}", identificador_admin=f"[UNI] Guiche 0{n}",
        ))
    db.session.add_all(locais)
    db.session.commit()

    def novo_usuario(login, perfis, local=None, desde=None, turno_em=None, turno_local=None, turno_ativo=True):
        u = Usuario(empresa_id=empresa.id, nome=login, login=login)
        u.set_senha("senha123")
        u.perfis = perfis
        u.unidades = [unidade]
        u.local_atual_id = local.id if local else None
        u.local_desde = desde
        db.session.add(u)
        db.session.commit()
        if turno_em is not None:
            db.session.add(TurnoAtivo(
                usuario_id=u.id, local_id=(turno_local or local).id,
                filas_config=[{"fila_id": fila.id, "ordem": 0, "chamadas_seguidas": 1}],
                iniciado_em=turno_em, ativo=turno_ativo,
            ))
            db.session.commit()
        return u.id

    L1, L2, L3, L4, L5, L6 = locais
    ids = dict(
        empresa=empresa.id, unidade=unidade.id, fila=fila.id,
        L1=L1.id, L2=L2.id, L3=L3.id, L4=L4.id, L5=L5.id, L6=L6.id,
        # turno + local de ontem à tarde -> os dois liberados
        ontem=novo_usuario("op_ontem", [perfil_atendente], L1, em_brasilia(10, 14, 15), em_brasilia(10, 14, 15)),
        # 21:50 de ontem em Brasília = 00:50 de HOJE em UTC: a armadilha UTC/clínica
        tarde=novo_usuario("op_2150", [perfil_atendente], L2, em_brasilia(10, 14, 21, 50), em_brasilia(10, 14, 21, 50)),
        # turno + local de hoje -> intactos
        hoje=novo_usuario("op_hoje", [perfil_atendente], L3, em_brasilia(10, 15, 6, 30), em_brasilia(10, 15, 6, 30)),
        # o caso "usuário 4" de produção: turno ativo desde 01/09, SEM local
        # (foi "Desconectado" e o turno ficou ativo) - o Local dele já está com outro
        sem_local=novo_usuario("op_desconectado", [perfil_atendente], turno_em=em_brasilia(9, 1, 8), turno_local=L3),
        # local_atual_id sem local_desde: não existe em produção - não é liberado
        desde_nulo=novo_usuario("op_desde_nulo", [perfil_atendente], L4),
        # turno de ontem JÁ encerrado: fora da conta
        inativo=novo_usuario("op_inativo", [perfil_atendente], turno_em=em_brasilia(10, 14, 9), turno_local=L5,
                             turno_ativo=False),
        novo=novo_usuario("op_novo", [perfil_atendente]),
        admin=novo_usuario("admin_turnos", [perfil_admin]),
    )


def usuario(chave):
    with app.app_context():
        u = db.session.get(Usuario, ids[chave])
        db.session.expunge(u)
        return u


def turno_ativo(chave):
    with app.app_context():
        return TurnoAtivo.query.filter_by(usuario_id=ids[chave]).one().ativo


def login_como(login):
    c = app.test_client()
    resp = c.post("/login", data={"login": login, "senha": "senha123"}, follow_redirects=True)
    assert resp.status_code == 200, (login, resp.status_code)
    return c


def autenticado(cliente):
    resp = cliente.get("/atendimento/local", follow_redirects=False)
    return resp.status_code == 200


# ===========================================================================
# A) Antes da varredura (gate desligado, como na suíte): o bug de produção -
#    o Local de ontem bloqueia outro operador hoje.
# ===========================================================================
assert app.config["ENCERRAMENTO_SESSOES_ATIVO"] is False

with relogio(*HOJE_7H):
    # sessões que já existem ANTES da varredura - nenhuma pode cair
    c_ontem = login_como("op_ontem")
    c_hoje = login_como("op_hoje")
    versoes = {chave: usuario(chave).sessao_versao for chave in ("ontem", "tarde", "hoje", "sem_local", "desde_nulo")}

    c_novo = login_como("op_novo")
    resp = c_novo.post("/atendimento/local", data={"local_id": ids["L1"]}, follow_redirects=True)
    assert "já está em uso por op_ontem".encode("utf-8") in resp.data
    assert usuario("novo").local_atual_id is None
    ok("Bug reproduzido: o Local ocupado desde ontem por op_ontem bloqueia op_novo hoje")

    resultado = app.test_cli_runner().invoke(args=["encerrar-pendentes", "--antes-de-hoje", "--dry-run"])
    assert resultado.exit_code == 0, resultado.output
    saida = resultado.output
    assert "Turnos ativos de dias anteriores: 3" in saida, saida
    assert "Locais ocupados desde dias anteriores: 2" in saida, saida
    assert f"01/09/2026 | Clinica A | op_desconectado (id {ids['sem_local']}) | [UNI] Guiche 03 (id {ids['L3']})" in saida, saida
    assert f"14/10/2026 | Clinica A | op_2150 (id {ids['tarde']}) | [UNI] Guiche 02 (id {ids['L2']})" in saida, saida
    assert "op_hoje" not in saida and "op_desde_nulo" not in saida and "op_inativo" not in saida
    assert "nada foi alterado" in saida
    assert turno_ativo("ontem") and usuario("ontem").local_atual_id == ids["L1"]
    ok("--dry-run: 3 turnos e 2 locais de dias anteriores listados (dia da clínica, empresa, usuário, local); nada alterado")

# ===========================================================================
# B) 1º request de HOJE às 07:00 - a varredura do 10.77 agora também fecha
#    turnos e libera locais de dias anteriores.
# ===========================================================================
app.config["ENCERRAMENTO_SESSOES_ATIVO"] = True
app.config["HORARIO_ENCERRAMENTO_SESSOES"] = "23:00"
app.extensions.pop(encerramento._CHAVE_DIA_VARRIDO, None)

with relogio(*HOJE_7H):
    assert app.test_client().get("/login").status_code == 200  # request anônimo já dispara

for chave in ("ontem", "tarde", "sem_local"):
    assert turno_ativo(chave) is False, chave
for chave in ("ontem", "tarde"):
    u = usuario(chave)
    assert u.local_atual_id is None and u.local_desde is None, chave
ok("Turnos ativos de ontem e de 01/09 desativados; locais de ontem liberados (local_atual_id/local_desde NULL)")

assert turno_ativo("tarde") is False and usuario("tarde").local_atual_id is None
ok("Turno/local das 21:50 de ontem (já 'hoje' em UTC) também fechados - dia civil da clínica, não de UTC")

assert turno_ativo("hoje") is True
u = usuario("hoje")
assert u.local_atual_id == ids["L3"] and u.local_desde == em_brasilia(10, 15, 6, 30)
assert turno_ativo("inativo") is False
ok("Turno e local de HOJE intactos; turno de ontem já encerrado continua como estava")

assert usuario("desde_nulo").local_atual_id == ids["L4"]
ok("local_atual_id com local_desde NULL não é liberado (não existe em produção - documentado, não tratado)")

for chave, versao in versoes.items():
    assert usuario(chave).sessao_versao == versao, chave
with relogio(*HOJE_7H):
    assert autenticado(c_hoje) and autenticado(c_ontem)
ok("sessao_versao intocada (opção A): ninguém é deslogado - nem quem entrou hoje nem a sessão que sobrou de ontem")

with relogio(*HOJE_7H):
    resp = c_novo.post("/atendimento/local", data={"local_id": ids["L1"]}, follow_redirects=True)
    assert resp.status_code == 200 and "já está em uso".encode("utf-8") not in resp.data
assert usuario("novo").local_atual_id == ids["L1"]
ok("O Local liberado pode ser escolhido por outro operador hoje")

# ===========================================================================
# C) Idempotente e condicional: o WHERE repete a condição, então quem iniciou
#    turno/escolheu local hoje nunca é desfeito.
# ===========================================================================
with relogio(15, 9):
    app.test_client().get("/login")  # memo do dia: não varre de novo
with relogio(15, 9), app.app_context():
    assert encerramento.liberar_turnos_e_locais_de_dias_anteriores() == (0, 0)
    assert encerramento.encerrar_pendencias_de_dias_anteriores() == (0, 0, 0)
assert turno_ativo("hoje") is True and usuario("hoje").local_atual_id == ids["L3"]
ok("Idempotente: 2ª execução no mesmo dia não acha nada e não toca o turno/local de hoje")

# ===========================================================================
# D) Uma transação só (senhas + turnos + locais): falha no meio desfaz tudo e
#    o request segue (mesma proteção do 10.77).
# ===========================================================================
with app.app_context():
    senha = Senha(
        empresa_id=ids["empresa"], unidade_id=ids["unidade"], fila_id=ids["fila"], codigo="FA001",
        sequencial=1, prioridade=False, status="aguardando", criada_em=em_brasilia(10, 15, 10),
    )
    db.session.add(senha)
    db.session.commit()
    id_senha = senha.id

app.extensions.pop(encerramento._CHAVE_DIA_VARRIDO, None)
with patch.object(
    encerramento, "liberar_turnos_e_locais_de_dias_anteriores", side_effect=RuntimeError("banco fora")
):
    with relogio(16, 7):
        assert app.test_client().get("/login").status_code == 200
with app.app_context():
    assert db.session.get(Senha, id_senha).status == "aguardando"
assert turno_ativo("hoje") is True and usuario("hoje").local_atual_id == ids["L3"]
assert encerramento._CHAVE_DIA_VARRIDO not in app.extensions
ok("Falha ao liberar turnos/locais desfaz também o cancelamento das senhas (rollback único); request responde 200")

with relogio(16, 7, 6):  # passou o intervalo de retentativa
    app.test_client().get("/login")
with app.app_context():
    assert db.session.get(Senha, id_senha).status == "cancelada"
assert turno_ativo("hoje") is False and usuario("hoje").local_atual_id is None
ok("Retentativa depois de 5 min: senha, turno e local de 15/10 fechados juntos no dia 16/10")

# ===========================================================================
# E) Comando real (gate desligado - o comando não depende dele).
# ===========================================================================
app.config["ENCERRAMENTO_SESSOES_ATIVO"] = False
with app.app_context():
    u = db.session.get(Usuario, ids["novo"])
    u.local_atual_id, u.local_desde = ids["L6"], em_brasilia(10, 16, 8)
    db.session.add(TurnoAtivo(
        usuario_id=u.id, local_id=ids["L6"], iniciado_em=em_brasilia(10, 16, 8),
        filas_config=[{"fila_id": ids["fila"], "ordem": 0, "chamadas_seguidas": 1}],
    ))
    db.session.commit()

runner = app.test_cli_runner()
with relogio(17, 7):
    resultado = runner.invoke(args=["encerrar-pendentes", "--antes-de-hoje"])
    assert resultado.exit_code == 0, resultado.output
    assert "Turnos desativados: 1. Locais liberados: 1." in resultado.output, resultado.output
    assert turno_ativo("novo") is False and usuario("novo").local_atual_id is None
    ok("Execução real do comando: 1 turno desativado e 1 local liberado (o de 16/10)")

    resultado = runner.invoke(args=["encerrar-pendentes", "--antes-de-hoje"])
    assert "Turnos desativados: 0. Locais liberados: 0." in resultado.output, resultado.output
    ok("Comando rodado de novo: 0 e 0")

# ===========================================================================
# F) "Desconectar" (tela de ocupação) agora também desativa o TurnoAtivo -
#    bug confirmado em produção (usuário 4 / Local 2 e usuário 97 / Local 93).
# ===========================================================================
with app.app_context():
    turno = TurnoAtivo.query.filter_by(usuario_id=ids["hoje"]).one()
    turno.ativo, turno.local_id, turno.iniciado_em = True, ids["L3"], em_brasilia(10, 17, 8)
    u = db.session.get(Usuario, ids["hoje"])
    u.local_atual_id, u.local_desde = ids["L3"], em_brasilia(10, 17, 8)
    db.session.commit()

with relogio(17, 9):
    c_admin = login_como("admin_turnos")
    versao = usuario("hoje").sessao_versao
    resp = c_admin.post(f"/admin/locais/ocupacao/{ids['hoje']}/desconectar", follow_redirects=True)
    assert "foi desconectado de".encode("utf-8") in resp.data
u = usuario("hoje")
assert turno_ativo("hoje") is False
assert u.local_atual_id is None and u.sessao_versao == versao + 1
ok("Desconectar: libera o Local, derruba a sessão E desativa o TurnoAtivo")

with app.app_context():
    turno = TurnoAtivo.query.filter_by(usuario_id=ids["sem_local"]).one()
    turno.ativo, turno.iniciado_em = True, em_brasilia(10, 17, 8)
    db.session.commit()
with relogio(17, 9):
    resp = c_admin.post(f"/admin/locais/ocupacao/{ids['sem_local']}/desconectar", follow_redirects=True)
    assert "já não estava ocupando nenhum local".encode("utf-8") in resp.data
assert turno_ativo("sem_local") is False
ok("Desconectar quem não ocupa Local também desativa o turno ativo dele")

print(f"\n{len(passos)} verificações OK.")
