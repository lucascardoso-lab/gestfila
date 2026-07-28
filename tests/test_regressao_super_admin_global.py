"""
Teste de regressão: criação de super admins globais (sem empresa) pelo próprio
painel web, a partir de um super admin já existente.

Cobre:
- GET /admin/usuarios (sem empresa_id, como super admin) lista os super admins
  globais existentes, com badge "Super admin global".
- POST /admin/usuarios/globais/novo cria um usuário com empresa_id=None
  (Usuario.is_super_admin True) sem exigir empresa.
- O super admin recém-criado consegue logar e tem acesso completo (mesmo nível
  do super admin original) - ex.: acessa /admin/empresas e a listagem de
  usuários de qualquer empresa, sem precisar de perfil/unidade.
- RBAC: admin de empresa recebe 403 ao tentar acessar as rotas de criação/edição
  de super admin global, mesmo sabendo a URL.
- Editar/ativar-desativar um super admin global funciona e redireciona sem
  quebrar (empresa_id=None não pode virar querystring inválida).

Execução: python -m tests.test_regressao_super_admin_global (a partir da raiz
do projeto, com o virtualenv ativado).
"""
import os

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

from app import create_app
from app.extensions import db
from app.models import Empresa, Perfil, Usuario

app = create_app("testing")

passos = []


def ok(msg):
    passos.append(msg)
    print("OK:", msg)


with app.app_context():
    db.create_all()

    perfil_admin = Perfil(nome="admin")
    db.session.add(perfil_admin)
    db.session.commit()

    empresa_a = Empresa(nome="Clinica A")
    db.session.add(empresa_a)
    db.session.commit()

    def novo_usuario(login, empresa_id, perfis=None):
        u = Usuario(empresa_id=empresa_id, nome=login, login=login)
        u.set_senha("senha123")
        u.perfis = perfis or []
        db.session.add(u)
        db.session.commit()
        return u.id

    super_id = novo_usuario("super_original", None)
    admin_a_id = novo_usuario("admin_a_sag", empresa_a.id, [perfil_admin])

    ids = dict(empresa_a=empresa_a.id, super_original=super_id, admin_a=admin_a_id)


def login_como(login):
    c = app.test_client()
    resp = c.post("/login", data={"login": login, "senha": "senha123"}, follow_redirects=True)
    assert resp.status_code == 200, (login, resp.status_code)
    return c


c_super = login_como("super_original")
c_admin_a = login_como("admin_a_sag")

# ===========================================================================
# A) Tela /admin/usuarios (super admin, sem empresa_id) lista o super admin
#    original com badge "Super admin global" e o botão de criar um novo.
# ===========================================================================
resp = c_super.get("/admin/usuarios")
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
assert "super_original" in texto
assert "Super admin global" in texto
assert "/admin/usuarios/globais/novo" in texto
ok("GET /admin/usuarios (super admin, sem empresa) lista super admins globais com badge própria")

# ===========================================================================
# B) RBAC: admin de empresa não pode criar nem ver a tela de super admin global.
# ===========================================================================
resp = c_admin_a.get("/admin/usuarios/globais/novo")
assert resp.status_code == 403
ok("GET /admin/usuarios/globais/novo como admin de empresa -> 403")

resp = c_admin_a.post(
    "/admin/usuarios/globais/novo",
    data={"nome": "Invasor", "login": "invasor_sag", "senha": "senha123"},
)
assert resp.status_code == 403
with app.app_context():
    assert Usuario.query.filter_by(login="invasor_sag").first() is None
ok("POST /admin/usuarios/globais/novo como admin de empresa -> 403, nenhum usuário criado")

# ===========================================================================
# C) Super admin cria outro super admin global pelo painel.
# ===========================================================================
resp = c_super.post(
    "/admin/usuarios/globais/novo",
    data={"nome": "Segundo Super Admin", "login": "super_novo", "senha": "senha123"},
    follow_redirects=True,
)
assert resp.status_code == 200

with app.app_context():
    criado = Usuario.query.filter_by(login="super_novo").first()
    assert criado is not None
    assert criado.empresa_id is None
    assert criado.is_super_admin is True
    assert criado.perfis == []
    novo_super_id = criado.id
ok("POST /admin/usuarios/globais/novo cria usuário com empresa_id=None (is_super_admin=True)")

texto = resp.data.decode("utf-8")
assert "super_novo" in texto
ok("Após criar, a listagem em /admin/usuarios volta a mostrar o novo super admin global")

# ===========================================================================
# D) O novo super admin consegue logar e tem acesso completo, igual ao original.
# ===========================================================================
c_novo_super = login_como("super_novo")

resp = c_novo_super.get("/admin/empresas")
assert resp.status_code == 200
assert "Clinica A" in resp.data.decode("utf-8")
ok("Novo super admin loga e acessa /admin/empresas (rota só de super admin)")

resp = c_novo_super.get(f"/admin/usuarios?empresa_id={ids['empresa_a']}")
assert resp.status_code == 200
assert "admin_a_sag" in resp.data.decode("utf-8")
ok("Novo super admin acessa a listagem de usuários de uma empresa que não é a dele (não tem nenhuma)")

# ===========================================================================
# E) Editar e ativar/desativar o super admin global funciona sem quebrar.
# ===========================================================================
resp = c_super.get(f"/admin/usuarios/{novo_super_id}/editar")
assert resp.status_code == 200
assert "Editar super admin" in resp.data.decode("utf-8")
ok("GET de edição de um super admin global usa o formulário simples, sem crash")

resp = c_super.post(
    f"/admin/usuarios/{novo_super_id}/editar",
    data={"nome": "Segundo Super Admin Renomeado", "login": "super_novo", "senha": ""},
    follow_redirects=True,
)
assert resp.status_code == 200
with app.app_context():
    atualizado = db.session.get(Usuario, novo_super_id)
    assert atualizado.nome == "Segundo Super Admin Renomeado"
    assert atualizado.empresa_id is None
ok("POST de edição atualiza nome mantendo empresa_id=None")

resp = c_super.post(f"/admin/usuarios/{novo_super_id}/toggle-ativo", follow_redirects=True)
assert resp.status_code == 200
with app.app_context():
    alternado = db.session.get(Usuario, novo_super_id)
    assert alternado.ativo is False
ok("Toggle-ativo de um super admin global funciona e redireciona sem quebrar (empresa_id=None)")

# ===========================================================================
# F) RBAC: admin de empresa não acessa/edita um super admin global por URL direta.
# ===========================================================================
resp = c_admin_a.get(f"/admin/usuarios/{novo_super_id}/editar")
assert resp.status_code == 404
ok("Admin de empresa tentando editar super admin global por ID direto -> 404 (não revela existência)")

resp = c_admin_a.post(f"/admin/usuarios/{novo_super_id}/toggle-ativo")
assert resp.status_code == 404
ok("Admin de empresa tentando ativar/desativar super admin global por ID direto -> 404")

print()
print(f"TODOS OS {len(passos)} TESTES DE SUPER ADMIN GLOBAL VIA PAINEL PASSARAM")
