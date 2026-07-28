"""
Teste de regressão da seção 10.3 do BLUEPRINT (Cadastro de Locais):

- CRUD administrativo de `locais`, com o mesmo padrão de isolamento multi-tenant
  e RBAC já validado nas fases anteriores (404 ao acessar local de outra empresa
  via ID manual, listagem automática por empresa, super admin vendo qualquer
  empresa via empresa_id, perfil operacional sem acesso ao /admin);
- fluxo de escolha/troca de Local na tela de Atendimento (seção 10.2/10.3): Local
  deixou de ser texto livre - o operador escolhe entre os cadastrados para as
  unidades que tem permissão de acessar, a escolha fica na sessão do turno, e
  pode ser trocada sem deslogar.

Execução: python -m tests.test_regressao_fase10_3 (a partir da raiz do projeto,
com o virtualenv ativado).
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

    perfil_admin = Perfil(nome="admin")
    perfil_recepcao = Perfil(nome="atendente")
    db.session.add_all([perfil_admin, perfil_recepcao])
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

    def novo_usuario(login, empresa_id, perfis, unidades):
        u = Usuario(empresa_id=empresa_id, nome=login, login=login)
        u.set_senha("senha123")
        u.perfis = perfis
        u.unidades = unidades
        db.session.add(u)
        db.session.commit()
        return u.id

    id_admin_a = novo_usuario("admin_a3", empresa_a.id, [perfil_admin], [])
    id_admin_b = novo_usuario("admin_b3", empresa_b.id, [perfil_admin], [])
    id_recepcao_a1 = novo_usuario("recepcao_a1_3", empresa_a.id, [perfil_recepcao], [unidade_a1])

    # locais pré-existentes: 1 ativo e 1 inativo na unidade A1, 1 na unidade A2
    # (outra unidade da MESMA empresa, usado para testar isolamento por unidade),
    # 1 na empresa B (usado para testar isolamento por empresa).
    local_a1_ativo = Local(empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="rec1-01", tipo="recepcao")
    local_a1_inativo = Local(
        empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="rec1-02 (desativado)", ativo=False
    )
    local_a2 = Local(empresa_id=empresa_a.id, unidade_id=unidade_a2.id, nome="rec2-01")
    local_b = Local(empresa_id=empresa_b.id, unidade_id=unidade_b1.id, nome="recB-01")
    db.session.add_all([local_a1_ativo, local_a1_inativo, local_a2, local_b])
    db.session.commit()

    ids = dict(
        empresa_a=empresa_a.id, empresa_b=empresa_b.id,
        unidade_a1=unidade_a1.id, unidade_a2=unidade_a2.id, unidade_b1=unidade_b1.id,
        admin_a=id_admin_a, admin_b=id_admin_b, recepcao_a1=id_recepcao_a1,
        local_a1_ativo=local_a1_ativo.id, local_a1_inativo=local_a1_inativo.id,
        local_a2=local_a2.id, local_b=local_b.id,
    )

print("IDS", ids)


def login_como(login):
    c = app.test_client()
    resp = c.post("/login", data={"login": login, "senha": "senha123"}, follow_redirects=True)
    assert resp.status_code == 200, (login, resp.status_code)
    return c


def db_get(modelo, id_):
    with app.app_context():
        return db.session.get(modelo, id_)


c_admin_a = login_como("admin_a3")
c_admin_b = login_como("admin_b3")
c_recepcao_a1 = login_como("recepcao_a1_3")

# ===========================================================================
# A) CRUD administrativo de Locais (seção 10.3)
# ===========================================================================

# RBAC: perfil operacional (recepção) não acessa o admin.
resp = c_recepcao_a1.get("/admin/locais")
assert resp.status_code == 403
ok("RBAC: perfil 'atendente' recebe 403 em /admin/locais")

# Listagem: admin da empresa A vê os 3 locais da própria empresa (e só eles).
resp = c_admin_a.get("/admin/locais")
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
assert "rec1-01" in texto and "rec1-02" in texto and "rec2-01" in texto
assert "recB-01" not in texto
ok("Listagem: admin da empresa A vê automaticamente todos os locais da própria empresa, e nenhum da B")

# Criação: nome obrigatório.
resp = c_admin_a.post(
    f"/admin/locais/novo?empresa_id={ids['empresa_a']}",
    data={"nome": "", "unidade_id": ids["unidade_a1"], "tipo": "outro"},
)
assert "Informe o nome do local".encode("utf-8") in resp.data
ok("Criação: nome vazio é rejeitado")

# Criação: unidade de outra empresa é rejeitada (não está entre as opções válidas).
resp = c_admin_a.post(
    f"/admin/locais/novo?empresa_id={ids['empresa_a']}",
    data={"nome": "Tentativa cross-tenant", "unidade_id": ids["unidade_b1"], "tipo": "outro"},
)
assert "Selecione uma unidade válida".encode("utf-8") in resp.data
with app.app_context():
    assert Local.query.filter_by(nome="Tentativa cross-tenant").first() is None
ok("Criação: unidade de outra empresa é rejeitada, nenhum local é criado")

# Criação: caminho feliz.
resp = c_admin_a.post(
    f"/admin/locais/novo?empresa_id={ids['empresa_a']}",
    data={"nome": "rec1-03", "unidade_id": ids["unidade_a1"], "tipo": "recepcao"},
    follow_redirects=True,
)
assert resp.status_code == 200
with app.app_context():
    novo = Local.query.filter_by(nome="rec1-03", empresa_id=ids["empresa_a"]).first()
    assert novo is not None and novo.unidade_id == ids["unidade_a1"] and novo.tipo == "recepcao" and novo.ativo
    id_novo_local = novo.id
ok("Criação: local novo é gravado com empresa/unidade/tipo corretos e ativo=True por padrão")

# Isolamento: admin da empresa B não edita/visualiza local da empresa A via ID manual.
resp = c_admin_b.get(f"/admin/locais/{ids['local_a1_ativo']}/editar")
assert resp.status_code == 404
ok("Isolamento: admin de outra empresa recebe 404 ao tentar EDITAR local da empresa A via ID manual")

resp = c_admin_b.post(
    f"/admin/locais/{ids['local_a1_ativo']}/editar",
    data={"nome": "hackeado", "unidade_id": ids["unidade_a1"], "tipo": ""},
)
assert resp.status_code == 404
with app.app_context():
    assert db.session.get(Local, ids["local_a1_ativo"]).nome == "rec1-01"
ok("Isolamento: admin de outra empresa recebe 404 ao tentar SALVAR edição em local da empresa A, nada muda")

resp = c_admin_b.post(f"/admin/locais/{ids['local_a1_ativo']}/toggle-ativo")
assert resp.status_code == 404
with app.app_context():
    assert db.session.get(Local, ids["local_a1_ativo"]).ativo is True
ok("Isolamento: admin de outra empresa recebe 404 ao tentar ativar/desativar local da empresa A")

# Edição: caminho feliz (admin da própria empresa).
resp = c_admin_a.post(
    f"/admin/locais/{id_novo_local}/editar",
    data={"nome": "rec1-03-renomeado", "unidade_id": ids["unidade_a2"], "tipo": ""},
    follow_redirects=True,
)
assert resp.status_code == 200
with app.app_context():
    editado = db.session.get(Local, id_novo_local)
    assert editado.nome == "rec1-03-renomeado" and editado.unidade_id == ids["unidade_a2"] and editado.tipo is None
ok("Edição: admin da própria empresa atualiza nome/unidade/tipo do local")

# Toggle ativo/inativo: caminho feliz.
resp = c_admin_a.post(f"/admin/locais/{id_novo_local}/toggle-ativo", follow_redirects=True)
assert resp.status_code == 200
with app.app_context():
    assert db.session.get(Local, id_novo_local).ativo is False
ok("Toggle: admin da própria empresa desativa o local")

resp = c_admin_a.get(f"/admin/locais?empresa_id={ids['empresa_b']}")
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
# admin (não super admin) que força empresa_id de outra empresa continua vendo a própria
assert "recB-01" not in texto
ok("Admin de empresa (não super admin) ignora empresa_id de outra empresa forçado na querystring")

print()

# ===========================================================================
# B) Fluxo de escolha/troca de Local na tela de Atendimento (seção 10.2/10.3)
# ===========================================================================

# Sem login: redireciona para o login (login_required).
resp = app.test_client().get("/atendimento/local")
assert resp.status_code == 302
assert "/login" in resp.headers["Location"]
ok("Sem login, GET /atendimento/local redireciona para o login")

# Listagem: recepcao_a1 (restrito à unidade A1) só vê os locais ATIVOS da unidade A1 -
# não vê o inativo da própria unidade, não vê o da unidade A2 (mesma empresa, outra
# unidade que ele não tem permissão), e não vê o da empresa B.
resp = c_recepcao_a1.get("/atendimento/local")
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
assert "rec1-01" in texto
assert "rec1-02" not in texto  # inativo
assert "rec2-01" not in texto  # outra unidade, sem permissão
assert "recB-01" not in texto  # outra empresa
ok("Escolher local: operador só vê locais ATIVOS das unidades que tem permissão de acessar")

# Escolha: caminho feliz, com 'next' relativo respeitado.
resp = c_recepcao_a1.post(
    "/atendimento/local",
    data={"local_id": ids["local_a1_ativo"], "next": "/atendimento/filas"},
    follow_redirects=False,
)
assert resp.status_code == 302
assert resp.headers["Location"] == "/atendimento/filas"
with c_recepcao_a1.session_transaction() as sess:
    assert sess["atendimento_local_id"] == ids["local_a1_ativo"]
ok("Escolher local: POST válido salva o local_id na sessão e redireciona para o 'next' informado")

# Local de outra unidade (sem permissão) não pode ser escolhido, mesmo forçando o ID.
resp = c_recepcao_a1.post("/atendimento/local", data={"local_id": ids["local_a2"]})
assert "Selecione um local válido".encode("utf-8") in resp.data
with c_recepcao_a1.session_transaction() as sess:
    assert sess["atendimento_local_id"] == ids["local_a1_ativo"]  # não mudou
ok("Escolher local: local de unidade sem permissão é rejeitado, sessão permanece com a escolha anterior")

# Local inativo não pode ser escolhido.
resp = c_recepcao_a1.post("/atendimento/local", data={"local_id": ids["local_a1_inativo"]})
assert "Selecione um local válido".encode("utf-8") in resp.data
ok("Escolher local: local desativado é rejeitado")

# Local de outra empresa não pode ser escolhido.
resp = c_recepcao_a1.post("/atendimento/local", data={"local_id": ids["local_b"]})
assert "Selecione um local válido".encode("utf-8") in resp.data
ok("Escolher local: local de outra empresa é rejeitado")

# Trocar local: novo POST sobrescreve a escolha anterior (sem precisar deslogar).
with app.app_context():
    local_extra = Local(empresa_id=ids["empresa_a"], unidade_id=ids["unidade_a1"], nome="rec1-04")
    db.session.add(local_extra)
    db.session.commit()
    id_local_extra = local_extra.id

resp = c_recepcao_a1.post("/atendimento/local", data={"local_id": id_local_extra}, follow_redirects=True)
assert resp.status_code == 200
with c_recepcao_a1.session_transaction() as sess:
    assert sess["atendimento_local_id"] == id_local_extra
ok("Trocar local: novo POST sobrescreve o local da sessão, sem precisar deslogar")

# next absoluto/externo (tentativa de open redirect) é ignorado, cai no padrão seguro.
resp = c_recepcao_a1.post(
    "/atendimento/local",
    data={"local_id": ids["local_a1_ativo"], "next": "https://evil.example.com/phish"},
    follow_redirects=False,
)
assert resp.status_code == 302
assert resp.headers["Location"] == "/atendimento/filas"
ok("Escolher local: 'next' absoluto/externo é ignorado (sem open redirect), cai no destino padrão")

print()
print(f"TODOS OS {len(passos)} TESTES DA SEÇÃO 10.3 (CADASTRO DE LOCAIS) PASSARAM")
