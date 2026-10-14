"""Helper dos testes que conferem o TEXTO de um PDF gerado pelo Chromium.

O texto extraído pelo pypdf depende de onde o Chromium quebra cada célula da
tabela, e isso depende da fonte: no runner do GitHub (Linux) `system-ui`/
`Segoe UI` caem em DejaVu Sans, mais larga que a Segoe UI do Windows local,
e mais células quebram em duas linhas. A extração devolve a célula quebrada
ora com "\\n" ("[MAT]\\nColeta"), ora SEM separador nenhum
("Históricoparcial") - por isso normalizar espaços não basta. Achado ao
investigar a Action vermelha desde o 10.69 (BLUEPRINT, adendo 10.78).

Regras:
- PRESENÇA (qualquer texto) e AUSÊNCIA de frases/nomes: `texto_compacto_pdf`
  + `compactar()` no texto procurado - compara SEM nenhum espaço em branco
  dos dois lados. Na ausência de um nome, compactar torna a checagem mais
  rigorosa (acha o nome mesmo quebrado no meio de uma célula).
- AUSÊNCIA de token curto (ex.: código de senha "S2"): `texto_normalizado_pdf`
  + `re.search(r"\\bS2\\b", ...)`. No texto compactado, a junção de células
  vizinhas pode formar o token por acaso e a checagem falharia à toa.
"""
import io

from pypdf import PdfReader


def compactar(texto):
    """Remove todo espaço em branco (espaço, quebra de linha, tab)."""
    return "".join(texto.split())


def _texto_extraido(conteudo):
    return "".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(conteudo)).pages)


def texto_compacto_pdf(conteudo):
    """Texto de todas as páginas do PDF (bytes), sem espaço em branco."""
    return compactar(_texto_extraido(conteudo))


def texto_normalizado_pdf(conteudo):
    """Texto de todas as páginas do PDF (bytes), com cada sequência de espaços
    em branco reduzida a um espaço - para ausência de token curto com borda de
    palavra (ver regras no docstring do módulo)."""
    return " ".join(_texto_extraido(conteudo).split())
