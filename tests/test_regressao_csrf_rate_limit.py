"""
Teste de regressão: hardening da Fase 10 do BLUEPRINT - proteção CSRF
(Flask-WTF, app/extensions.py::csrf) e rate limiting (Flask-Limiter,
app/extensions.py::limiter, decorators @limiter.limit em app/auth/routes.py e
app/totem/routes.py).

Gap de cobertura identificado na auditoria de testes: TestingConfig desliga os
dois por padrão (WTF_CSRF_ENABLED=False, RATELIMIT_ENABLED=False - ver
app/config.py) para não travar a suíte inteira, mas nenhum teste dedicado
religava os dois de propósito pra exercitar o comportamento real, como já é
feito para ENCERRAMENTO_SESSOES_ATIVO em test_regressao_encerramento_automatico.py.

Cobre:
1) CSRF: POST sem `csrf_token` é rejeitado com 400; POST com o token extraído
   do formulário renderizado (GET) é aceito normalmente; GET nunca exige token.
2) Rate limit: a rota de login (10/min, app/auth/routes.py) responde 429 depois
   de estourar o limite, com o corpo/estado do banco intactos (nenhum novo
   login bem-sucedido); some novamente após resetar o storage do limiter
   (equivalente a esperar a janela passar).

Execução: python -m tests.test_regressao_csrf_rate_limit (a partir da raiz do
projeto, com o virtualenv ativado).
"""
import os
import re

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

from app import create_app
from app.extensions import db, limiter
from app.models import Empresa, Perfil, Unidade, Usuario

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

    empresa = Empresa(nome="Clinica A")
    db.session.add(empresa)
    db.session.commit()

    unidade = Unidade(empresa_id=empresa.id, nome="Unidade A1")
    db.session.add(unidade)
    db.session.commit()

    admin = Usuario(empresa_id=empresa.id, nome="Admin A", login="admin_csrf")
    admin.set_senha("senha123")
    admin.perfis = [perfil_admin]
    db.session.add(admin)
    db.session.commit()

    ids = dict(empresa=empresa.id)


def extrair_csrf_token(html):
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert m, "campo csrf_token não encontrado no HTML renderizado"
    return m.group(1)


# ===========================================================================
# A) CSRF - religa a proteção (desligada por padrão em TestingConfig, mesmo
#    padrão de app.config[...] = True usado em test_regressao_encerramento_automatico).
# ===========================================================================
app.config["WTF_CSRF_ENABLED"] = True
try:
    c = app.test_client()
    resp_login_get = c.get("/login")
    assert resp_login_get.status_code == 200
    token = extrair_csrf_token(resp_login_get.data.decode("utf-8"))
    ok("GET /login (sem token) responde 200 normalmente - CSRF não se aplica a GET")

    resp_sem_token = c.post("/login", data={"login": "admin_csrf", "senha": "senha123"})
    assert resp_sem_token.status_code == 400
    ok("POST /login sem csrf_token é rejeitado com 400 (CSRF ligado)")

    resp_com_token = c.post(
        "/login",
        data={"login": "admin_csrf", "senha": "senha123", "csrf_token": token},
        follow_redirects=True,
    )
    assert resp_com_token.status_code == 200
    assert "/login" not in resp_com_token.request.path
    ok("POST /login com csrf_token válido é aceito normalmente e autentica")

    # Endpoint admin também protegido - mesmo comportamento fora do auth. Cada
    # test_client tem sua própria sessão/cookie, então o token do CSRF precisa
    # ser buscado de novo para esta sessão (não reaproveitar o de `c` acima).
    c2 = app.test_client()
    resp_login2_get = c2.get("/login")
    token_login2 = extrair_csrf_token(resp_login2_get.data.decode("utf-8"))
    c2.post("/login", data={"login": "admin_csrf", "senha": "senha123", "csrf_token": token_login2})
    resp_form = c2.get(f"/admin/unidades/nova?empresa_id={ids['empresa']}")
    assert resp_form.status_code == 200
    token_unidade = extrair_csrf_token(resp_form.data.decode("utf-8"))

    resp_sem_token_admin = c2.post(
        f"/admin/unidades/nova?empresa_id={ids['empresa']}", data={"nome": "Sem Token"},
    )
    assert resp_sem_token_admin.status_code == 400
    with app.app_context():
        assert Unidade.query.filter_by(nome="Sem Token").first() is None
    ok("POST /admin/unidades/nova sem csrf_token é rejeitado com 400, nada é criado")

    resp_com_token_admin = c2.post(
        f"/admin/unidades/nova?empresa_id={ids['empresa']}",
        data={"nome": "Com Token", "csrf_token": token_unidade},
        follow_redirects=True,
    )
    assert resp_com_token_admin.status_code == 200
    with app.app_context():
        assert Unidade.query.filter_by(nome="Com Token").first() is not None
    ok("POST /admin/unidades/nova com csrf_token válido cria a unidade normalmente")
finally:
    app.config["WTF_CSRF_ENABLED"] = False

# ===========================================================================
# B) Rate limit - Flask-Limiter decide `self.enabled` (config RATELIMIT_ENABLED)
#    dentro de init_app() e não relê o config a cada request (diferente do CSRF,
#    que checa app.config live em cada requisição) - religar via
#    `app.config["RATELIMIT_ENABLED"] = True` no app já criado NÃO reativa o
#    limiter. Por isso este bloco cria um app Flask próprio, com
#    RATELIMIT_ENABLED=True desde a criação (TestingConfig + override via
#    subclasse), só para exercitar o limite de /login (10/min,
#    app/auth/routes.py) - mesmo banco de dados/usuários já criados acima
#    (SQLite em memória do processo, não por app).
# ===========================================================================
from app.config import TestingConfig, config_by_name


class TestingConfigComRateLimit(TestingConfig):
    RATELIMIT_ENABLED = True


config_by_name["testing_rate_limit"] = TestingConfigComRateLimit
app_rl = create_app("testing_rate_limit")

with app_rl.app_context():
    db.create_all()  # SQLite em memória própria deste app - precisa das tabelas de novo
    limiter.reset()

c3 = app_rl.test_client()
respostas = []
for _ in range(12):
    resp = c3.post("/login", data={"login": "inexistente_rl", "senha": "errada"})
    respostas.append(resp.status_code)

assert 429 in respostas, respostas
indice_bloqueio = respostas.index(429)
assert indice_bloqueio <= 10  # o 11º request (índice 10) já deveria estar bloqueado
ok(f"POST /login estoura o limite de 10/min e responde 429 a partir da tentativa {indice_bloqueio + 1}")

with app.app_context():
    # nenhuma das tentativas era credencial válida, mas confirma que o rate
    # limit por si só não corrompeu nem alterou nenhum usuário existente.
    assert Usuario.query.filter_by(login="admin_csrf").first() is not None
ok("Rate limit não afeta dados existentes - só bloqueia novas requisições")

with app_rl.app_context():
    limiter.reset()
resp_depois_reset = c3.post("/login", data={"login": "inexistente_rl", "senha": "errada"})
assert resp_depois_reset.status_code != 429
ok("Após resetar a janela do limiter, novas requisições voltam a ser aceitas (não ficam bloqueadas para sempre)")

with app_rl.app_context():
    limiter.reset()

print()
print(f"TODOS OS {len(passos)} TESTES DE CSRF E RATE LIMIT PASSARAM")
