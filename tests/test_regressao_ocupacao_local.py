"""
Teste de regressão: impedir que dois usuários distintos ocupem o mesmo Local ao
mesmo tempo, e tela administrativa de ocupação de locais (visualizar quem está em
qual local + desconectar com um botão).

Cobre:
- escolher_local() bloqueia quando o local já está em uso por OUTRO usuário
  (local_atual_id/local_desde do usuário bloqueado não mudam).
- O mesmo usuário pode reescolher/confirmar o local que ele mesmo já ocupa (não se
  autobloqueia).
- Depois que o ocupante libera (logout), outro usuário consegue escolher o mesmo local.
- GET /admin/locais/ocupacao lista corretamente quem ocupa o quê, agregando TODAS as
  unidades da empresa (não só uma) - usuarios_ativos_da_empresa().
- POST .../desconectar libera o local (local_atual_id/local_desde -> None) sem afetar
  a sessão de login do usuário (ele continua autenticado, só perde o local).
- Isolamento multi-tenant: admin de outra empresa recebe 404 ao tentar desconectar.
- Desconectar alguém que já não ocupa nada não quebra, só avisa.

Execução: python -m tests.test_regressao_ocupacao_local (a partir da raiz do
projeto, com o virtualenv ativado).
"""
import os

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

from app import create_app
from app.extensions import db
from app.models import Empresa, Local, Perfil, Unidade, Usuario

app = create_app("testing")

passos = []


def ok(msg):
    passos.append(msg)
    print("OK:", msg)


with app.app_context():
    db.create_all()

    perfil_recepcao = Perfil(nome="atendente")
    perfil_admin = Perfil(nome="admin")
    db.session.add_all([perfil_recepcao, perfil_admin])
    db.session.commit()

    empresa_a = Empresa(nome="Clinica A")
    empresa_b = Empresa(nome="Clinica B")
    db.session.add_all([empresa_a, empresa_b])
    db.session.commit()

    unidade_a1 = Unidade(empresa_id=empresa_a.id, nome="Unidade A1")
    unidade_a2 = Unidade(empresa_id=empresa_a.id, nome="Unidade A2")
    unidade_b1 = Unidade(empresa_id=empresa_b.id, nome="Unidade B1")
    db.session.add_all([unidade_a1, unidade_a2, unidade_b1])
    db.session.commit()

    local1 = Local(
        empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="Guiche 01",
        nome_exibicao="Guiche 01", identificador_admin="[UNI] Guiche 01",
    )
    local_a2 = Local(
        empresa_id=empresa_a.id, unidade_id=unidade_a2.id, nome="Guiche A2-01",
        nome_exibicao="Guiche A2-01", identificador_admin="[UNI] Guiche A2-01",
    )
    db.session.add_all([local1, local_a2])
    db.session.commit()

    def novo_usuario(login, empresa_id, perfis, unidades):
        u = Usuario(empresa_id=empresa_id, nome=login, login=login)
        u.set_senha("senha123")
        u.perfis = perfis
        u.unidades = unidades
        db.session.add(u)
        db.session.commit()
        return u.id

    id_op1 = novo_usuario("operador1_ocp", empresa_a.id, [perfil_recepcao], [unidade_a1])
    id_op2 = novo_usuario("operador2_ocp", empresa_a.id, [perfil_recepcao], [unidade_a1])
    id_op_a2 = novo_usuario("operador_a2_ocp", empresa_a.id, [perfil_recepcao], [unidade_a2])
    id_admin_a = novo_usuario("admin_a_ocp", empresa_a.id, [perfil_admin], [])
    id_admin_b = novo_usuario("admin_b_ocp", empresa_b.id, [perfil_admin], [])

    ids = dict(
        empresa_a=empresa_a.id, unidade_a1=unidade_a1.id, unidade_a2=unidade_a2.id,
        local1=local1.id, local_a2=local_a2.id,
        op1=id_op1, op2=id_op2, op_a2=id_op_a2, admin_a=id_admin_a, admin_b=id_admin_b,
    )


def login_como(login):
    c = app.test_client()
    resp = c.post("/login", data={"login": login, "senha": "senha123"}, follow_redirects=True)
    assert resp.status_code == 200, (login, resp.status_code)
    return c


def db_get_usuario(id_):
    with app.app_context():
        return db.session.get(Usuario, id_)


c_op1 = login_como("operador1_ocp")
c_op2 = login_como("operador2_ocp")
c_op_a2 = login_como("operador_a2_ocp")
c_admin_a = login_como("admin_a_ocp")
c_admin_b = login_como("admin_b_ocp")

# ===========================================================================
# A) operador1 escolhe local1 - funciona normalmente (ninguém ocupando ainda).
# ===========================================================================
resp = c_op1.post("/atendimento/local", data={"local_id": ids["local1"]}, follow_redirects=True)
assert resp.status_code == 200
u1 = db_get_usuario(ids["op1"])
assert u1.local_atual_id == ids["local1"]
ok("Primeiro usuário escolhe um local livre normalmente")

# ===========================================================================
# B) operador2 tenta escolher o MESMO local1 - bloqueado (não é o mesmo usuário).
# ===========================================================================
resp = c_op2.post("/atendimento/local", data={"local_id": ids["local1"]}, follow_redirects=True)
assert resp.status_code == 200
assert "já está em uso por operador1_ocp".encode("utf-8") in resp.data
u2 = db_get_usuario(ids["op2"])
assert u2.local_atual_id is None
ok("Segundo usuário é bloqueado ao tentar escolher um local já ocupado por outro")

# ===========================================================================
# C) operador1 pode reescolher o local que ELE MESMO já ocupa (não se autobloqueia).
# ===========================================================================
resp = c_op1.post("/atendimento/local", data={"local_id": ids["local1"]}, follow_redirects=True)
assert resp.status_code == 200
assert "já está em uso".encode("utf-8") not in resp.data
u1 = db_get_usuario(ids["op1"])
assert u1.local_atual_id == ids["local1"]
ok("O próprio ocupante pode reconfirmar/reescolher o mesmo local sem ser bloqueado")

# ===========================================================================
# D) Depois que operador1 libera (logout), operador2 consegue escolher local1.
# ===========================================================================
c_op1.get("/logout", follow_redirects=True)
u1 = db_get_usuario(ids["op1"])
assert u1.local_atual_id is None

resp = c_op2.post("/atendimento/local", data={"local_id": ids["local1"]}, follow_redirects=True)
assert resp.status_code == 200
u2 = db_get_usuario(ids["op2"])
assert u2.local_atual_id == ids["local1"]
ok("Depois que o ocupante libera o local (logout), outro usuário consegue escolhê-lo")

# operador_a2 ocupa um local de outra unidade, pra testar agregação multi-unidade.
resp = c_op_a2.post("/atendimento/local", data={"local_id": ids["local_a2"]}, follow_redirects=True)
assert resp.status_code == 200

# ===========================================================================
# E) Tela admin /admin/locais/ocupacao agrega TODAS as unidades da empresa.
# ===========================================================================
resp = c_admin_a.get(f"/admin/locais/ocupacao?empresa_id={ids['empresa_a']}")
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
assert "operador2_ocp" in texto and "Guiche 01" in texto
assert "operador_a2_ocp" in texto and "Guiche A2-01" in texto
ok("Tela de ocupação lista usuários de TODAS as unidades da empresa, não só uma")

# ===========================================================================
# F) Admin desconecta operador2 - libera o local E derruba a sessão de login dele
#    (Usuario.sessao_versao) - o cliente antigo (c_op2) deixa de conseguir acessar
#    qualquer rota @login_required, mesmo com o cookie de sessão ainda "presente".
# ===========================================================================
versao_antes = db_get_usuario(ids["op2"]).sessao_versao

resp = c_admin_a.post(f"/admin/locais/ocupacao/{ids['op2']}/desconectar", follow_redirects=True)
assert resp.status_code == 200
assert "foi desconectado de".encode("utf-8") in resp.data and "Guiche 01".encode("utf-8") in resp.data
assert "encerrada".encode("utf-8") in resp.data

u2 = db_get_usuario(ids["op2"])
assert u2.local_atual_id is None and u2.local_desde is None
assert u2.sessao_versao == versao_antes + 1
ok("Admin desconecta o usuário: local_atual_id/local_desde limpos e sessao_versao incrementada")

# cliente antigo de operador2 (cookie de sessão emitido ANTES do desconectar) não
# consegue mais acessar uma rota @login_required - é redirecionado pro login.
resp = c_op2.get("/atendimento/local", follow_redirects=False)
assert resp.status_code == 302
assert resp.headers["Location"].startswith("/login")
ok("Sessão do usuário desconectado é invalidada de fato - cliente antigo cai no login")

# ele precisa logar de novo pra voltar a usar o sistema.
c_op2_novo = login_como("operador2_ocp")
resp = c_op2_novo.post("/atendimento/local", data={"local_id": ids["local1"]}, follow_redirects=True)
assert resp.status_code == 200
u2 = db_get_usuario(ids["op2"])
assert u2.local_atual_id == ids["local1"]
ok("Depois de reautenticar, o usuário volta a operar normalmente")

# ===========================================================================
# G) Desconectar a si mesmo é bloqueado (mesmo padrão de 'não desativar a si
#    mesmo' já usado em admin/usuarios.py).
# ===========================================================================
resp = c_admin_a.post(f"/admin/locais/ocupacao/{ids['admin_a']}/desconectar", follow_redirects=True)
assert resp.status_code == 200
assert "não pode desconectar a si mesmo".encode("utf-8") in resp.data
ok("Admin não pode desconectar a si mesmo")

# ===========================================================================
# H) Desconectar quem já não ocupa nenhum local ainda encerra a sessão (não é
#    só um "nada a fazer") - mesmo padrão do item F, mas sem local pra liberar.
# ===========================================================================
resp = c_admin_a.post(f"/admin/locais/ocupacao/{ids['op_a2']}/desconectar", follow_redirects=True)
# operador_a2 ainda ocupa local_a2 neste ponto - primeiro desconecta de fato...
assert resp.status_code == 200
assert "foi desconectado de".encode("utf-8") in resp.data and "Guiche A2-01".encode("utf-8") in resp.data

# ...precisa logar de novo pra tentar a segunda vez (a primeira já derrubou a sessão).
c_op_a2_novo = login_como("operador_a2_ocp")
versao_antes = db_get_usuario(ids["op_a2"]).sessao_versao
resp = c_admin_a.post(f"/admin/locais/ocupacao/{ids['op_a2']}/desconectar", follow_redirects=True)
assert resp.status_code == 200
assert "já não estava ocupando nenhum local".encode("utf-8") in resp.data
assert "encerrada mesmo assim".encode("utf-8") in resp.data
assert db_get_usuario(ids["op_a2"]).sessao_versao == versao_antes + 1
ok("Desconectar quem já não ocupa local nenhum ainda assim encerra a sessão (não é um no-op)")

resp = c_op_a2_novo.get("/atendimento/local", follow_redirects=False)
assert resp.status_code == 302 and resp.headers["Location"].startswith("/login")
ok("Confirma: mesmo sem local pra liberar, a sessão foi mesmo invalidada")

# ===========================================================================
# I) Isolamento multi-tenant: admin da empresa B não desconecta usuário da empresa A.
# ===========================================================================
resp = c_admin_b.post(f"/admin/locais/ocupacao/{ids['op2']}/desconectar")
assert resp.status_code == 404
u2 = db_get_usuario(ids["op2"])
assert u2.local_atual_id == ids["local1"]
ok("Isolamento multi-tenant: admin de outra empresa recebe 404 ao tentar desconectar (nada muda)")

resp = c_admin_b.get("/admin/locais/ocupacao")
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
assert "operador2_ocp" not in texto and "Guiche 01" not in texto
ok("Isolamento multi-tenant: tela de ocupação da empresa B não lista ninguém da empresa A")

print()
print(f"TODOS OS {len(passos)} TESTES DE OCUPACAO DE LOCAL PASSARAM")
