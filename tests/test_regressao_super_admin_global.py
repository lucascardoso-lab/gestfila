"""
Teste de regressão: criação de super admins globais (sem empresa) pelo próprio
painel web, a partir de um super admin já existente.

Cobre:
- GET /admin/usuarios (sem empresa_id, como super admin) lista os super admins
  globais existentes, com badge "Super admin global".
- POST /admin/usuarios/globais/novo cria um usuário com empresa_id=None
  (Usuario.is_super_admin True) sem exigir empresa.
- Senha (adendo 10.60): quem cria não escolhe a senha - o sistema gera uma
  temporária aleatória, marca deve_trocar_senha=True (mesmo padrão do seed,
  adendo 10.35) e a exibe uma única vez na resposta do POST. O form de
  criação não tem campo de senha.
- O super admin recém-criado consegue logar (com a senha temporária), é
  obrigado a trocá-la, e depois tem acesso completo (mesmo nível do super
  admin original) - ex.: acessa /admin/empresas e a listagem de usuários de
  qualquer empresa, sem precisar de perfil/unidade.
- RBAC: admin de empresa recebe 403 ao tentar acessar as rotas de criação/edição
  de super admin global, mesmo sabendo a URL.
- Editar/ativar-desativar um super admin global funciona e redireciona sem
  quebrar (empresa_id=None não pode virar querystring inválida).

Execução: python -m tests.test_regressao_super_admin_global (a partir da raiz
do projeto, com o virtualenv ativado).
"""
import os
import re

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


def login_como(login, senha="senha123"):
    c = app.test_client()
    resp = c.post("/login", data={"login": login, "senha": senha}, follow_redirects=True)
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
# C) Super admin cria outro super admin global pelo painel. Formulário não tem
#    campo de senha; o servidor gera uma temporária (ignorando qualquer
#    "senha" que o cliente tente enviar) e exige troca no 1º login.
# ===========================================================================
resp_form = c_super.get("/admin/usuarios/globais/novo")
assert resp_form.status_code == 200
assert b'name="senha"' not in resp_form.data
ok("Formulário de criação de super admin global não tem campo de senha")

resp = c_super.post(
    "/admin/usuarios/globais/novo",
    data={"nome": "Segundo Super Admin", "login": "super_novo", "senha": "senha_que_deveria_ser_ignorada"},
)
assert resp.status_code == 200

with app.app_context():
    criado = Usuario.query.filter_by(login="super_novo").first()
    assert criado is not None
    assert criado.empresa_id is None
    assert criado.is_super_admin is True
    assert criado.perfis == []
    assert criado.deve_trocar_senha is True
    assert not criado.checar_senha("senha_que_deveria_ser_ignorada")
    novo_super_id = criado.id
ok("POST /admin/usuarios/globais/novo cria usuário com empresa_id=None, deve_trocar_senha=True, ignorando senha enviada pelo cliente")

texto = resp.data.decode("utf-8")
match = re.search(r'id="senha-temporaria"[^>]*>([^<]+)<', texto)
assert match, "senha temporária não encontrada na resposta do POST"
senha_temporaria = match.group(1)
with app.app_context():
    assert db.session.get(Usuario, novo_super_id).checar_senha(senha_temporaria)
ok("Senha temporária é exibida uma única vez, direto na resposta do POST, e bate com o hash gravado")

resp = c_super.get("/admin/usuarios")
assert resp.status_code == 200
assert "super_novo" in resp.data.decode("utf-8")
ok("Após criar, a listagem em /admin/usuarios volta a mostrar o novo super admin global")

c_novo_super_primeiro_login = login_como("super_novo", senha=senha_temporaria)
resp = c_novo_super_primeiro_login.get("/admin/empresas", follow_redirects=False)
assert resp.status_code == 302
assert "/trocar-senha-obrigatoria" in resp.headers["Location"]
ok("Novo super admin logando com a senha temporária cai no fluxo de troca obrigatória, como o do seed")

# ===========================================================================
# D) Após trocar a senha temporária, o novo super admin tem acesso completo,
#    igual ao original (reaproveita a sessão já logada da etapa C).
# ===========================================================================
c_novo_super = c_novo_super_primeiro_login
resp = c_novo_super.post(
    "/trocar-senha-obrigatoria",
    data={"nova_senha": "SenhaDefinitivaValida123!", "confirmar_senha": "SenhaDefinitivaValida123!"},
    follow_redirects=True,
)
assert resp.status_code == 200
with app.app_context():
    assert db.session.get(Usuario, novo_super_id).deve_trocar_senha is False
ok("Novo super admin troca a senha temporária pela definitiva, flag deve_trocar_senha vira False")

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
