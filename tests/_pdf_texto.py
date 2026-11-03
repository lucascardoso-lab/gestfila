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

Hífen opcional (adendo 10.83): os cabeçalhos do "Tempos por senha" têm
&shy; - onde o Chromium quebra a palavra, a extração devolve um hífen que
depende da fonte: ASCII "-" com Segoe UI (Windows local), U+2010 com DejaVu
Sans (Linux/produção) - "aten‐\\ndente"; onde não quebra, às vezes um espaço.
`compactar` remove também os hífens ("-", U+2010, U+00AD), dos DOIS lados
(o texto procurado passa pela mesma função), então a comparação continua
simétrica - nenhuma checagem de PDF depende de distinguir "-00:05" de
"00:05". ATENÇÃO: por isso `compactar`/`texto_compacto_pdf` NÃO servem para
verificar o sinal de uma duração negativa ("-00:05") em PDF - para isso, use
`texto_normalizado_pdf()` procurando o texto com o sinal.

Ligaduras (achado no 10.83): com DejaVu Sans o Chromium grava "fi" como a
ligadura U+FB01 ("Fila ﬁnal", "(ﬁnalização)") - `compactar` normaliza em
NFKC (ﬁ -> fi), também dos dois lados.
"""
import io
import unicodedata

from pypdf import PdfReader


HIFENS = str.maketrans("", "", "-‐­")


def compactar(texto):
    """Remove todo espaço em branco (espaço, quebra de linha, tab) e os hífens
    ("-", U+2010, U+00AD - ver a nota do &shy; no topo), com as ligaduras
    desfeitas (NFKC)."""
    return "".join(unicodedata.normalize("NFKC", texto).translate(HIFENS).split())


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
