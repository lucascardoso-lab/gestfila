"""
Teste de regressão: Local atual persistido no servidor (Usuario.local_atual_id/
local_desde), diagnóstico item 1 - antes o Local escolhido só vivia na sessão do
navegador (cookie), invisível a qualquer query/tela administrativa.

Cobre: escolher_local() grava os dois campos; trocar de local atualiza os dois;
logout() limpa local_atual_id/local_desde; usuarios_ativos_por_local(unidade_id)
reflete o estado atual e respeita o escopo de unidade passado pelo chamador.

Execução: python -m tests.test_regressao_local_atual_persistido (a partir da raiz
do projeto, com o virtualenv ativado).
"""
import os
from datetime import datetime

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

from app import create_app
from app.extensions import db
from app.models import Empresa, Local, Perfil, Unidade, Usuario
from app.atendimento.helpers import usuarios_ativos_por_local

app = create_app("testing")

passos = []


def ok(msg):
    passos.append(msg)
    print("OK:", msg)


with app.app_context():
    db.create_all()

    perfil_recepcao = Perfil(nome="atendente")
    db.session.add(perfil_recepcao)
    db.session.commit()

    empresa_a = Empresa(nome="Clinica A")
    db.session.add(empresa_a)
    db.session.commit()

    unidade_a1 = Unidade(empresa_id=empresa_a.id, nome="Unidade A1")
    unidade_a2 = Unidade(empresa_id=empresa_a.id, nome="Unidade A2")
    db.session.add_all([unidade_a1, unidade_a2])
    db.session.commit()

    local1 = Local(
        empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="Guiche 01",
        nome_exibicao="Guiche 01", identificador_admin="[UNI] Guiche 01",
    )
    local2 = Local(
        empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="Guiche 02",
        nome_exibicao="Guiche 02", identificador_admin="[UNI] Guiche 02",
    )
    local_a2 = Local(
        empresa_id=empresa_a.id, unidade_id=unidade_a2.id, nome="Guiche A2-01",
        nome_exibicao="Guiche A2-01", identificador_admin="[UNI] Guiche A2-01",
    )
    db.session.add_all([local1, local2, local_a2])
    db.session.commit()

    def novo_usuario(login, unidades):
        u = Usuario(empresa_id=empresa_a.id, nome=login, login=login)
        u.set_senha("senha123")
        u.perfis = [perfil_recepcao]
        u.unidades = unidades
        db.session.add(u)
        db.session.commit()
        return u.id

    id_recepcao_a1 = novo_usuario("recepcao_a1_local", [unidade_a1])
    id_recepcao_a2 = novo_usuario("recepcao_a2_local", [unidade_a2])

    ids = dict(
        empresa_a=empresa_a.id, unidade_a1=unidade_a1.id, unidade_a2=unidade_a2.id,
        local1=local1.id, local2=local2.id, local_a2=local_a2.id,
        recepcao_a1=id_recepcao_a1, recepcao_a2=id_recepcao_a2,
    )

print("IDS", ids)


def login_como(login):
    c = app.test_client()
    resp = c.post("/login", data={"login": login, "senha": "senha123"}, follow_redirects=True)
    assert resp.status_code == 200, (login, resp.status_code)
    return c


def db_get_usuario(id_):
    with app.app_context():
        return db.session.get(Usuario, id_)


# ---------------------------------------------------------------------------
# 1) Antes de escolher qualquer local: campos ficam NULL (estado implícito que já
#    existia antes desta mudança, só que agora explícito no banco).
# ---------------------------------------------------------------------------
u = db_get_usuario(ids["recepcao_a1"])
assert u.local_atual_id is None and u.local_desde is None
ok("Antes de escolher local: local_atual_id e local_desde são NULL")

c_recepcao_a1 = login_como("recepcao_a1_local")

# ---------------------------------------------------------------------------
# 2) Escolher local grava local_atual_id/local_desde no banco (não só na sessão).
# ---------------------------------------------------------------------------
antes = datetime.utcnow()
resp = c_recepcao_a1.post("/atendimento/local", data={"local_id": ids["local1"]}, follow_redirects=True)
assert resp.status_code == 200
depois = datetime.utcnow()

u = db_get_usuario(ids["recepcao_a1"])
assert u.local_atual_id == ids["local1"]
assert u.local_desde is not None
assert antes <= u.local_desde <= depois
ok("escolher_local(): grava local_atual_id e local_desde (timestamp real) no Usuario")

# ---------------------------------------------------------------------------
# 3) usuarios_ativos_por_local reflete a escolha.
# ---------------------------------------------------------------------------
with app.app_context():
    ativos = usuarios_ativos_por_local(ids["unidade_a1"])
    assert len(ativos) == 1
    assert ativos[0]["usuario"].id == ids["recepcao_a1"]
    assert ativos[0]["local"].id == ids["local1"]
    assert ativos[0]["desde"] is not None
ok("usuarios_ativos_por_local(unidade_a1): mostra recepcao_a1 em Guiche 01")

# ---------------------------------------------------------------------------
# 4) Trocar de local atualiza os dois campos (não acumula histórico, é o estado atual).
# ---------------------------------------------------------------------------
desde_local1 = db_get_usuario(ids["recepcao_a1"]).local_desde

resp = c_recepcao_a1.post("/atendimento/local", data={"local_id": ids["local2"]}, follow_redirects=True)
assert resp.status_code == 200

u = db_get_usuario(ids["recepcao_a1"])
assert u.local_atual_id == ids["local2"]
assert u.local_desde is not None and u.local_desde >= desde_local1
ok("Trocar de local: local_atual_id e local_desde são atualizados para a nova escolha")

with app.app_context():
    ativos = usuarios_ativos_por_local(ids["unidade_a1"])
    assert len(ativos) == 1
    assert ativos[0]["local"].id == ids["local2"]
ok("usuarios_ativos_por_local(unidade_a1): reflete a troca (Guiche 02, não mais Guiche 01)")

# ---------------------------------------------------------------------------
# 5) Isolamento por unidade: usuário de outra unidade não aparece na consulta.
# ---------------------------------------------------------------------------
c_recepcao_a2 = login_como("recepcao_a2_local")
resp = c_recepcao_a2.post("/atendimento/local", data={"local_id": ids["local_a2"]}, follow_redirects=True)
assert resp.status_code == 200

with app.app_context():
    ativos_a1 = usuarios_ativos_por_local(ids["unidade_a1"])
    ativos_a2 = usuarios_ativos_por_local(ids["unidade_a2"])
    assert len(ativos_a1) == 1 and ativos_a1[0]["usuario"].id == ids["recepcao_a1"]
    assert len(ativos_a2) == 1 and ativos_a2[0]["usuario"].id == ids["recepcao_a2"]
ok("usuarios_ativos_por_local respeita o unidade_id passado - cada unidade só vê seus próprios operadores")

# ---------------------------------------------------------------------------
# 6) Logout limpa local_atual_id e local_desde.
# ---------------------------------------------------------------------------
resp = c_recepcao_a1.get("/logout", follow_redirects=True)
assert resp.status_code == 200

u = db_get_usuario(ids["recepcao_a1"])
assert u.local_atual_id is None
assert u.local_desde is None
ok("logout(): limpa local_atual_id e local_desde")

with app.app_context():
    ativos_a1 = usuarios_ativos_por_local(ids["unidade_a1"])
    assert ativos_a1 == []
ok("usuarios_ativos_por_local(unidade_a1): vazio após o logout de recepcao_a1")

# recepcao_a2 continua ativo (não foi deslogado) - confirma que o logout de um
# usuário não afeta o estado de outro.
with app.app_context():
    ativos_a2 = usuarios_ativos_por_local(ids["unidade_a2"])
    assert len(ativos_a2) == 1 and ativos_a2[0]["usuario"].id == ids["recepcao_a2"]
ok("Logout de um usuário não afeta o local_atual persistido de outro")

print()
print(f"TODOS OS {len(passos)} TESTES DE LOCAL ATUAL PERSISTIDO PASSARAM")
