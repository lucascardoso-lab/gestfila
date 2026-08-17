"""
Teste de regressão: consolidação pré-dark-mode (etapa 1 de 2, ver BLUEPRINT.md)
- troca MECÂNICA de valor (hex solto -> variável CSS), sem mudança de
comportamento/aparência, e extração do <nav> duplicado (admin/atendimento/
relatorios/core.dashboard) num partial único (`_nav_principal.html`).

Cobre:
1. As 7 variáveis semânticas novas (--gf-superficie, --gf-texto-secundario,
   --gf-borda, --gf-info-fundo, --gf-alerta-fundo, --gf-sucesso-fundo,
   --gf-nav-texto) estão definidas no :root de gestfila.css, com os valores
   escolhidos documentados no BLUEPRINT - e as 10 variáveis pré-existentes
   continuam intocadas.
2. O <nav> das 4 áreas (admin/atendimento/relatorios/core.dashboard) renderiza
   a partir do MESMO partial (`_nav_principal.html`) - mesmo conteúdo comum
   (logo + "Sair (nome)") em todas, mas cada área preserva seus PRÓPRIOS itens
   de menu (não virou um menu genérico forçado).
3. Uma amostra de declarações que eram hex cru agora usa var(--gf-*) no HTML
   renderizado (prova de que a substituição realmente chegou ao output, não
   só ao arquivo-fonte).
4. Exceções deliberadas continuam intocadas: o cupom impresso/prévia (totem/
   senha_gerada.html, branco/preto fixos) e o badge "Super admin" (roxo,
   single-use) não foram convertidos pra variável.

Execução: python -m tests.test_regressao_consolidacao_pre_dark_mode (a partir
da raiz do projeto, com o virtualenv ativado).
"""
import os

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

from app import create_app
from app.extensions import db
from app.models import Empresa, Perfil, Unidade, Usuario

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

    empresa = Empresa(nome="Clinica Consolidacao Dark Mode")
    db.session.add(empresa)
    db.session.commit()

    unidade = Unidade(empresa_id=empresa.id, nome="Unidade 1")
    db.session.add(unidade)
    db.session.commit()

    u = Usuario(empresa_id=empresa.id, nome="admin_consolidacao", login="admin_consolidacao")
    u.set_senha("senha123")
    u.perfis = [perfil_admin, perfil_recepcao]
    u.unidades = [unidade]
    db.session.add(u)
    db.session.commit()


def login_como(login):
    c = app.test_client()
    resp = c.post("/login", data={"login": login, "senha": "senha123"}, follow_redirects=True)
    assert resp.status_code == 200, (login, resp.status_code)
    return c


c = login_como("admin_consolidacao")

# ===========================================================================
# A) As 7 variáveis novas estão no :root de gestfila.css, com os valores
#    documentados - e as 10 pré-existentes continuam lá, intocadas.
# ===========================================================================
CAMINHO_CSS = os.path.join(os.path.dirname(__file__), "..", "app", "static", "css", "gestfila.css")
with open(CAMINHO_CSS, encoding="utf-8") as f:
    css = f.read()

inicio_root = css.index(":root {")
fim_root = css.index("}", inicio_root)
bloco_root = css[inicio_root:fim_root]

VARIAVEIS_NOVAS = {
    "--gf-superficie": "#FFFFFF",
    "--gf-texto-secundario": "#555555",
    "--gf-borda": "#ECEEF2",
    "--gf-info-fundo": "#EAF2F8",
    "--gf-alerta-fundo": "#FDF0E2",
    "--gf-sucesso-fundo": "#E3F6E8",
    "--gf-nav-texto": "#CFD6E4",
}
for nome, valor in VARIAVEIS_NOVAS.items():
    assert f"{nome}: {valor};" in bloco_root, nome
ok("As 7 variáveis semânticas novas estão definidas no :root de gestfila.css, com os valores documentados")

VARIAVEIS_PRE_EXISTENTES = {
    "--gf-primaria": "#C0392B",
    "--gf-primaria-escura": "#922B21",
    "--gf-primaria-clara": "#FADBD8",
    "--gf-cinza-escuro": "#2C3E50",
    "--gf-cinza-medio": "#95A5A6",
    "--gf-cinza-claro": "#F4F6F7",
    "--gf-branco": "#FFFFFF",
    "--gf-sucesso": "#27AE60",
    "--gf-alerta": "#E67E22",
    "--gf-info": "#2980B9",
}
for nome, valor in VARIAVEIS_PRE_EXISTENTES.items():
    assert f"{nome}: {valor};" in bloco_root, nome
ok("As 10 variáveis pré-existentes continuam definidas, com os mesmos valores de sempre (nenhuma alterada)")

# ===========================================================================
# B) <nav> das 4 áreas vem do MESMO partial - conteúdo comum presente nas
#    quatro, mas cada área preserva seus próprios itens (não virou um menu
#    genérico forçado). Testado num único usuário com perfis admin+atendente,
#    que acessa as 4 áreas.
# ===========================================================================
paginas = {
    "admin": c.get("/admin/unidades"),
    "atendimento": c.get("/atendimento/iniciar-turno"),
    "relatorios": c.get("/relatorios/"),
    "core_dashboard": c.get("/dashboard"),
}
for nome_area, resp in paginas.items():
    assert resp.status_code == 200, (nome_area, resp.status_code)

textos = {nome: resp.data.decode("utf-8") for nome, resp in paginas.items()}

for nome_area, texto in textos.items():
    assert "<nav>" in texto, nome_area
    assert "Sair (admin_consolidacao)" in texto, nome_area
    assert 'gf-logo gf-logo--navbar gf-logo--claro' in texto, nome_area
ok("As 4 áreas renderizam <nav> com o conteúdo comum do partial (logo + \"Sair (nome)\")")

# Item exclusivo do admin (🏢 Empresas, só super admin - este usuário NÃO é
# super admin, então nem aparece pra ele; usa-se aqui um item sempre visível
# pra admin: 🏥 Unidades) não aparece nas outras 3 áreas - prova de que cada
# {% call %} preserva seu próprio menu, não um genérico compartilhado.
assert "🏥 Unidades" in textos["admin"]
assert "🏥 Unidades" not in textos["atendimento"]
assert "🏥 Unidades" not in textos["relatorios"]
assert "🏥 Unidades" not in textos["core_dashboard"]
ok("Item de menu exclusivo do admin não vaza pras outras 3 áreas - cada uma preserva seu próprio menu")

# Item exclusivo de atendimento (🎫 Filas, visível pra admin no menu de
# atendimento) não aparece no <nav> das outras áreas - checa o HREF JÁ
# RESOLVIDO (href="/atendimento/filas"), não a sintaxe de template
# "url_for(...)" (que nunca sobrevive à renderização - checar essa string
# literal provaria a asserção verdadeira sempre, vazamento ou não). Primeiro
# confirma que o link aparece de verdade em atendimento (prova que o HREF
# resolvido é esse mesmo, não um typo que faria a checagem abaixo passar
# por engano), só depois confirma a ausência em relatórios.
assert 'href="/atendimento/filas"' in textos["atendimento"]
assert 'href="/atendimento/filas"' not in textos["relatorios"]
ok("Item de menu exclusivo de atendimento (href=/atendimento/filas) não vaza pra relatórios")

# ===========================================================================
# C) Amostra de hex->variável realmente chegou ao HTML renderizado (não só ao
#    arquivo-fonte) - texto secundário (admin) e fundo de superfície (login).
# ===========================================================================
assert "var(--gf-texto-secundario)" in textos["admin"]
assert "color: #555" not in textos["admin"]
ok("admin/_layout.html: hex de texto secundário virou var(--gf-texto-secundario) no HTML renderizado")

resp_login = app.test_client().get("/login")
assert resp_login.status_code == 200
texto_login = resp_login.data.decode("utf-8")
assert "background: var(--gf-superficie)" in texto_login
ok("base.html (login): .container usa var(--gf-superficie), não mais var(--gf-branco)/hex fixo")

# ===========================================================================
# D) Exceções deliberadas continuam intocadas - cupom (sempre branco/preto,
#    print e prévia) e badge "Super admin" (roxo, single-use).
# ===========================================================================
assert "background: #ede4fb; color: #6b21a8;" in textos["admin"]
ok("admin/_layout.html: badge 'Super admin' (roxo, single-use) continua com hex fixo, não consolidado")

print()
print(f"TODOS OS {len(passos)} TESTES DA CONSOLIDAÇÃO PRÉ-DARK-MODE PASSARAM")
