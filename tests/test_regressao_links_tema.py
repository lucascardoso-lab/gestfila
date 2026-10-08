"""
Teste de regressão (estrutural): cor dos links de conteúdo nos temas claro e
escuro (adendo 10.72).

Antes, nenhum layout definia cor para <a>: links como "Editar"/"Configurar
rodízio" ficavam no azul (e, visitados, no roxo) padrão do navegador -
ilegíveis no tema escuro. Cobre:

1) Token --gf-link existe nos DOIS temas (:root e [data-theme="dark"]) com
   contraste >= 4,5:1 sobre a superfície, o fundo da página e os fundos de
   aviso (info/alerta/sucesso/erro) do respectivo tema - calculado a partir
   dos valores reais do gestfila.css, não de números copiados.
2) As regras de link (cor, hover, foco) têm o seletor INTEIRO dentro de
   :where() (especificidade zero - classes como .botao continuam vencendo,
   mesmo num link visitado), escopadas a main/.container, sem `nav` e com
   exclusão explícita do painel de TV; não existe regra `a`/`a:visited`
   global solta.
3) Fora do escopo por construção: painel de TV marcado com .area-painel-tv;
   totem sem <main>/.container; cupom sem <a>; PDF dos relatórios sem
   gestfila.css.
4) A classe .link-admin do 10.71 foi removida (uma fonte de verdade só).

Execução: python -m tests.test_regressao_links_tema (a partir da raiz do
projeto, com o virtualenv ativado).
"""
import glob
import re

passos = []


def ok(msg):
    passos.append(msg)
    print("OK:", msg)


def ler(caminho):
    with open(caminho, encoding="utf-8") as f:
        return f.read()


CSS = ler("app/static/css/gestfila.css")
CSS_SEM_COMENTARIOS = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)


def bloco(seletor):
    m = re.search(re.escape(seletor) + r"\s*\{(.*?)\}", CSS_SEM_COMENTARIOS, re.S)
    assert m, seletor
    return dict(re.findall(r"(--gf-[a-z-]+):\s*(#[0-9A-Fa-f]{6})", m.group(1)))


def luminancia(hexa):
    r, g, b = (int(hexa.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def contraste(a, b):
    la, lb = sorted((luminancia(a), luminancia(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


# ===========================================================================
# 1) Token nos dois temas, com contraste.
# ===========================================================================
claro = bloco(":root")
escuro = {**claro, **bloco('[data-theme="dark"]')}
assert "--gf-link" in claro and "--gf-link" in bloco('[data-theme="dark"]')
FUNDOS = ("--gf-superficie", "--gf-cinza-claro", "--gf-info-fundo", "--gf-alerta-fundo",
          "--gf-sucesso-fundo", "--gf-primaria-clara")
for nome_tema, tokens in (("claro", claro), ("escuro", escuro)):
    for fundo in FUNDOS:
        razao = contraste(tokens["--gf-link"], tokens[fundo])
        assert razao >= 4.5, (nome_tema, fundo, tokens["--gf-link"], tokens[fundo], round(razao, 2))
ok("--gf-link definido nos dois temas, com contraste >= 4,5:1 sobre superfície, fundo e fundos de aviso")

# ===========================================================================
# 2) Regras de link: :where() inteiro, escopo main/.container, sem nav.
# ===========================================================================
regras = re.findall(r"([^{}]+)\{([^{}]*)\}", CSS_SEM_COMENTARIOS)
regras_link = [(sel.strip(), corpo) for sel, corpo in regras if "--gf-link" in corpo or re.search(r"\ba[:\s,)]", sel)]
seletores = [sel for sel, _ in regras_link if re.search(r"\ba\b", sel)]
assert seletores, "nenhuma regra de link encontrada"
for sel in seletores:
    sem_where = re.sub(r":where\((?:[^()]|\([^()]*\))*\)", "", sel)
    sem_where = re.sub(r":not\(\)", "", sem_where)
    assert sem_where.strip() == "", f"parte do seletor fora de :where() (especificidade > 0): {sel!r}"
    assert "main a" in sel and ".container a" in sel, sel
    assert "nav" not in sel, sel
    assert ".area-painel-tv" in sel, sel
cor = [sel for sel, corpo in regras_link if re.search(r"(?<!-)color:\s*var\(--gf-link\)", corpo)]
foco = [sel for sel in seletores if ":focus-visible" in sel]
hover = [sel for sel in seletores if ":hover" in sel]
assert len(cor) == 1 and foco and hover, (cor, foco, hover)
for sel, _ in regras:
    nivel_superior = re.sub(r":(?:where|not)\((?:[^()]|\([^()]*\))*\)", "", sel)
    for parte in nivel_superior.split(","):
        assert parte.strip() not in ("a", "a:link", "a:visited", "a:hover"), f"regra global solta: {sel.strip()!r}"
ok("Regras de cor/hover/foco com especificidade zero (tudo em :where()), escopo main/.container, sem nav")
ok("Nenhuma regra `a`/`a:visited` global solta - a cor de autor em `a` já cobre o estado visitado")

# ===========================================================================
# 3) Fora do escopo por construção.
# ===========================================================================
assert '<body class="area-painel-tv">' in ler("app/templates/painel/_layout.html")
for arq in glob.glob("app/templates/painel/*.html"):
    assert "<a " not in ler(arq), arq
for arq in glob.glob("app/templates/totem/*.html"):
    assert not re.search(r"<main\b|class=\"container\"", ler(arq)), arq
cupom = ler("app/templates/totem/senha_gerada.html")
inicio_cupom = cupom.index('class="cupom"')
assert "<a " not in cupom[inicio_cupom:cupom.index("</div>", inicio_cupom)]
impressao = ler("app/templates/relatorios/_layout_impressao.html")
assert "gestfila.css" not in re.sub(r"\{#.*?#\}", "", impressao, flags=re.S)
ok("Painel de TV excluído (.area-painel-tv, e sem <a>); totem sem main/.container; cupom sem <a>; PDF sem gestfila.css")

# ===========================================================================
# 4) .link-admin (10.71) removida.
# ===========================================================================
for arq in glob.glob("app/templates/**/*.html", recursive=True):
    assert "link-admin" not in ler(arq), arq
ok(".link-admin do 10.71 removida - a regra global é a única fonte da cor de link")

print(f"\n{len(passos)} verificações OK")
