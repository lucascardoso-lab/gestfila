"""
Teste de regressão: dark mode (etapa 2 de 2, ver BLUEPRINT.md - etapa 1 foi a
consolidação prévia de variáveis, adendo 10.55). Mecanismo de tema (claro/
escuro/automático via `data-theme` no <html>, persistido em localStorage POR
NAVEGADOR - inclusive em totem/TV, risco aceito explicitamente pelo cliente,
já documentado no adendo 10.55) + paleta escura para --gf-* + paleta clara
NOVA para --painel-* (painel de TV só tinha escura antes).

Cobre:
1. As 7 "áreas-raiz" (base.html, admin/atendimento/relatorios/_layout.html,
   core/dashboard.html, totem/_layout.html, painel/_layout.html) renderizam
   o script de tema (`window.GestfilaTema`) e o botão de alternar
   (`.alternador-tema`).
2. gestfila.css tem um bloco `[data-theme="dark"]` redefinindo as variáveis
   temáticas com valores DIFERENTES dos claros (não é um bloco vazio/
   decorativo).
3. painel/_layout.html ganha uma paleta CLARA nova (`[data-theme="light"]`)
   com valores DIFERENTES da paleta escura existente (`:root`).
4. O script de tema lê a preferência salva em localStorage ao carregar
   (checagem ESTRUTURAL do código JS gerado - mesma limitação de sempre
   pra testes deste projeto, sem runtime de navegador de verdade).
5. O cupom (totem/senha_gerada.html) nunca referencia nenhuma variável de
   tema - continua sempre branco/preto fixo, dentro E fora do
   `@media print`, independente do tema ativo.

Execução: python -m tests.test_regressao_dark_mode (a partir da raiz do
projeto, com o virtualenv ativado).
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
    perfil_recepcao = Perfil(nome="atendente")
    db.session.add_all([perfil_admin, perfil_recepcao])
    db.session.commit()

    empresa = Empresa(nome="Clinica Dark Mode")
    db.session.add(empresa)
    db.session.commit()

    unidade = Unidade(empresa_id=empresa.id, nome="Unidade 1")
    db.session.add(unidade)
    db.session.commit()

    totem = Totem(empresa_id=empresa.id, unidade_id=unidade.id, nome="Totem 1", slug="totem-dark-mode")
    tv = Tv(empresa_id=empresa.id, unidade_id=unidade.id, nome="TV 1", slug="tv-dark-mode")
    db.session.add_all([totem, tv])
    db.session.commit()

    u = Usuario(empresa_id=empresa.id, nome="admin_dark_mode", login="admin_dark_mode")
    u.set_senha("senha123")
    u.perfis = [perfil_admin, perfil_recepcao]
    u.unidades = [unidade]
    db.session.add(u)
    db.session.commit()

    ids = dict(totem_slug=totem.slug, tv_slug=tv.slug)


def login_como(login):
    c = app.test_client()
    resp = c.post("/login", data={"login": login, "senha": "senha123"}, follow_redirects=True)
    assert resp.status_code == 200, (login, resp.status_code)
    return c


c = login_como("admin_dark_mode")
c_anonimo = app.test_client()

# ===========================================================================
# A) As 7 áreas-raiz renderizam o script de tema + o botão de alternar.
# ===========================================================================
paginas = {
    "base_login": c_anonimo.get("/login"),
    "admin": c.get("/admin/unidades"),
    "atendimento": c.get("/atendimento/iniciar-turno"),
    "relatorios": c.get("/relatorios/"),
    "core_dashboard": c.get("/dashboard"),
    "totem": c.get(f"/totem/{ids['totem_slug']}"),
    "painel_tv": c.get(f"/painel/tv/{ids['tv_slug']}"),
}
for nome_area, resp in paginas.items():
    assert resp.status_code == 200, (nome_area, resp.status_code)

textos = {nome: resp.data.decode("utf-8") for nome, resp in paginas.items()}

for nome_area, texto in textos.items():
    assert "window.GestfilaTema" in texto, nome_area
    assert 'class="alternador-tema' in texto, nome_area
ok("As 7 áreas-raiz (base/admin/atendimento/relatorios/core.dashboard/totem/painel) renderizam o script de tema e o botão de alternar")

# Script de tema vem ANTES do <link> de gestfila.css (evita flash do tema
# errado no primeiro paint - aplicação síncrona precisa rodar antes do CSSOM
# terminar de montar).
for nome_area, texto in textos.items():
    indice_script = texto.index("window.GestfilaTema")
    indice_link_css = texto.index("gestfila.css")
    assert indice_script < indice_link_css, nome_area
ok("Script de tema vem antes do <link> de gestfila.css nas 7 áreas (evita flash de tema errado)")

# Botão flutuante nas 3 áreas sem <nav> (totem/painel/login); dentro do
# <nav> nas outras 4 (sem a classe --flutuante).
for nome_area in ("base_login", "totem", "painel_tv"):
    assert "alternador-tema--flutuante" in textos[nome_area], nome_area
for nome_area in ("admin", "atendimento", "relatorios", "core_dashboard"):
    assert "alternador-tema--flutuante" not in textos[nome_area], nome_area
ok("Botão flutuante nas 3 áreas sem <nav> (login/totem/painel); dentro do <nav>, sem a variante flutuante, nas outras 4")

# ===========================================================================
# B) gestfila.css tem [data-theme="dark"] com valores DIFERENTES do :root
#    (claro) - não é um bloco vazio/decorativo.
# ===========================================================================
CAMINHO_CSS = os.path.join(os.path.dirname(__file__), "..", "app", "static", "css", "gestfila.css")
with open(CAMINHO_CSS, encoding="utf-8") as f:
    css = f.read()

assert '[data-theme="dark"] {' in css
inicio_root = css.index(":root {")
fim_root = css.index("}", inicio_root)
bloco_claro = css[inicio_root:fim_root]

inicio_escuro = css.index('[data-theme="dark"] {')
fim_escuro = css.index("}", inicio_escuro)
bloco_escuro = css[inicio_escuro:fim_escuro]

VARIAVEIS_TEMATICAS = [
    "--gf-primaria", "--gf-primaria-escura", "--gf-primaria-clara",
    "--gf-cinza-escuro", "--gf-cinza-medio", "--gf-cinza-claro",
    "--gf-sucesso", "--gf-alerta", "--gf-info", "--gf-superficie",
    "--gf-texto-secundario", "--gf-borda", "--gf-info-fundo",
    "--gf-alerta-fundo", "--gf-sucesso-fundo",
]
for nome in VARIAVEIS_TEMATICAS:
    valor_claro = bloco_claro[bloco_claro.index(nome):].split(";")[0].split(":")[1].strip()
    assert nome in bloco_escuro, nome
    valor_escuro = bloco_escuro[bloco_escuro.index(nome):].split(";")[0].split(":")[1].strip()
    assert valor_claro != valor_escuro, (nome, valor_claro, valor_escuro)
ok("gestfila.css: as 15 variáveis temáticas têm valor DIFERENTE em [data-theme=\"dark\"] em relação ao :root (claro)")

# --gf-branco/--gf-nav-texto/--gf-escuro-fixo continuam CONSTANTES (não
# redefinidas no bloco escuro) - decisão deliberada (etapa 1 pro nav-texto,
# etapa 2 pro branco/escuro-fixo), não esquecimento.
for nome in ("--gf-branco", "--gf-nav-texto", "--gf-escuro-fixo"):
    assert nome in bloco_claro, nome
    assert nome not in bloco_escuro, nome
ok("--gf-branco/--gf-nav-texto/--gf-escuro-fixo continuam constantes (não redefinidas em [data-theme=\"dark\"]), de propósito")

# ===========================================================================
# C) painel/_layout.html ganha [data-theme="light"] com valores DIFERENTES
#    da paleta escura existente (:root) - paleta clara nova, não existia
#    antes desta etapa.
# ===========================================================================
texto_painel_fonte_inicio = textos["painel_tv"].index("--painel-fundo:")
# usa o HTML renderizado (que inclui o <style> inline) pra não depender de
# outro arquivo - painel/_layout.html é quem define :root e [data-theme=light].
html_painel = textos["painel_tv"]
assert '[data-theme="light"] {' in html_painel
inicio_root_painel = html_painel.index(":root {")
fim_root_painel = html_painel.index("}", inicio_root_painel)
bloco_painel_escuro = html_painel[inicio_root_painel:fim_root_painel]

inicio_claro_painel = html_painel.index('[data-theme="light"] {')
fim_claro_painel = html_painel.index("}", inicio_claro_painel)
bloco_painel_claro = html_painel[inicio_claro_painel:fim_claro_painel]

VARIAVEIS_PAINEL = ["--painel-fundo", "--painel-fundo-header", "--painel-fundo-elemento", "--painel-texto-secundario", "--painel-texto-principal"]
for nome in VARIAVEIS_PAINEL:
    assert nome in bloco_painel_claro, nome
    valor_escuro = bloco_painel_escuro[bloco_painel_escuro.index(nome):].split(";")[0].split(":")[1].strip()
    valor_claro = bloco_painel_claro[bloco_painel_claro.index(nome):].split(";")[0].split(":")[1].strip()
    assert valor_claro != valor_escuro, (nome, valor_claro, valor_escuro)
ok("painel/_layout.html: paleta clara nova ([data-theme=\"light\"]) com as 5 variáveis --painel-* diferentes da paleta escura existente")

# --gf-primaria/primaria-escura/sucesso/alerta/info/cinza-medio usados no
# painel de TV ficam PINADOS (mesmo valor sempre) - painel não muda de
# aparência conforme o tema geral do resto do site.
for nome, valor in [
    ("--gf-primaria", "#C0392B"), ("--gf-primaria-escura", "#922B21"),
    ("--gf-sucesso", "#27AE60"), ("--gf-alerta", "#E67E22"),
    ("--gf-info", "#2980B9"), ("--gf-cinza-medio", "#95A5A6"),
]:
    assert f"{nome}: {valor};" in bloco_painel_escuro, nome
ok("painel/_layout.html: --gf-primaria/sucesso/alerta/info/cinza-medio ficam pinados nos valores canônicos, independentes do tema geral")

# ===========================================================================
# D) Script de tema lê localStorage ao carregar (checagem estrutural do
#    código JS - sem runtime de navegador real).
# ===========================================================================
CAMINHO_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "app", "templates", "_tema_script.html")
with open(CAMINHO_SCRIPT, encoding="utf-8") as f:
    script = f.read()

assert 'localStorage.getItem(CHAVE)' in script
assert 'localStorage.setItem(CHAVE' in script
assert 'setAttribute("data-theme"' in script
assert 'prefers-color-scheme: dark' in script
ok("_tema_script.html: lê/escreve localStorage e resolve 'automático' via prefers-color-scheme, estruturalmente presente no código")

# ===========================================================================
# E) Cupom (totem/senha_gerada.html) nunca referencia variável de tema -
#    branco/preto fixo, dentro e fora do @media print, sempre.
# ===========================================================================
CAMINHO_CUPOM = os.path.join(os.path.dirname(__file__), "..", "app", "templates", "totem", "senha_gerada.html")
with open(CAMINHO_CUPOM, encoding="utf-8") as f:
    cupom_fonte = f.read()

# Bloco <style> inteiro (não só ".cupom{...}") - este arquivo só tem CSS do
# cupom (prévia + @media print), então cobre as duas partes de uma vez sem
# precisar contar chaves aninhadas do @media print pra achar o fim exato.
inicio_cupom_css = cupom_fonte.index("<style>")
fim_media_print = cupom_fonte.index("</style>")
bloco_cupom_completo = cupom_fonte[inicio_cupom_css:fim_media_print]

# Checa DECLARAÇÕES CSS reais ("propriedade: var(...)"), não o token solto -
# o próprio comentário desta seção MENCIONA "var(--gf-superficie)" como
# texto explicativo (por que NÃO é usado aqui), o que faria uma busca pelo
# token nu disparar em falso positivo no comentário, não numa declaração.
assert "background: var(--gf" not in bloco_cupom_completo
assert "color: var(--gf" not in bloco_cupom_completo
assert "background: var(--painel" not in bloco_cupom_completo
assert "color: var(--painel" not in bloco_cupom_completo
assert "[data-theme=" not in bloco_cupom_completo
assert "background: #fff" in bloco_cupom_completo
assert "color: #000" in bloco_cupom_completo
ok("Cupom (senha_gerada.html): nenhuma variável de tema referenciada dentro/fora do @media print - continua branco/preto fixo sempre")

print()
print(f"TODOS OS {len(passos)} TESTES DE DARK MODE (ETAPA 2) PASSARAM")
