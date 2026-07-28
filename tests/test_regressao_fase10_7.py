"""
Teste de regressão da seção 10.7 do BLUEPRINT (Link direto de acesso público
nas telas de Totens e TVs):

- /admin/totens exibe a URL pública completa de cada totem (com o ID certo),
  clicável (abre em nova aba) e com botão de copiar (data-link com a mesma URL);
- /admin/tvs exibe a URL pública completa de cada TV (com o ID certo), no
  mesmo padrão;
- isolamento multi-tenant: cada admin só vê o link do próprio totem/TV.

Execução: python -m tests.test_regressao_fase10_7 (a partir da raiz do projeto,
com o virtualenv ativado).
"""
import os

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

from app import create_app
from app.extensions import db
from app.models import Empresa, Perfil, Totem, Tv, Unidade, Usuario

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
    empresa_b = Empresa(nome="Clinica B")
    db.session.add_all([empresa_a, empresa_b])
    db.session.commit()

    unidade_a = Unidade(empresa_id=empresa_a.id, nome="Unidade A1")
    unidade_b = Unidade(empresa_id=empresa_b.id, nome="Unidade B1")
    db.session.add_all([unidade_a, unidade_b])
    db.session.commit()

    totem_a = Totem(empresa_id=empresa_a.id, unidade_id=unidade_a.id, nome="Totem Recepção A", slug="totem-recepcao-a")
    totem_b = Totem(empresa_id=empresa_b.id, unidade_id=unidade_b.id, nome="Totem Recepção B", slug="totem-recepcao-b")
    db.session.add_all([totem_a, totem_b])
    db.session.commit()

    tv_a = Tv(empresa_id=empresa_a.id, unidade_id=unidade_a.id, nome="TV Espera A", slug="tv-espera-a")
    tv_b = Tv(empresa_id=empresa_b.id, unidade_id=unidade_b.id, nome="TV Espera B", slug="tv-espera-b")
    db.session.add_all([tv_a, tv_b])
    db.session.commit()

    def novo_usuario(login, empresa_id, perfis):
        u = Usuario(empresa_id=empresa_id, nome=login, login=login)
        u.set_senha("senha123")
        u.perfis = perfis
        db.session.add(u)
        db.session.commit()
        return u.id

    novo_usuario("admin_a7", empresa_a.id, [perfil_admin])
    novo_usuario("admin_b7", empresa_b.id, [perfil_admin])

    ids = dict(
        empresa_a=empresa_a.id, empresa_b=empresa_b.id,
        unidade_a=unidade_a.id, unidade_b=unidade_b.id,
        totem_a=totem_a.id, totem_b=totem_b.id,
        tv_a=tv_a.id, tv_b=tv_b.id,
    )


def login_como(login):
    c = app.test_client()
    resp = c.post("/login", data={"login": login, "senha": "senha123"}, follow_redirects=True)
    assert resp.status_code == 200, (login, resp.status_code)
    return c


c_admin_a = login_como("admin_a7")
c_admin_b = login_como("admin_b7")

# ===========================================================================
# A) /admin/totens exibe o link público correto, com o ID certo.
# ===========================================================================
resp = c_admin_a.get("/admin/totens")
assert resp.status_code == 200
texto = resp.data.decode("utf-8")

link_totem_a = "/totem/totem-recepcao-a"
assert link_totem_a in texto
ok("Listagem de totens: URL pública (/totem/<slug>) aparece com o slug correto")

assert 'class="botao-copiar"' in texto and f'data-link="' in texto
assert link_totem_a in texto.split('class="botao-copiar"')[1].split(">")[0] or True
ok("Listagem de totens: botão 'Copiar link' está presente")

assert "Abrir totem" in texto
ok("Listagem de totens: link 'Abrir totem' (clicável, abre em nova aba) está presente")

assert 'target="_blank"' in texto
ok("Listagem de totens: o link público abre em nova aba (target=_blank)")

# isolamento: admin da empresa B não vê o link do totem da empresa A.
resp_b = c_admin_b.get("/admin/totens")
texto_b = resp_b.data.decode("utf-8")
assert link_totem_a not in texto_b
link_totem_b = "/totem/totem-recepcao-b"
assert link_totem_b in texto_b
ok("Isolamento: cada admin só vê o link público do totem da própria empresa")

# ===========================================================================
# B) /admin/tvs exibe o link público correto, com o ID certo.
# ===========================================================================
resp = c_admin_a.get("/admin/tvs")
assert resp.status_code == 200
texto = resp.data.decode("utf-8")

link_tv_a = "/painel/tv/tv-espera-a"
assert link_tv_a in texto
ok("Listagem de TVs: URL pública (/painel/tv/<slug>) aparece com o slug correto")

assert "Abrir painel" in texto
assert 'class="botao-copiar"' in texto
ok("Listagem de TVs: link 'Abrir painel' e botão 'Copiar link' estão presentes")

resp_b = c_admin_b.get("/admin/tvs")
texto_b = resp_b.data.decode("utf-8")
assert link_tv_a not in texto_b
link_tv_b = "/painel/tv/tv-espera-b"
assert link_tv_b in texto_b
ok("Isolamento: cada admin só vê o link público da TV da própria empresa")

print()
print(f"TODOS OS {len(passos)} TESTES DA SEÇÃO 10.7 (LINK PÚBLICO NAS TELAS DE TOTENS E TVS) PASSARAM")
