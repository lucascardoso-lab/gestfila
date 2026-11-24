"""
Teste de regressão (adendo 10.87): "Tempos por senha" - em senha CONCLUÍDA, a
etapa de atendimento que não aconteceu (Atendimento atendente / executor /
Outros atendimentos) sai 00:00 na tela, no PDF, no CSV e no Excel, em vez de
vazio. Senha sem dado (não finalizada, cancelada, expirada, aguardando,
histórico parcial) continua vazia nas três colunas. O zero é só de
APRESENTAÇÃO (`zerar_atendimento_nao_aplicavel`, app/relatorios/routes.py):
resumo "N de M", quadro "Por fila" e os cartões/JSON do Dashboard (10.86) não
mudam, e a coluna "Outros" da tela continua condicional ao dado cru.

Relógio simulado em 09/10/2026 01:00 UTC (08/10 22:00 de Brasília). Minutos a
partir de T0 (08/10 09:00 de Brasília), logs no formato novo salvo HP:
  Matriz (MAT), locais Guichê (recepcao) e Coleta (coleta):
  R1  só recepção: espera 3, atendente 10 -> Executor e Outros 00:00
  C1  direto na coleta: espera 4, executor 15 -> Atendente e Outros 00:00
  RC  recepção -> coleta: espera 1, atendente 10, intermediária 1, executor 20
  NF  chamada, não finalizada (espera 2)
  CA  cancelada pelo encerramento automático (espera 6)
  EX  expirada (nunca chamada)
  AG  aguardando (nunca chamada)
  HP  histórico parcial (sem snapshot, com realocação; espera inicial 7)
  Outra unidade (OUT), local Triagem (tipo 'outro'):
  T1  só triagem: espera 5, outros 8 -> Atendente e Executor 00:00
Todas com nome_paciente - não pode aparecer em nenhuma saída (LGPD, 10.67).

Execução: python -m tests.test_regressao_tempos_zero_nao_se_aplica (a partir
da raiz do projeto, com o virtualenv ativado).
"""
import os

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

import html
import io
import logging
import re
import zipfile
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import openpyxl
import pandas as pd
from openpyxl.utils import get_column_letter

from app import create_app
from app.extensions import db
from app.models import AreaFila, Empresa, Fila, Local, LogAtendimento, Perfil, Senha, Unidade, Usuario
from app.relatorios.helpers import Filtros
from app.relatorios.queries import SITUACAO_CONCLUIDA, _resumo_tempos, tempos_por_senha
from app.relatorios.routes import ATENDIMENTO_ZERO_SE_NAO_OCORREU
from app.utils.tempo import janela_dia_clinica_em_utc
from tests._pdf_texto import compactar, texto_compacto_pdf

UTC = ZoneInfo("UTC")
SP = ZoneInfo("America/Sao_Paulo")
NOME = "Paciente Sigiloso"
T0 = datetime(2026, 10, 8, 12, 0)  # 09:00 em Brasília
app = create_app("testing")

passos = []


def ok(msg):
    passos.append(msg)
    print("OK:", msg)


def relogio():
    return patch("app.utils.tempo._instante_utc", return_value=datetime(2026, 10, 9, 1, 0, tzinfo=UTC))


with app.app_context():
    db.create_all()
    p_admin = Perfil(nome="admin")
    empresa = Empresa(nome="Clinica Zero")
    db.session.add_all([p_admin, empresa])
    db.session.commit()
    mat = Unidade(empresa_id=empresa.id, nome="Matriz", sigla="MAT")
    out = Unidade(empresa_id=empresa.id, nome="Outra", sigla="OUT")
    db.session.add_all([mat, out])
    db.session.commit()

    def fila(unidade, nome):
        f = Fila(empresa_id=empresa.id, unidade_id=unidade.id, nome=nome)
        db.session.add(f)
        db.session.commit()
        return f

    f_rec, f_col = fila(mat, "Recepção"), fila(mat, "Coleta")
    f_tri = fila(out, "Triagem")

    def local(unidade, nome, tipo):
        l = Local(empresa_id=empresa.id, unidade_id=unidade.id, nome=nome, nome_exibicao=nome,
                  identificador_admin=f"[{unidade.sigla}] {nome}", tipo=tipo)
        db.session.add(l)
        db.session.commit()
        return l

    l_gui = local(mat, "Guichê-01", "recepcao")
    l_col = local(mat, "Coleta-01", "coleta")
    l_tri = local(out, "Triagem 1", "outro")

    area = AreaFila(empresa_id=empresa.id, unidade_id=mat.id, nome="Laboratório")
    db.session.add(area)
    db.session.commit()
    f_rec.area_fila_id = area.id
    f_col.area_fila_id = area.id

    admin = Usuario(empresa_id=empresa.id, nome="admin_zero", login="admin_zero")
    admin.set_senha("senha123")
    admin.perfis = [p_admin]
    op = Usuario(empresa_id=empresa.id, nome="op", login="op_zero")
    op.set_senha("senha123")
    db.session.add_all([admin, op])
    db.session.commit()

    def senha(codigo, unidade, fila_final, status, local_final, eventos, snapshot=True):
        """`eventos`: (evento, minuto, fila, local) - fila/local do snapshot."""
        s = Senha(empresa_id=empresa.id, unidade_id=unidade.id, fila_id=fila_final.id, codigo=codigo, sequencial=1,
                  prioridade=False, status=status, criada_em=T0, local_id=local_final.id if local_final else None,
                  nome_paciente=NOME)
        db.session.add(s)
        db.session.commit()
        for evento, minuto, f, l in eventos:
            db.session.add(LogAtendimento(
                senha_id=s.id, evento=evento, timestamp=T0 + timedelta(minutes=minuto),
                usuario_id=None if evento in ("gerada", "expirada_aguardando") else op.id, unidade_id=unidade.id,
                fila_id=f.id if snapshot and f else None, local_id=l.id if snapshot and l else None,
            ))
        db.session.commit()

    senha("R1", mat, f_rec, "finalizada", l_gui, [
        ("gerada", 0, f_rec, None), ("chamada_senha", 3, f_rec, l_gui), ("fim_atendimento", 13, f_rec, l_gui)])
    senha("C1", mat, f_col, "finalizada", l_col, [
        ("gerada", 0, f_col, None), ("chamada_senha", 4, f_col, l_col), ("fim_atendimento", 19, f_col, l_col)])
    senha("RC", mat, f_col, "finalizada", l_col, [
        ("gerada", 0, f_rec, None), ("chamada_senha", 1, f_rec, l_gui), ("realocado", 11, f_rec, l_gui),
        ("chamada_senha", 12, f_col, l_col), ("fim_atendimento", 32, f_col, l_col)])
    senha("NF", mat, f_rec, "chamada", l_gui, [("gerada", 0, f_rec, None), ("chamada_senha", 2, f_rec, l_gui)])
    senha("CA", mat, f_rec, "cancelada", l_gui, [("gerada", 0, f_rec, None), ("chamada_senha", 6, f_rec, l_gui)])
    senha("EX", mat, f_rec, "cancelada", None, [("gerada", 0, f_rec, None), ("expirada_aguardando", 600, f_rec, None)])
    senha("AG", mat, f_rec, "aguardando", None, [("gerada", 0, f_rec, None)])
    senha("HP", mat, f_col, "finalizada", l_col, [
        ("gerada", 0, None, None), ("chamada_senha", 7, None, None), ("realocado", 8, None, None),
        ("chamada_senha", 9, None, None), ("fim_atendimento", 20, None, None)], snapshot=False)
    senha("T1", out, f_tri, "finalizada", l_tri, [
        ("gerada", 0, f_tri, None), ("chamada_senha", 5, f_tri, l_tri), ("fim_atendimento", 13, f_tri, l_tri)])

    ids = dict(empresa=empresa.id, mat=mat.id, out=out.id, area=area.id)
    INICIO, FIM = janela_dia_clinica_em_utc(datetime(2026, 10, 8, tzinfo=SP))

CONCLUIDAS = ("R1", "C1", "RC", "T1")
SEM_DADO = ("NF", "CA", "EX", "AG", "HP")
# Atendente, executor, outros (minutos) esperados na saída; None = vazio.
ESPERADO = {
    "R1": (10, 0, 0), "C1": (0, 15, 0), "RC": (10, 20, 0), "T1": (0, 0, 8),
    **{codigo: (None, None, None) for codigo in SEM_DADO},
}
CHAVES_ATD = ("atendimento_atendente", "atendimento_executor", "atendimento_outros")
ROTULOS_ATD = ("Atendimento atendente", "Atendimento executor", "Outros atendimentos")


def texto_mmss(minutos):
    return "" if minutos is None else f"{minutos:02d}:00"


# ===========================================================================
# 1) Consulta: tempos_por_senha() NÃO muda - continua None na etapa não ocorrida.
# ===========================================================================
def filtros(unidade_id=None):
    return Filtros(empresa_id=ids["empresa"], unidades_permitidas=None, unidade_id=unidade_id, fila_id=None,
                   operador_id=None, data_inicio=INICIO, data_fim_exclusivo=FIM,
                   data_inicio_str="2026-10-08", data_fim_str="2026-10-08")


with app.app_context():
    CRU, _ = tempos_por_senha(filtros())
    CRU_MAT, _ = tempos_por_senha(filtros(ids["mat"]))
L = {l["codigo"]: l for l in CRU}
assert set(L) == set(CONCLUIDAS + SEM_DADO), sorted(L)
for codigo in CONCLUIDAS:
    assert L[codigo]["situacao"] == SITUACAO_CONCLUIDA, codigo
for codigo in SEM_DADO:
    assert L[codigo]["situacao"] != SITUACAO_CONCLUIDA, codigo
assert L["R1"]["atendimento_executor_segundos"] is None and L["R1"]["atendimento_outros_segundos"] is None
assert L["C1"]["atendimento_atendente_segundos"] is None and L["T1"]["atendimento_atendente_segundos"] is None
assert ATENDIMENTO_ZERO_SE_NAO_OCORREU == tuple(f"{c}_segundos" for c in CHAVES_ATD)
ok("Consulta sem mudança: tempos_por_senha() devolve None na etapa não ocorrida (o zero é só de apresentação); "
   "situações do cenário conferidas")

# ===========================================================================
# 2) Formatos: tela, PDF, CSV e Excel. Cada verificação confere as concluídas
#    (00:00) E as sem dado (vazio) - é o que as provas por mutação exercitam.
# ===========================================================================
c = app.test_client()
assert c.post("/login", data={"login": "admin_zero", "senha": "senha123"}, follow_redirects=True).status_code == 200
URL = "/relatorios/tempos-por-senha?data_inicio=2026-10-08&data_fim=2026-10-08"


class _GuardarLog(logging.Handler):
    """Guarda os logs do app: o 302 de um PDF é o caminho do FalhaGeracaoPdf em
    `_exportar_relatorio`, que registra a exceção com `logger.exception`."""
    def __init__(self):
        super().__init__(level=logging.ERROR)
        self.registros = []

    def emit(self, record):
        self.registros.append(self.format(record))


LOG_APP = _GuardarLog()
LOG_APP.setFormatter(logging.Formatter("%(message)s\n%(exc_text)s"))
app.logger.addHandler(LOG_APP)


def get(url):
    LOG_APP.registros.clear()
    with relogio():
        resp = c.get(url)
    # RuntimeError, e não AssertionError: na prova por mutação (seção 6) uma
    # resposta que nem chegou (ex.: PDF que falhou e redirecionou) não pode
    # contar como "a mutação foi pega". Leva a causa: flash e log da falha.
    if resp.status_code != 200:
        with c.session_transaction() as sessao:
            flashes = sessao.get("_flashes", [])
        raise RuntimeError(f"{url} -> {resp.status_code}; flash={flashes}; log={LOG_APP.registros}")
    resp.direct_passthrough = False
    return resp


def limpar(fragmento):
    return re.sub(r"<[^>]+>", "", fragmento).replace("­", "").strip()


def tabela_tela(pagina):
    """{codigo: {cabeçalho: texto da célula}} da tabela por senha da tela."""
    tabela = re.search(r'<table class="tabela-larga">(.*?)</table>', pagina, re.S).group(1)
    cab = [limpar(th) for th in re.findall(r"<th[^>]*>.*?</th>", re.search(r"<thead>(.*?)</thead>", tabela, re.S).group(1), re.S)]
    linhas = {}
    for tr in re.findall(r"<tr>(.*?)</tr>", re.search(r"<tbody>(.*?)</tbody>", tabela, re.S).group(1), re.S):
        celulas = [limpar(td) for td in re.findall(r"<td>(.*?)</td>", tr, re.S)]
        linhas[celulas[0]] = dict(zip(cab, celulas))
    return cab, linhas


def tela(extra=""):
    return html.unescape(get(URL + extra).get_data(as_text=True))


def verificar_tela():
    _cab, linhas = tabela_tela(tela())
    for codigo, esperado in ESPERADO.items():
        for rotulo, minutos in zip(ROTULOS_ATD, esperado):
            obtido = linhas[codigo][f"{rotulo} (mm:ss)"]
            assert obtido == (texto_mmss(minutos) or "—"), ("tela", codigo, rotulo, obtido)


def verificar_pdf():
    """Sequência das 6 colunas de tempo do PDF (Espera inicial, intermediária,
    atendente, executor, outros, permanência) no texto compactado - mesma
    ordem da linha, sem depender de onde a célula quebra (10.78)."""
    pdf = texto_compacto_pdf(get(URL + "&formato=pdf").get_data())
    assert compactar("03:00 00:00 10:00 00:00 00:00 13:00") in pdf, "PDF R1"
    assert compactar("04:00 00:00 00:00 15:00 00:00 19:00") in pdf, "PDF C1"
    assert compactar("01:00 01:00 10:00 20:00 00:00 32:00") in pdf, "PDF RC"
    assert compactar("05:00 00:00 00:00 00:00 08:00 13:00") in pdf, "PDF T1"
    # Sem dado: as 5 colunas depois da espera inicial + Etapas = "—".
    for espera, codigo in (("02:00", "NF"), ("06:00", "CA"), ("07:00", "HP")):
        assert compactar(f"{espera} " + "— " * 6) in pdf, f"PDF {codigo}"
    # Nunca chamadas: as 6 colunas de tempo + Etapas, Operador e Local = "—".
    assert compactar("— " * 9 + "Expirada (nunca chamada)") in pdf, "PDF EX"
    assert compactar("— " * 9 + "Aguardando (nunca chamada)") in pdf, "PDF AG"
    assert "—00:00" not in pdf, "PDF: 00:00 depois de um vazio = zero numa senha sem dado"
    assert compactar(NOME) not in pdf and "Sigiloso" not in pdf
    return pdf


def csv_por_senha():
    bruto = get(URL + "&formato=csv").get_data().decode("utf-8-sig")
    assert NOME not in bruto
    return pd.read_csv(io.StringIO(bruto), sep=";", decimal=",", dtype={f"{r} (mm:ss)": str for r in ROTULOS_ATD}
                       ).set_index("Senha")


def verificar_csv():
    df = csv_por_senha()
    for codigo, esperado in ESPERADO.items():
        for rotulo, minutos in zip(ROTULOS_ATD, esperado):
            em_min, em_texto = df.loc[codigo][f"{rotulo} (min)"], df.loc[codigo][f"{rotulo} (mm:ss)"]
            if minutos is None:
                assert pd.isna(em_min) and pd.isna(em_texto), ("csv", codigo, rotulo, em_min, em_texto)
            else:
                assert em_min == float(minutos) and em_texto == texto_mmss(minutos), ("csv", codigo, rotulo, em_min, em_texto)


def verificar_csv_bruto():
    """O par "(min)" = "0,00" e "(mm:ss)" = "00:00" literalmente, no texto do
    arquivo (R1: Executor e Outros)."""
    bruto = get(URL + "&formato=csv").get_data().decode("utf-8-sig")
    linha_r1 = next(l for l in bruto.splitlines() if l.startswith("R1;"))
    # ... Atendente 10,00;10:00; Executor 0,00;00:00; Outros 0,00;00:00; Atendimento total 10,00;10:00 ...
    assert ";10,00;10:00;0,00;00:00;0,00;00:00;10,00;10:00;" in linha_r1, linha_r1


def verificar_excel():
    conteudo = get(URL + "&formato=xlsx").get_data()
    livro = openpyxl.load_workbook(io.BytesIO(conteudo))
    aba = livro["Por senha"]
    with zipfile.ZipFile(io.BytesIO(conteudo)) as z:
        xml = z.read(f"xl/worksheets/sheet{livro.sheetnames.index('Por senha') + 1}.xml").decode("utf-8")
    cab = [x.value for x in aba[1]]
    linha_de = {r[0].value: r[0].row for r in aba.iter_rows(min_row=2)}
    for codigo, esperado in ESPERADO.items():
        for rotulo, minutos in zip(ROTULOS_ATD, esperado):
            coluna = f"{rotulo} (h:mm:ss)"
            ref = f"{get_column_letter(cab.index(coluna) + 1)}{linha_de[codigo]}"
            cel = aba[ref]
            if minutos is None:
                assert cel.value is None, ("xlsx", codigo, rotulo, cel.value)
                continue
            # Célula NUMÉRICA no XML cru (sem t= ou t="n"; texto seria t="s"/"str").
            m = re.search(rf'<c r="{ref}"([^>]*)><v>([^<]*)</v></c>', xml)
            assert m, ("xlsx sem valor", codigo, rotulo)
            tipo = re.search(r' t="([^"]*)"', m.group(1))
            assert tipo is None or tipo.group(1) == "n", ("xlsx não numérico", codigo, rotulo, m.group(1))
            assert abs(float(m.group(2)) - minutos * 60 / 86400) < 1e-12, ("xlsx", codigo, rotulo, m.group(2))
            assert cel.value == timedelta(minutes=minutos) and cel.number_format == "[h]:mm:ss", (
                "xlsx", codigo, rotulo, cel.value, cel.number_format)
    for nome_aba in livro.sheetnames:
        for linha in livro[nome_aba].iter_rows(values_only=True):
            assert NOME not in [str(v) for v in linha], nome_aba


FORMATOS = {"tela": verificar_tela, "PDF": verificar_pdf, "CSV": verificar_csv, "Excel": verificar_excel}

verificar_tela()
ok("Tela: concluída só recepção -> Executor e Outros 00:00; direto na coleta -> Atendente 00:00; T1 (só Outros) -> "
   "Atendente e Executor 00:00; NF, CA, EX, AG e HP com as 3 colunas '—'")
# UM PDF só, com todos os cenários (as 9 senhas das 2 unidades) - reaproveitado
# para as notas na seção 4. Além dele, só os 2 da prova por mutação (seção 6),
# que precisam de PDFs próprios porque a mutação muda o conteúdo.
PDF_TODOS = verificar_pdf()
ok("PDF real (Chromium): mesmas linhas - 00:00 nas concluídas, '—' nas sem dado, nenhum 00:00 depois de vazio, sem nome")
verificar_csv()
verificar_csv_bruto()
ok("CSV por senha: par (min) = 0,00 e (mm:ss) = 00:00 na etapa não ocorrida da concluída; vazio nas sem dado; sem nome")
verificar_excel()
ok("Excel (aba Por senha): célula NUMÉRICA de valor 0 com formato [h]:mm:ss na etapa não ocorrida; vazia nas sem dado")

# Visão por etapa NÃO muda: só lista etapas que existiram (nenhuma de duração 0).
etapas_csv = pd.read_csv(io.StringIO(get(URL + "&formato=csv_etapas").get_data().decode("utf-8-sig")), sep=";",
                         decimal=",")
r1 = etapas_csv[etapas_csv["Senha"] == "R1"]
assert list(r1["Classificação"].fillna("")) == ["", "Atendente"], list(r1["Classificação"])
assert (etapas_csv["Duração (min)"] > 0).all() and "Executor" not in list(etapas_csv[etapas_csv["Senha"] == "R1"]["Classificação"])
livro = openpyxl.load_workbook(io.BytesIO(get(URL + "&formato=xlsx").get_data()))
assert livro["Por etapa"].max_row - 1 == len(etapas_csv)
ok("Visão por etapa (CSV e aba do Excel) sem mudança: só as etapas que existiram, sem linha de 00:00")

# ===========================================================================
# 3) Coerência: Atendente + Executor + Outros = Atendimento total, com os zeros.
# ===========================================================================
df = csv_por_senha()
for codigo in CONCLUIDAS:
    linha = df.loc[codigo]
    partes = [linha[f"{r} (min)"] for r in ROTULOS_ATD]
    assert not any(pd.isna(p) for p in partes), codigo
    assert abs(sum(partes) - linha["Atendimento total (min)"]) < 1e-6, (codigo, partes, linha["Atendimento total (min)"])
for codigo in SEM_DADO:
    assert pd.isna(df.loc[codigo]["Atendimento total (min)"]), codigo
ok("Coerência nas concluídas: Atendente + Executor + Outros = Atendimento total, agora sem nenhum vazio na soma")

# ===========================================================================
# 4) Resumo, "Por fila" e Dashboard idênticos ao comportamento anterior.
# ===========================================================================
def cartao(pagina, rotulo):
    m = re.search(rf"<strong>{rotulo}</strong>\s*<span>Média: ([^<]*)</span>\s*<span>Mediana: ([^<]*)</span>\s*"
                  rf"<span>(\d+) de (\d+) senhas</span>", pagina)
    assert m, rotulo
    return m.groups()


tela_mat = tela(f"&unidade_id={ids['mat']}")
# Só quem passou pela etapa entra: atendente R1 e RC (10 e 10); executor C1 e RC
# (15 e 20); com o zero no resumo seriam "3 de 8" (06:40) e "3 de 8" (11:40).
assert cartao(tela_mat, "Atendimento atendente") == ("10:00", "10:00", "2", "8"), cartao(tela_mat, "Atendimento atendente")
assert cartao(tela_mat, "Atendimento executor") == ("17:30", "17:30", "2", "8"), cartao(tela_mat, "Atendimento executor")
assert cartao(tela_mat, "Atendimento total") == ("18:20", "15:00", "3", "8"), cartao(tela_mat, "Atendimento total")
resumo_cru = _resumo_tempos(CRU_MAT)
for chave, rotulo in (("atendimento_atendente", "Atendimento atendente"), ("atendimento_executor", "Atendimento executor")):
    assert int(cartao(tela_mat, rotulo)[2]) == resumo_cru[chave]["quantidade"], rotulo
ok("Resumo do topo sem mudança: média, mediana e 'N de M senhas' só com quem passou pela etapa (2 de 8)")

texto_mat = " ".join(tela_mat.split())  # as notas quebram linha no template
assert "As médias do resumo consideram só as senhas que passaram pela etapa (N de M)." in texto_mat
assert "No Excel, a MÉDIA da coluna inclui os 00:00 e por isso pode ser menor." in texto_mat
assert ("Em senha concluída, a etapa que não aconteceu aparece como 00:00 (ex.: sem coleta, Atendimento executor = "
        "00:00). Senhas não concluídas ficam em branco.") in texto_mat
assert "Atendimento executor fica vazio - está correto" not in texto_mat
assert 'title="Tempo na coleta, do início à finalização do procedimento. 00:00 em senha concluída que não passou pela coleta."' in tela_mat
pdf_notas = PDF_TODOS
for nota in ("Em senha concluída, a etapa que não aconteceu aparece como 00:00",
             "As médias do resumo consideram só as senhas que passaram pela etapa (N de M).",
             "No Excel, a MÉDIA da coluna inclui os 00:00 e por isso pode ser menor."):
    assert compactar(nota) in pdf_notas, nota
ok("Notas novas na tela e no PDF (00:00 em concluída / média do resumo x média do Excel) e dicas das 3 colunas")

tela_area = tela(f"&area_fila_id={ids['area']}")
por_fila = re.search(r'<table class="tabela-por-fila">(.*?)</table>', tela_area, re.S).group(1)
linhas_fila = [[limpar(td) for td in re.findall(r"<td>(.*?)</td>", tr, re.S)]
               for tr in re.findall(r"<tr>(.*?)</tr>", por_fila, re.S) if "<td>" in tr]
# Recepção: R1, NF, CA, EX, AG (permanência só R1; executor nenhum -> "—", e não 00:00).
# Coleta: C1, RC, HP (permanência 19 e 32; executor 15 e 20).
assert linhas_fila == [["[MAT] Coleta", "3", "25:30", "25:30", "17:30", "17:30"],
                       ["[MAT] Recepção", "5", "13:00", "13:00", "—", "—"]], linhas_fila
ok("Quadro 'Por fila' sem mudança: Atendimento executor da Recepção continua '—' (nenhuma senha passou pela coleta)")

with relogio():
    dados = c.get(f"/api/dashboard/kpis?unidade_id={ids['mat']}").get_json()
assert dados["tempos"]["atendimento_executor"] == {
    "quantidade": 2, "media_segundos": 1050.0, "mediana_segundos": 1050.0, "media_texto": "17:30", "mediana_texto": "17:30",
}, dados["tempos"]["atendimento_executor"]
for chave, resumo in dados["tempos"].items():
    assert resumo["quantidade"] == resumo_cru[chave]["quantidade"] and resumo["media_segundos"] == resumo_cru[chave]["media"], chave
with relogio():
    pagina_dash = html.unescape(c.get(f"/dashboard?unidade_id={ids['mat']}").get_data(as_text=True))
assert "17:30" in pagina_dash
ok("Dashboard (10.86): JSON e cartão de Atendimento executor iguais ao resumo cru (2 senhas, 17:30) - sem os zeros")

# ===========================================================================
# 5) Coluna "Outros" da tela: condição avaliada sobre o dado CRU.
# ===========================================================================
cab_mat, linhas_mat = tabela_tela(tela_mat)
assert "Outros atendimentos (mm:ss)" not in cab_mat, cab_mat
assert linhas_mat["R1"]["Atendimento executor (mm:ss)"] == "00:00"  # há zeros, e mesmo assim a coluna não aparece
cab_todas, _ = tabela_tela(tela())
assert "Outros atendimentos (mm:ss)" in cab_todas, cab_todas
cab_out, linhas_out = tabela_tela(tela(f"&unidade_id={ids['out']}"))
assert "Outros atendimentos (mm:ss)" in cab_out and linhas_out["T1"]["Outros atendimentos (mm:ss)"] == "08:00"
ok("Coluna Outros na tela: ausente na Matriz (só zeros de apresentação, nenhum Outros real), presente quando há (T1)")

# ===========================================================================
# 6) Prova por mutação: (a) helper neutralizado -> os 4 formatos falham;
#    (b) zero aplicado também às não concluídas -> os 4 formatos falham.
# ===========================================================================
def zera_tudo(linha):
    return {**linha, **{chave: 0.0 for chave in ATENDIMENTO_ZERO_SE_NAO_OCORREU if linha[chave] is None}}


for nome_mutante, mutante in (("helper neutralizado", lambda linha: linha), ("zero também nas não concluídas", zera_tudo)):
    with patch("app.relatorios.routes.zerar_atendimento_nao_aplicavel", new=mutante):
        for formato, verificar in FORMATOS.items():
            try:
                verificar()
            except AssertionError:
                continue
            raise AssertionError(f"mutação '{nome_mutante}' não foi pega no formato {formato}")
# E sem mutação, tudo volta a passar (o PDF sem mutação já foi conferido na
# seção 2 - não gera um 4º PDF).
for formato, verificar in FORMATOS.items():
    if formato != "PDF":
        verificar()
ok("Prova por mutação: com o helper neutralizado e com o zero também nas não concluídas, tela, PDF, CSV e Excel falham")

print(f"\n{len(passos)} verificações OK")
