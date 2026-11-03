"""
Teste de regressão (adendo 10.83): "Tempos por senha" por etapa - Atendimento
atendente / executor / Outros atendimentos (pelo Local.tipo do local do
segmento), Espera intermediária e Permanência total (emissão -> finalização).

Relógio simulado em 09/10/2026 01:00 UTC (08/10 22:00 de Brasília); logs no
formato novo (snapshot do 10.62) salvo onde indicado. Minutos a partir de T0:
  G1  exemplo da gestão: espera 5, atendente 25, coleta 15, sem espera entre
      as etapas -> permanência 45
  G2  o mesmo com 20 de espera entre recepção e coleta -> permanência 65,
      espera intermediária 20
  R1  só recepção; C1 direto na coleta; RR recepção -> recepção (soma no atendente)
  BS  Banco de Sangue: guichê -> triagem (tipo 'outro') -> coleta, as 3 colunas
  ST  local sem tipo -> Outros atendimentos
  CE  local "Coleta-A 01" cadastrado como recepcao -> Atendente; trocando o
      tipo para coleta, a MESMA senha vai para Executor (segue o cadastro)
  CR  local de coleta atendendo fila de recepção -> Executor
  AG  com "Aguardar": permanência maior que a soma das colunas
  NF  não finalizada -> permanência vazia
  HR  histórico (sem snapshot) com realocação -> "Histórico parcial", vazio
  HS  histórico sem realocação -> classificado pelo Senha.local_id
  A1  unidade "Anápolis" (só recepção): sem Outros na tela, Executor vazio
Todas com nome_paciente - não pode aparecer em nenhuma saída (LGPD, 10.67).

Execução: python -m tests.test_regressao_tempos_por_etapa (a partir da raiz
do projeto, com o virtualenv ativado).
"""
import os

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

import html
import io
import re
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import openpyxl
import pandas as pd

from app import create_app
from app.extensions import db
from app.models import AreaFila, Empresa, Fila, Local, LogAtendimento, Perfil, Senha, Unidade, Usuario
from app.relatorios.helpers import Filtros
from app.relatorios.queries import SITUACAO_HISTORICO_PARCIAL, tempo_atendimento, tempos_por_senha
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
    empresa = Empresa(nome="Clinica Etapas")
    db.session.add_all([p_admin, empresa])
    db.session.commit()
    mat = Unidade(empresa_id=empresa.id, nome="Matriz", sigla="MAT")
    ana = Unidade(empresa_id=empresa.id, nome="Anapolis", sigla="ANA")
    db.session.add_all([mat, ana])
    db.session.commit()

    def fila(unidade, nome):
        f = Fila(empresa_id=empresa.id, unidade_id=unidade.id, nome=nome)
        db.session.add(f)
        db.session.commit()
        return f

    f_rec, f_rec2, f_tri, f_col = (fila(mat, n) for n in ("Recepção", "Recepção 2", "Triagem", "Coleta"))
    f_ana = fila(ana, "Atendimento Geral")

    def local(unidade, nome, tipo):
        l = Local(empresa_id=empresa.id, unidade_id=unidade.id, nome=nome, nome_exibicao=nome,
                  identificador_admin=f"[{unidade.sigla}] {nome}", tipo=tipo)
        db.session.add(l)
        db.session.commit()
        return l

    l_gui = local(mat, "Guichê-01", "recepcao")
    l_col = local(mat, "Coleta-01", "coleta")
    l_tri = local(mat, "Triagem 1", "outro")
    l_sem = local(mat, "Posto sem tipo", None)
    l_errado = local(mat, "Coleta-A 01", "recepcao")  # coleta cadastrada como recepcao
    l_ana = local(ana, "Guichê-02", "recepcao")

    area = AreaFila(empresa_id=empresa.id, unidade_id=mat.id, nome="Laboratório")
    db.session.add(area)
    db.session.commit()
    f_col.area_fila_id = area.id

    admin = Usuario(empresa_id=empresa.id, nome="admin_etapas", login="admin_etapas")
    admin.set_senha("senha123")
    admin.perfis = [p_admin]
    op = Usuario(empresa_id=empresa.id, nome="op", login="op_etapas")
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
                usuario_id=None if evento == "gerada" else op.id, unidade_id=unidade.id,
                fila_id=f.id if snapshot and f else None, local_id=l.id if snapshot and l else None,
            ))
        db.session.commit()

    senha("G1", mat, f_col, "finalizada", l_col, [
        ("gerada", 0, f_rec, None), ("chamada_senha", 5, f_rec, l_gui), ("realocado", 30, f_rec, l_gui),
        ("chamada_senha", 30, f_col, l_col), ("fim_atendimento", 45, f_col, l_col)])
    senha("G2", mat, f_col, "finalizada", l_col, [
        ("gerada", 0, f_rec, None), ("chamada_senha", 5, f_rec, l_gui), ("realocado", 30, f_rec, l_gui),
        ("chamada_senha", 50, f_col, l_col), ("fim_atendimento", 65, f_col, l_col)])
    senha("R1", mat, f_rec, "finalizada", l_gui, [
        ("gerada", 0, f_rec, None), ("chamada_senha", 3, f_rec, l_gui), ("fim_atendimento", 13, f_rec, l_gui)])
    senha("C1", mat, f_col, "finalizada", l_col, [
        ("gerada", 0, f_col, None), ("chamada_senha", 4, f_col, l_col), ("fim_atendimento", 19, f_col, l_col)])
    senha("RR", mat, f_rec2, "finalizada", l_gui, [
        ("gerada", 0, f_rec, None), ("chamada_senha", 2, f_rec, l_gui), ("realocado", 7, f_rec, l_gui),
        ("chamada_senha", 9, f_rec2, l_gui), ("fim_atendimento", 15, f_rec2, l_gui)])
    senha("BS", mat, f_col, "finalizada", l_col, [
        ("gerada", 0, f_rec, None), ("chamada_senha", 2, f_rec, l_gui), ("realocado", 10, f_rec, l_gui),
        ("chamada_senha", 12, f_tri, l_tri), ("realocado", 20, f_tri, l_tri),
        ("chamada_senha", 25, f_col, l_col), ("fim_atendimento", 40, f_col, l_col)])
    senha("ST", mat, f_rec, "finalizada", l_sem, [
        ("gerada", 0, f_rec, None), ("chamada_senha", 1, f_rec, l_sem), ("fim_atendimento", 6, f_rec, l_sem)])
    senha("CE", mat, f_col, "finalizada", l_errado, [
        ("gerada", 0, f_col, None), ("chamada_senha", 2, f_col, l_errado), ("fim_atendimento", 12, f_col, l_errado)])
    senha("CR", mat, f_rec, "finalizada", l_col, [
        ("gerada", 0, f_rec, None), ("chamada_senha", 3, f_rec, l_col), ("fim_atendimento", 8, f_rec, l_col)])
    senha("AG", mat, f_rec, "finalizada", l_gui, [
        ("gerada", 0, f_rec, None), ("chamada_senha", 2, f_rec, l_gui), ("voltou_aguardar", 6, f_rec, l_gui),
        ("chamada_senha", 10, f_rec, l_gui), ("fim_atendimento", 14, f_rec, l_gui)])
    senha("NF", mat, f_rec, "chamada", l_gui, [
        ("gerada", 0, f_rec, None), ("chamada_senha", 2, f_rec, l_gui)])
    senha("HR", mat, f_col, "finalizada", l_col, [
        ("gerada", 0, None, None), ("chamada_senha", 6, None, None), ("realocado", 7, None, None),
        ("chamada_senha", 8, None, None), ("fim_atendimento", 20, None, None)], snapshot=False)
    senha("HS", mat, f_rec, "finalizada", l_gui, [
        ("gerada", 0, None, None), ("chamada_senha", 4, None, None), ("fim_atendimento", 10, None, None)],
          snapshot=False)
    senha("A1", ana, f_ana, "finalizada", l_ana, [
        ("gerada", 0, f_ana, None), ("chamada_senha", 3, f_ana, l_ana), ("fim_atendimento", 11, f_ana, l_ana)])

    ids = dict(empresa=empresa.id, mat=mat.id, ana=ana.id, area=area.id, l_errado=l_errado.id)
    INICIO, FIM = janela_dia_clinica_em_utc(datetime(2026, 10, 8, tzinfo=SP))


def filtros(unidade_id=None):
    return Filtros(empresa_id=ids["empresa"], unidades_permitidas=None, unidade_id=unidade_id, fila_id=None,
                   operador_id=None, data_inicio=INICIO, data_fim_exclusivo=FIM,
                   data_inicio_str="2026-10-08", data_fim_str="2026-10-08")


CHAVES = ("espera_inicial", "espera_intermediaria", "espera_total", "atendimento_atendente",
          "atendimento_executor", "atendimento_outros", "atendimento_total", "permanencia_total")


def consultar(unidade_id=None):
    with app.app_context():
        linhas, etapas = tempos_por_senha(filtros(unidade_id))
    return {l["codigo"]: l for l in linhas}, etapas


def minutos(linha):
    """Os 8 tempos da senha em minutos (None = vazio)."""
    return tuple(None if linha[f"{c}_segundos"] is None else linha[f"{c}_segundos"] / 60 for c in CHAVES)


L, ETAPAS = consultar()

# ===========================================================================
# 1) Consulta - (espera ini, interm, total, atendente, executor, outros, atend total, permanência)
# ===========================================================================
assert minutos(L["G1"]) == (5, 0, 5, 25, 15, None, 40, 45), minutos(L["G1"])
ok("Exemplo da gestão: espera 5 + atendente 25 + coleta 15, sem espera entre as etapas = permanência 45")
assert minutos(L["G2"]) == (5, 20, 25, 25, 15, None, 40, 65), minutos(L["G2"])
ok("Com 20 min entre recepção e coleta: espera intermediária 20, permanência 65")
assert minutos(L["R1"]) == (3, 0, 3, 10, None, None, 10, 13), minutos(L["R1"])
assert minutos(L["C1"]) == (4, 0, 4, None, 15, None, 15, 19), minutos(L["C1"])
ok("Só recepção: Executor vazio; direto na coleta: Atendente vazio (nunca 0 - 'não se aplica'); espera inicial até a "
   "1ª chamada; espera intermediária 00:00 (concluída sem espera entre etapas: o dado existe e é zero)")
assert minutos(L["RR"]) == (2, 2, 4, 11, None, None, 11, 15) and L["RR"]["etapas"] == 2, minutos(L["RR"])
ok("Recepção -> recepção: os dois segmentos somados no Atendente (5 + 6 = 11), 2 etapas")
assert minutos(L["BS"]) == (2, 7, 9, 8, 15, 8, 31, 40) and L["BS"]["etapas"] == 3, minutos(L["BS"])
ok("Banco de Sangue (guichê -> triagem 'outro' -> coleta): Atendente 8 + Executor 15 + Outros 8 = 31; permanência 40")
assert minutos(L["ST"]) == (1, 0, 1, None, None, 5, 5, 6), minutos(L["ST"])
ok("Local sem tipo: o segmento vai para Outros atendimentos")
assert minutos(L["CE"])[3:5] == (10, None), minutos(L["CE"])
assert minutos(L["CR"])[3:5] == (None, 5), minutos(L["CR"])
ok("'Coleta-A 01' cadastrado como recepcao -> Atendente (segue o cadastro); coleta atendendo fila de recepção -> Executor")
assert minutos(L["AG"]) == (2, 4, 6, 4, None, None, 4, 14), minutos(L["AG"])
ok("Com 'Aguardar': permanência 14 > espera 6 + atendimento 4 (o trecho chamada -> Aguardar não é medido, seção 11)")
assert minutos(L["NF"])[1] is None and minutos(L["NF"])[3:] == (None, None, None, None, None), minutos(L["NF"])
ok("Não finalizada: espera intermediária, atendimento e permanência vazios")
assert L["HR"]["situacao"] == SITUACAO_HISTORICO_PARCIAL and minutos(L["HR"])[1:] == (None,) * 7, minutos(L["HR"])
assert minutos(L["HS"]) == (4, 0, 4, 6, None, None, 6, 10), minutos(L["HS"])
ok("Histórico: com realocação 'Histórico parcial' e colunas novas vazias; sem realocação classificado pelo Senha.local_id")

# Coerência por senha (item 10).
for codigo, l in L.items():
    if l["atendimento_total_segundos"] is not None:
        partes = [l[f"atendimento_{c}_segundos"] or 0 for c in ("atendente", "executor", "outros")]
        assert abs(sum(partes) - l["atendimento_total_segundos"]) < 1e-6, codigo
    if l["permanencia_total_segundos"] is not None and codigo != "AG":
        assert abs(l["espera_inicial_segundos"] + (l["espera_intermediaria_segundos"] or 0)
                   - l["espera_total_segundos"]) < 1e-6, codigo
        assert abs(l["espera_total_segundos"] + l["atendimento_total_segundos"]
                   - l["permanencia_total_segundos"]) < 1e-6, codigo
ok("Coerência: Atendente + Executor + Outros = Atendimento total; sem 'Aguardar', inicial + intermediária = espera total "
   "e espera total + atendimento total = permanência")

# Visão por etapa: só rotula os MESMOS segmentos dos agregados.
classes_bs = [(e["tipo"], e["classe"]) for e in ETAPAS if e["codigo"] == "BS"]
assert classes_bs == [("espera", None), ("repasse", "atendente"), ("espera", None), ("repasse", "outros"),
                      ("espera", None), ("conclusao", "executor")], classes_bs
with app.app_context():
    agregado = tempo_atendimento(filtros(ids["mat"]))
etapas_mat = [e for e in ETAPAS if e["unidade_id"] == ids["mat"] and e["tipo"] != "espera"]
assert len(etapas_mat) == sum(r["quantidade"] for r in agregado)
assert abs(sum(e["segundos"] for e in etapas_mat) - sum(r["quantidade"] * r["tempo_medio_segundos"] for r in agregado)) < 1
ok("Por etapa: classificação de cada etapa (BS: Atendente, Outros, Executor) e os mesmos segmentos do Tempo de atendimento")

# Corrigir o cadastro reclassifica o passado (Local.tipo ATUAL, sem snapshot).
with app.app_context():
    db.session.get(Local, ids["l_errado"]).tipo = "coleta"
    db.session.commit()
L2, _ = consultar()
assert minutos(L2["CE"])[3:5] == (None, 10), minutos(L2["CE"])
with app.app_context():
    db.session.get(Local, ids["l_errado"]).tipo = "recepcao"
    db.session.commit()
ok("Trocando 'Coleta-A 01' para coleta no cadastro, a MESMA senha passa de Atendente para Executor")

# ===========================================================================
# 2) Tela
# ===========================================================================
c = app.test_client()
assert c.post("/login", data={"login": "admin_etapas", "senha": "senha123"}, follow_redirects=True).status_code == 200
URL = "/relatorios/tempos-por-senha?data_inicio=2026-10-08&data_fim=2026-10-08"


def get(url):
    with relogio():
        resp = c.get(url)
    assert resp.status_code == 200, (url, resp.status_code)
    return resp


def cabecalhos(pagina):
    """Texto visível dos <th> da tabela por senha, sem o hífen opcional (&shy;)."""
    tabela = re.search(r'<table class="tabela-larga">(.*?)</table>', pagina, re.S).group(1)
    return [re.sub(r"<[^>]+>", "", th).replace("­", "") for th in re.findall(r"<th[^>]*>.*?</th>", re.search(r"<thead>(.*?)</thead>", tabela, re.S).group(1))]


tela = html.unescape(get(URL).get_data(as_text=True))
assert cabecalhos(tela) == [
    "Senha", "Emissão", "Fila inicial", "Fila final", "Área", "Espera inicial (mm:ss)", "Espera intermediária (mm:ss)",
    "Atendimento atendente (mm:ss)", "Atendimento executor (mm:ss)", "Outros atendimentos (mm:ss)",
    "Permanência total (mm:ss)", "Etapas de atendimento", "Operador (finalização)", "Local (finalização)", "Situação",
], cabecalhos(tela)
assert 'title="Tempo com o atendente: cadastro e inclusão dos exames."' in tela
assert 'title="Tempo na coleta, do início à finalização do procedimento."' in tela
assert "A permanência é o tempo real na clínica, da emissão da senha à finalização." in tela
assert "Em unidades sem etapa de coleta no sistema, Atendimento executor fica vazio" in tela
assert NOME not in tela
ok("Tela: 15 colunas (sem Unidade, Espera total e Atendimento total; com Outros, que tem valor), dicas, notas, sem nome")

for titulo in ("Espera intermediária", "Atendimento atendente", "Atendimento executor", "Permanência total"):
    assert f"<strong>{titulo}</strong>" in tela, titulo
assert re.search(r"<strong>Permanência total</strong>\s*<span>Média: [^<]+</span>\s*<span>Mediana: [^<]+</span>\s*"
                 r"<span>12 de 14 senhas</span>", tela), "cartão de permanência"  # NF e HR ficam fora
ok("Resumo: cartões novos com média, mediana e '12 de 14 senhas' (sem o dado ficam fora)")

tela_ana = html.unescape(get(f"{URL}&unidade_id={ids['ana']}").get_data(as_text=True))
assert "Outros atendimentos (mm:ss)" not in cabecalhos(tela_ana) and len(cabecalhos(tela_ana)) == 14
assert re.search(r"<td>08:00</td>\s*<td>—</td>\s*<td>11:00</td>", tela_ana), "Anápolis: Atendente 08:00, Executor —, permanência 11:00"
ok("Unidade sem coleta nem Outros: coluna Outros some da tela (14 colunas) e Executor fica '—'")

tela_area = html.unescape(get(f"{URL}&area_fila_id={ids['area']}").get_data(as_text=True))
assert "<th>Permanência total - média</th>" in tela_area and "<th>Atendimento executor - mediana</th>" in tela_area
assert "Espera total - média" not in tela_area
ok("Quadro 'Por fila' (filtro de Área): Permanência total e Atendimento executor")

# ===========================================================================
# 3) Exportações - CSV (par min + mm:ss), Excel (duração), PDF (todas as colunas)
# ===========================================================================
def baixar(formato, extra=""):
    resp = get(f"{URL}&formato={formato}{extra}")
    resp.direct_passthrough = False
    return resp.get_data()


bruto = baixar("csv").decode("utf-8-sig")
df = pd.read_csv(io.StringIO(bruto), sep=";", decimal=",").set_index("Senha")
for rotulo in ("Espera inicial", "Espera intermediária", "Espera total", "Atendimento atendente", "Atendimento executor",
               "Outros atendimentos", "Atendimento total", "Permanência total"):
    assert f"{rotulo} (min)" in df.columns and f"{rotulo} (mm:ss)" in df.columns, rotulo
assert "Unidade" in df.columns
g1 = df.loc["G1"]
assert (g1["Atendimento atendente (min)"], g1["Atendimento atendente (mm:ss)"]) == (25.0, "25:00")
assert (g1["Atendimento executor (mm:ss)"], g1["Permanência total (mm:ss)"]) == ("15:00", "45:00")
assert pd.isna(g1["Outros atendimentos (min)"]) and df.loc["BS"]["Outros atendimentos (mm:ss)"] == "08:00"
assert NOME not in bruto
ok("CSV: par (min) + (mm:ss) das 8 colunas de tempo (Outros sempre), Unidade, valores do exemplo, vazio sem dado, sem nome")

etapas_csv = pd.read_csv(io.StringIO(baixar("csv_etapas").decode("utf-8-sig")), sep=";", decimal=",")
bs = etapas_csv[etapas_csv["Senha"] == "BS"]
assert list(bs["Classificação"].fillna("")) == ["", "Atendente", "", "Outros", "", "Executor"], list(bs["Classificação"])
ok("CSV por etapa: coluna Classificação (vazia nas esperas)")

livro = openpyxl.load_workbook(io.BytesIO(baixar("xlsx")))
aba = livro["Por senha"]
col = {cel.value: i for i, cel in enumerate(aba[1])}
linhas_xlsx = {r[0].value: r for r in aba.iter_rows(min_row=2)}
celula = linhas_xlsx["G1"][col["Permanência total (h:mm:ss)"]]
assert celula.value == timedelta(minutes=45) and celula.number_format == "[h]:mm:ss", (celula.value, celula.number_format)
assert linhas_xlsx["G1"][col["Atendimento atendente (h:mm:ss)"]].value == timedelta(minutes=25)
assert linhas_xlsx["G1"][col["Outros atendimentos (h:mm:ss)"]].value is None
assert "Classificação" in [cel.value for cel in livro["Por etapa"][1]]
for nome_aba in livro.sheetnames:
    for linha in livro[nome_aba].iter_rows(values_only=True):
        assert NOME not in [str(v) for v in linha], nome_aba
ok("Excel: duração nativa [h]:mm:ss nas colunas novas (45:00, 25:00, vazio), Classificação na aba Por etapa, sem nome")

# PDF = colunas da tela + "Outros atendimentos" SEMPRE (Anápolis não tem Outros:
# na tela a coluna some, no PDF fica). Cabeçalhos com &shy; - o helper
# (tests/_pdf_texto.py) tira o hífen de quebra.
pdf = texto_compacto_pdf(baixar("pdf", f"&unidade_id={ids['ana']}"))
for cabecalho in ("Espera inicial", "Espera intermediária", "Atendimento atendente", "Atendimento executor",
                  "Outros atendimentos", "Permanência total"):
    assert compactar(f"{cabecalho} (mm:ss)") in pdf, cabecalho
assert compactar("Etapas de atendimento") in pdf and compactar("Operador (finalização)") in pdf
# Sem Unidade, Espera total e Atendimento total na TABELA (os cartões do resumo
# continuam com "Espera total"/"Atendimento total", mas sem "(mm:ss)").
assert compactar("Senha Emissão Fila inicial") in pdf, "Unidade não deveria estar entre Emissão e Fila inicial"
assert compactar("Espera total (mm:ss)") not in pdf and compactar("Atendimento total (mm:ss)") not in pdf
assert compactar("A permanência é o tempo real na clínica") in pdf
assert compactar(NOME) not in pdf and "Sigiloso" not in pdf
ok("PDF: colunas da tela + Outros (sempre, mesmo sem valor); sem Unidade, Espera total e Atendimento total; nota; sem nome")

print(f"\n{len(passos)} verificações OK")
