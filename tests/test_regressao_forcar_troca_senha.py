"""
Teste de regressão: troca de senha obrigatória (checklist de segurança pra
publicação no GitHub) - Usuario.deve_trocar_senha bloqueia qualquer tela até
o usuário trocar a senha, pra uma senha temporária (ex.: a gerada
aleatoriamente pelo seed do superadmin) nunca virar permanente por
esquecimento operacional.

Cobre:
- seeds/seed_inicial.py gera senha aleatória (não mais "ChangeMe123!" fixo) e
  marca deve_trocar_senha=True no superadmin criado.
- Middleware (app/middleware/forcar_troca_senha.py) redireciona QUALQUER rota
  pra /trocar-senha-obrigatoria enquanto o flag for True - exceto a própria
  tela de troca, logout e arquivos estáticos.
- POST válido troca a senha, limpa o flag, libera o resto do sistema.
- Validação: senha curta ou confirmação divergente é rejeitada, flag continua
  True.
- Usuário comum (deve_trocar_senha=False, o default) nunca é afetado.
- Rotas públicas (totem/painel de TV, sem login) continuam livres mesmo que
  ALGUM usuário do sistema esteja com o flag True (o middleware só age em
  cima de current_user.is_authenticated).

Execução: python -m tests.test_regressao_forcar_troca_senha (a partir da raiz
do projeto, com o virtualenv ativado).
"""
import os
import sys
import tempfile

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

# seeds/seed_inicial.py::seed() chama create_app() por conta própria (processo
# real de deploy, sem app_context injetado de fora) - com sqlite ":memory:"
# (default de TestingConfig) isso criaria um banco EM MEMÓRIA SEPARADO do
# `app` deste teste, e as duas metades do teste nunca veriam os dados uma da
# outra. Um arquivo sqlite compartilhado (via TEST_DATABASE_URL) faz os dois
# create_app("testing") apontarem pro mesmo banco.
_DB_TEMP = os.path.join(tempfile.gettempdir(), "test_forcar_troca_senha.db")
if os.path.exists(_DB_TEMP):
    os.remove(_DB_TEMP)
os.environ["TEST_DATABASE_URL"] = f"sqlite:///{_DB_TEMP}"

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app
from app.extensions import db
from app.models import Empresa, Perfil, Unidade, Usuario
from seeds.seed_inicial import SUPER_ADMIN_LOGIN, seed

app = create_app("testing")

passos = []


def ok(msg):
    passos.append(msg)
    print("OK:", msg)


with app.app_context():
    db.create_all()

    empresa = Empresa(nome="Clinica Troca Senha")
    db.session.add(empresa)
    db.session.commit()

    unidade = Unidade(empresa_id=empresa.id, nome="Unidade 1")
    db.session.add(unidade)
    db.session.commit()

    perfil_admin = Perfil(nome="admin")
    db.session.add(perfil_admin)
    db.session.commit()

    usuario_normal = Usuario(nome="Admin Normal", login="admin_normal_troca", empresa_id=empresa.id)
    usuario_normal.set_senha("senha123")
    usuario_normal.perfis = [perfil_admin]
    db.session.add(usuario_normal)
    db.session.commit()

    ids = dict(empresa=empresa.id, unidade=unidade.id)

# ===========================================================================
# A) seed_inicial.seed() gera senha aleatória (nunca mais "ChangeMe123!") e
#    marca deve_trocar_senha=True.
# ===========================================================================
with app.app_context():
    seed()
    super_admin = Usuario.query.filter_by(login=SUPER_ADMIN_LOGIN).first()
    assert super_admin is not None
    assert super_admin.deve_trocar_senha is True
    assert not super_admin.checar_senha("ChangeMe123!")  # a senha fixa antiga NUNCA mais é aceita
    ok("seed_inicial cria o superadmin com deve_trocar_senha=True e sem a senha fixa antiga")

    seed()  # idempotente - rodar de novo não reseta o flag nem troca a senha de novo
    super_admin_depois = Usuario.query.filter_by(login=SUPER_ADMIN_LOGIN).first()
    assert super_admin_depois.senha_hash == super_admin.senha_hash
    assert super_admin_depois.deve_trocar_senha is True
    ok("Rodar o seed de novo é idempotente - não gera senha nova nem mexe no flag de um superadmin já existente")

# ===========================================================================
# B) Usuário comum (deve_trocar_senha=False, default) loga normalmente, sem
#    ser redirecionado pra tela de troca.
# ===========================================================================
c_normal = app.test_client()
resp = c_normal.post("/login", data={"login": "admin_normal_troca", "senha": "senha123"}, follow_redirects=True)
assert resp.status_code == 200
assert b"Troca de senha obrigat" not in resp.data
resp = c_normal.get("/painel/tv/qualquer-slug-inexistente")  # rota pública - só confirma que não houve redirect de troca
assert resp.status_code == 404  # 404 (slug não existe), não um redirect de troca de senha
ok("Usuário comum (deve_trocar_senha=False) não é afetado pelo middleware")

# ===========================================================================
# C) Superadmin com deve_trocar_senha=True: QUALQUER rota redireciona pra
#    /trocar-senha-obrigatoria, mesmo tentando ir direto pra outra tela.
# ===========================================================================
with app.app_context():
    senha_temporaria = "TemporariaXYZ123"
    su = Usuario.query.filter_by(login=SUPER_ADMIN_LOGIN).first()
    su.set_senha(senha_temporaria)
    db.session.commit()

c_super = app.test_client()
resp = c_super.post("/login", data={"login": SUPER_ADMIN_LOGIN, "senha": senha_temporaria}, follow_redirects=True)
assert resp.status_code == 200
assert b"Troca de senha obrigat" in resp.data
ok("Login com deve_trocar_senha=True cai direto na tela de troca obrigatória")

resp = c_super.get("/admin/empresas", follow_redirects=False)
assert resp.status_code == 302
assert "/trocar-senha-obrigatoria" in resp.headers["Location"]
ok("Tentar acessar /admin/empresas direto redireciona pra troca de senha (middleware bloqueia)")

resp = c_super.get("/", follow_redirects=False)
assert resp.status_code == 302
assert "/trocar-senha-obrigatoria" in resp.headers["Location"]
ok("Até a rota raiz (core.index) é bloqueada enquanto o flag estiver True")

# ===========================================================================
# D) Validação: senha curta ou confirmação divergente é rejeitada, flag
#    continua True, usuário continua bloqueado.
# ===========================================================================
resp = c_super.post(
    "/trocar-senha-obrigatoria",
    data={"nova_senha": "curta", "confirmar_senha": "curta"},
    follow_redirects=True,
)
assert b"pelo menos 8 caracteres" in resp.data
with app.app_context():
    assert Usuario.query.filter_by(login=SUPER_ADMIN_LOGIN).first().deve_trocar_senha is True
ok("Senha curta (< 8 caracteres) é rejeitada, flag continua True")

resp = c_super.post(
    "/trocar-senha-obrigatoria",
    data={"nova_senha": "SenhaNova123!", "confirmar_senha": "SenhaDiferente456!"},
    follow_redirects=True,
)
assert "não coincidem".encode("utf-8") in resp.data
with app.app_context():
    assert Usuario.query.filter_by(login=SUPER_ADMIN_LOGIN).first().deve_trocar_senha is True
ok("Confirmação divergente é rejeitada, flag continua True")

# ===========================================================================
# E) Troca válida: limpa o flag, libera o resto do sistema.
# ===========================================================================
resp = c_super.post(
    "/trocar-senha-obrigatoria",
    data={"nova_senha": "SenhaNovaValida123!", "confirmar_senha": "SenhaNovaValida123!"},
    follow_redirects=True,
)
assert resp.status_code == 200
with app.app_context():
    su_final = Usuario.query.filter_by(login=SUPER_ADMIN_LOGIN).first()
    assert su_final.deve_trocar_senha is False
    assert su_final.checar_senha("SenhaNovaValida123!")
    assert not su_final.checar_senha(senha_temporaria)
ok("Troca válida limpa deve_trocar_senha e grava o hash da nova senha")

resp = c_super.get("/admin/empresas", follow_redirects=False)
assert resp.status_code == 200
ok("Depois da troca, /admin/empresas (e o resto do sistema) volta a ficar acessível normalmente")

# ===========================================================================
# F) logout continua acessível mesmo com o flag True (evita lockout total) -
#    testado com um usuário novo, sem trocar a senha, pra não interferir no
#    estado já validado acima.
# ===========================================================================
with app.app_context():
    outro_super = Usuario(nome="Super 2", login="super2_troca", empresa_id=None)
    outro_super.set_senha("temporaria2")
    outro_super.deve_trocar_senha = True
    db.session.add(outro_super)
    db.session.commit()

c_super2 = app.test_client()
c_super2.post("/login", data={"login": "super2_troca", "senha": "temporaria2"}, follow_redirects=True)
resp = c_super2.get("/logout", follow_redirects=False)
assert resp.status_code == 302
assert "/trocar-senha-obrigatoria" not in resp.headers["Location"]
ok("Logout continua acessível mesmo com deve_trocar_senha=True (sem lockout)")

print()
print(f"TODOS OS {len(passos)} TESTES DE TROCA DE SENHA OBRIGATÓRIA PASSARAM")
