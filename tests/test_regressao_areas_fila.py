"""
Teste de regressão: Áreas - agrupamento cadastrável de filas para os
relatórios (adendo 10.76).

Cenário (08/10/2026; Clinica A com Matriz "MAT" e Laranjeiras "LAR"; Clinica
B com uma área própria):
  Matriz:      F1 "Dr. Geraldo", F2 "Dra. Ana", F3 "Coleta", F4 "Recepção"
  Laranjeiras: F5 "Coleta"
  S1 (F1) espera 10 min | S2 (F2) espera 20 | S3 (F3) espera 5
  S4 Recepção -> Coleta: espera 2 (F4), repasse 1, espera 3 (F3), conclusão 4
  S5 (F5) espera 6

Cobre: RBAC e isolamento multi-tenant; validação (fila só em área da própria
unidade, nome único por unidade); fila em UMA área só; aviso (antes de
salvar, JS real) e resumo (flash) de fila movida; select de Área no cadastro
de Fila (filtrado por unidade, área inativa só quando já é a da fila);
"Filas sem área"; filtro por área = união das filas dela; "Agrupar por:
Área" com "Sem área" e soma das áreas = total nos três agregados; área
inativa no filtro com "(inativa)"; mudar a fila de área muda os relatórios
(limitação documentada); exportações/PDF/log de auditoria com a área; quadro
"Por fila" no Tempos por senha; exclusão segura (área, empresa, fila com
exportação); nenhum código de totem/painel/atendimento lê a área.

Execução: python -m tests.test_regressao_areas_fila (a partir da raiz do
projeto, com o virtualenv ativado).
"""
import os

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

import glob
import html
import io
import re
from datetime import datetime, timedelta

import pandas as pd
from playwright.sync_api import sync_playwright

from app import create_app
from app.extensions import db
from app.models import AreaFila, Empresa, Fila, LogAtendimento, LogExportacaoRelatorio, Perfil, Senha, Unidade, Usuario
from tests._pdf_texto import compactar, texto_compacto_pdf

app = create_app("testing")

passos = []


def ok(msg):
    passos.append(msg)
    print("OK:", msg)


T0 = datetime(2026, 10, 8, 12, 0)

with app.app_context():
    db.create_all()
    p_admin, p_gerencial = Perfil(nome="admin"), Perfil(nome="gerencial")
    emp_a, emp_b = Empresa(nome="Clinica A"), Empresa(nome="Clinica B")
    db.session.add_all([p_admin, p_gerencial, emp_a, emp_b])
    db.session.commit()
    matriz = Unidade(empresa_id=emp_a.id, nome="Matriz", sigla="MAT")
    laranj = Unidade(empresa_id=emp_a.id, nome="Laranjeiras", sigla="LAR")
    unid_b = Unidade(empresa_id=emp_b.id, nome="Unidade B", sigla="UB")
    db.session.add_all([matriz, laranj, unid_b])
    db.session.commit()

    def fila(unidade, nome):
        f = Fila(empresa_id=unidade.empresa_id, unidade_id=unidade.id, nome=nome)
        db.session.add(f)
        db.session.commit()
        return f

    f1, f2, f3, f4 = (fila(matriz, n) for n in ("Dr. Geraldo", "Dra. Ana", "Coleta", "Recepção"))
    f5 = fila(laranj, "Coleta")
    f_b = fila(unid_b, "Fila B")
    area_b = AreaFila(empresa_id=emp_b.id, unidade_id=unid_b.id, nome="Área B")
    db.session.add(area_b)
    db.session.commit()

    def usuario(login, empresa, perfil):
        u = Usuario(empresa_id=empresa.id if empresa else None, nome=login, login=login)
        u.set_senha("senha123")
        u.perfis = [perfil] if perfil else []
        db.session.add(u)
        db.session.commit()
        return u

    admin_a = usuario("admin_a_area", emp_a, p_admin)
    usuario("admin_b_area", emp_b, p_admin)
    usuario("gerencial_a_area", emp_a, p_gerencial)
    usuario("super_area", None, None)

    def senha(codigo, fila_final, eventos):
        s = Senha(empresa_id=fila_final.empresa_id, unidade_id=fila_final.unidade_id, fila_id=fila_final.id,
                  codigo=codigo, sequencial=1, prioridade=False, status="finalizada", criada_em=T0)
        db.session.add(s)
        db.session.commit()
        for evento, minuto, fila_snap in eventos:
            db.session.add(LogAtendimento(
                senha_id=s.id, evento=evento, timestamp=T0 + timedelta(minutes=minuto),
                usuario_id=None if evento == "gerada" else admin_a.id, unidade_id=fila_final.unidade_id,
                fila_id=fila_snap.id, setor="recepcao",
            ))
        db.session.commit()

    for codigo, f, espera in (("S1", f1, 10), ("S2", f2, 20), ("S3", f3, 5), ("S5", f5, 6)):
        senha(codigo, f, [("gerada", 0, f), ("chamada_senha", espera, f), ("fim_atendimento", espera + 1, f)])
    senha("S4", f3, [("gerada", 0, f4), ("chamada_senha", 2, f4), ("realocado", 3, f4),
                     ("chamada_senha", 6, f3), ("fim_atendimento", 10, f3)])

    ids = dict(emp_a=emp_a.id, emp_b=emp_b.id, matriz=matriz.id, laranj=laranj.id, f1=f1.id, f2=f2.id,
               f3=f3.id, f4=f4.id, f5=f5.id, f_b=f_b.id, area_b=area_b.id)


def login(login_):
    c = app.test_client()
    assert c.post("/login", data={"login": login_, "senha": "senha123"}, follow_redirects=True).status_code == 200
    return c


def texto(resp):
    return html.unescape(resp.get_data(as_text=True))


def area_id(nome):
    with app.app_context():
        return AreaFila.query.filter_by(nome=nome).one().id


def area_da_fila(fila_id):
    with app.app_context():
        return db.session.get(Fila, fila_id).area_fila_id


c = login("admin_a_area")

# ===========================================================================
# 1) RBAC e isolamento multi-tenant no admin.
# ===========================================================================
assert login("gerencial_a_area").get("/admin/areas").status_code == 403
c_b = login("admin_b_area")
assert c_b.get(f"/admin/areas/{ids['area_b']}/editar").status_code == 200
assert "Área B" not in texto(c.get("/admin/areas"))
ok("RBAC igual ao de Filas (gerencial 403) e listagem só com as áreas da própria empresa")

# ===========================================================================
# 2) Criar áreas, validação, fila em uma área só, aviso e resumo de movida.
# ===========================================================================
r = c.post("/admin/areas/nova", data={"nome": "2º Andar", "unidade_id": ids["matriz"], "ativo": "on",
                                       "filas": [ids["f1"], ids["f2"]]}, follow_redirects=True)
assert "Área '2º Andar' salva." in texto(r)
andar = area_id("2º Andar")
assert area_da_fila(ids["f1"]) == andar and area_da_fila(ids["f2"]) == andar
assert c_b.get(f"/admin/areas/{andar}/editar").status_code == 404
ok("Área '2º Andar' criada com 2 filas; outra empresa recebe 404 ao editá-la")

r = c.post("/admin/areas/nova", data={"nome": "Lab", "unidade_id": ids["laranj"], "filas": [ids["f1"]]})
assert "A fila 'Dr. Geraldo' é de outra unidade" in texto(r)
r = c.post("/admin/areas/nova", data={"nome": "2º andar", "unidade_id": ids["matriz"]})
assert "Já existe uma área chamada '2º Andar' nesta unidade." in texto(r)
ok("Validação no servidor: fila de outra unidade recusada; nome repetido na unidade (ignorando maiúsculas) recusado")

r = c.post("/admin/areas/nova", data={"nome": "Laboratório", "unidade_id": ids["matriz"], "ativo": "on",
                                       "filas": [ids["f2"], ids["f3"]]}, follow_redirects=True)
assert "'Dra. Ana' movida de '2º Andar' para 'Laboratório'" in texto(r)
lab = area_id("Laboratório")
assert area_da_fila(ids["f2"]) == lab and area_da_fila(ids["f1"]) == andar
with app.app_context():
    assert Fila.query.filter_by(area_fila_id=andar).count() == 1  # Dra. Ana saiu do 2º Andar
ok("Fila em UMA área só: 'Dra. Ana' movida do 2º Andar para o Laboratório, com o resumo no flash")

form_lab = texto(c.get(f"/admin/areas/{lab}/editar"))
assert 'data-outra-area="2º Andar"' in form_lab and "(hoje em 2º Andar)" in form_lab
with sync_playwright() as pw:
    nav = pw.chromium.launch()
    pg = nav.new_page()
    pg.set_content(re.sub(r'<link rel="stylesheet"[^>]*>', "", form_lab))
    assert pg.is_hidden("#aviso-movidas")
    pg.check(f'input[name=filas][value="{ids["f1"]}"]')
    aviso = pg.inner_text("#aviso-movidas")
    assert pg.is_visible("#aviso-movidas") and aviso.startswith("1 fila(s) serão movidas de outra área"), aviso
    assert "Dr. Geraldo (hoje em 2º Andar)" in aviso, aviso
    pg.select_option("#unidade_id", str(ids["laranj"]))  # filas da Matriz somem e são desmarcadas
    assert pg.is_hidden("#aviso-movidas") and not pg.is_checked(f'input[name=filas][value="{ids["f1"]}"]')
    nav.close()
ok("Aviso ANTES de salvar (JS real): marcar uma fila de outra área mostra 'N fila(s) serão movidas...'")

# ===========================================================================
# 3) Cadastro de Fila: select de Área; "Filas sem área".
# ===========================================================================
form_fila = texto(c.get(f"/admin/filas/{ids['f4']}/editar"))
assert f'<option value="{andar}" data-unidade-id="{ids["matriz"]}"' in form_fila
r = c.post(f"/admin/filas/{ids['f5']}/editar", data={"nome": "Coleta", "unidade_id": ids["laranj"],
                                                    "digitos_sequencial": 3, "area_fila_id": andar})
assert "A área escolhida é de outra unidade" in texto(r) and area_da_fila(ids["f5"]) is None
lista = texto(c.get(f"/admin/areas?empresa_id={ids['emp_a']}"))
assert "Filas sem área (2)" in lista and "[MAT] Recepção" in lista and "[LAR] Coleta" in lista
ok("Cadastro de Fila: select de Área por unidade (outra unidade recusada); listagem 'Filas sem área (2)'")

# ===========================================================================
# 4) Relatórios: filtro por área, agrupar por área (soma = total), inativa.
# ===========================================================================
PER = "data_inicio=2026-10-08&data_fim=2026-10-08"


def csv(rota, extra=""):
    resp = c.get(f"/relatorios/{rota}?{PER}&formato=csv{extra}")
    assert resp.status_code == 200, (rota, resp.status_code)
    resp.direct_passthrough = False
    corpo = resp.get_data().decode("utf-8-sig")
    sep = ";" if rota == "tempos-por-senha" else ","
    return pd.read_csv(io.StringIO(corpo), sep=sep, decimal="," if sep == ";" else ".")


tps = csv("tempos-por-senha", f"&area_fila_id={lab}")
assert set(tps["Senha"]) == {"S2", "S3", "S4"}, set(tps["Senha"])  # filas F2 + F3 (fila final)
assert set(tps["Área"]) == {"Laboratório"}
ok("Filtro por área = união das filas dela (S2, S3 e S4 - esta pela fila final Coleta); coluna 'Área' no CSV")

espera_fila = csv("tempo-espera")
espera_area = csv("tempo-espera", "&agrupar=area")
assert "area" in espera_area.columns and "fila" not in espera_area.columns
assert espera_area["quantidade"].sum() == espera_fila["quantidade"].sum() == 6
soma = lambda df: (df["quantidade"] * df["tempo_medio_segundos"]).sum()
assert abs(soma(espera_area) - soma(espera_fila)) < 1
por_area = dict(zip(zip(espera_area["unidade"], espera_area["area"]), espera_area["quantidade"]))
assert por_area == {("Matriz", "2º Andar"): 1, ("Matriz", "Laboratório"): 3, ("Matriz", "Sem área"): 1,
                    ("Laranjeiras", "Sem área"): 1}, por_area
ok("Tempo de espera agrupado por Área: 'Sem área' presente e soma das áreas = total (6 esperas, mesma soma de tempo)")

for rota, coluna in (("volume", "total"), ("tempo-atendimento", "quantidade")):
    por_fila, agrupado = csv(rota), csv(rota, "&agrupar=area")
    assert agrupado[coluna].sum() == por_fila[coluna].sum(), rota
    assert "Sem área" in set(agrupado["area"]), rota
ok("Volume e Tempo de atendimento agrupados por Área: soma = total e 'Sem área' presente")

c.post(f"/admin/areas/{lab}/toggle-ativo")
tela = texto(c.get(f"/relatorios/tempo-espera?{PER}"))
assert f'<option value="{lab}" data-unidade-id="{ids["matriz"]}" >[MAT] Laboratório (inativa)</option>' in tela
assert "<th>Área</th>" not in tela and "<th>Fila</th>" in tela
tela_area = texto(c.get(f"/relatorios/tempo-espera?{PER}&agrupar=area"))
assert "<th>Área</th>" in tela_area and "<td>[MAT] Laboratório</td>" in tela_area and "<td>Sem área</td>" in tela_area
ok("Área INATIVA continua no filtro com '(inativa)' e os relatórios seguem agrupando nela")

form_f3 = texto(c.get(f"/admin/filas/{ids['f3']}/editar"))
form_f1 = texto(c.get(f"/admin/filas/{ids['f1']}/editar"))
assert "Laboratório (inativa)</option>" in form_f3 and "Laboratório" not in form_f1
ok("Cadastro de Fila: área inativa some do select, exceto na fila que já está nela (não desvincula sem querer)")

# Mudar a fila de área muda os relatórios (área ATUAL, sem snapshot - limitação).
c.post(f"/admin/areas/{andar}/editar", data={"nome": "2º Andar", "unidade_id": ids["matriz"], "ativo": "on",
                                              "filas": [ids["f1"], ids["f4"]]})
depois = csv("tempo-espera", "&agrupar=area")
por_area = dict(zip(zip(depois["unidade"], depois["area"]), depois["quantidade"]))
assert por_area[("Matriz", "2º Andar")] == 2 and ("Matriz", "Sem área") not in por_area, por_area
ok("Mudar a Recepção para o 2º Andar muda o passado (S4 já aparece lá): área atual, limitação documentada")

# ===========================================================================
# 5) PDF, log de auditoria, Tempos por senha "Por fila", multi-tenant.
# ===========================================================================
resp = c.get(f"/relatorios/tempo-espera?{PER}&agrupar=area&area_fila_id={andar}&formato=pdf")
assert resp.status_code == 200
# Texto SEM espaços dos dois lados (tests/_pdf_texto.py, adendo 10.78): a célula
# "[MAT] 2º Andar" pode quebrar em duas linhas conforme a fonte do servidor.
pdf = texto_compacto_pdf(resp.get_data())
for esperado in ("Área: [MAT] 2º Andar", "Agrupado por: Área", "[MAT] 2º Andar"):
    assert compactar(esperado) in pdf, f"PDF sem '{esperado}': {pdf}"
with app.app_context():
    log = LogExportacaoRelatorio.query.filter_by(formato="pdf").order_by(LogExportacaoRelatorio.id.desc()).first()
    assert log.area_fila_id == andar
ok("PDF com 'Área: ...' e 'Agrupado por: Área' no cabeçalho; log de auditoria grava area_fila_id")

tela_tps = texto(c.get(f"/relatorios/tempos-por-senha?{PER}&area_fila_id={andar}"))
assert "<h2>Por fila</h2>" in tela_tps and "Filtro de Área: entram as senhas cuja fila ATUAL" in tela_tps
assert "<td>[MAT] Dr. Geraldo</td>" in tela_tps and "<td>10:00</td>" in tela_tps
assert "<h2>Por fila</h2>" not in texto(c.get(f"/relatorios/tempos-por-senha?{PER}"))
ok("Tempos por senha filtrado por área: quadro 'Por fila' e a nota da semântica do filtro")

tps_b = csv("tempos-por-senha", f"&area_fila_id={ids['area_b']}")
assert set(tps_b["Senha"]) == {"S1", "S2", "S3", "S4", "S5"}  # área de outra empresa ignorada
with app.app_context():
    assert LogExportacaoRelatorio.query.order_by(LogExportacaoRelatorio.id.desc()).first().area_fila_id is None
ok("Isolamento multi-tenant: area_fila_id de outra empresa é ignorado (relatório inteiro, log sem área)")

# ===========================================================================
# 6) Exclusão segura.
# ===========================================================================
r = c.post(f"/admin/areas/{andar}/excluir", follow_redirects=True)
assert "Não é possível excluir a área '2º Andar'" in texto(r) and "filas vinculadas" in texto(r)
c.post(f"/admin/areas/{andar}/editar", data={"nome": "2º Andar", "unidade_id": ids["matriz"], "filas": []})
r = c.post(f"/admin/areas/{andar}/excluir", follow_redirects=True)
assert "exportações de relatório registradas" in texto(r)  # usada no PDF acima
c.post("/admin/areas/nova", data={"nome": "Temporária", "unidade_id": ids["matriz"]})
r = c.post(f"/admin/areas/{area_id('Temporária')}/excluir", follow_redirects=True)
assert "Área 'Temporária' excluída." in texto(r)
ok("Exclusão de área: bloqueada com filas vinculadas e com exportação registrada; área vazia e sem uso é excluída")

with app.app_context():
    vazia = Empresa(nome="Clinica Vazia")
    db.session.add(vazia)
    db.session.commit()
    u_vazia = Unidade(empresa_id=vazia.id, nome="U")
    db.session.add(u_vazia)
    db.session.commit()
    db.session.add(AreaFila(empresa_id=vazia.id, unidade_id=u_vazia.id, nome="X"))
    db.session.commit()
    id_vazia = vazia.id
r = login("super_area").post(f"/admin/empresas/{id_vazia}/excluir", follow_redirects=True)
assert "áreas" in texto(r) and "Não é possível excluir" in texto(r)
ok("Exclusão de empresa lista 'áreas' entre os bloqueios (sem isso daria erro de FK)")

c_tmp = login("admin_a_area")
with app.app_context():
    nova = Fila(empresa_id=ids["emp_a"], unidade_id=ids["matriz"], nome="Sem senhas")
    db.session.add(nova)
    db.session.commit()
    id_nova = nova.id
c_tmp.get(f"/relatorios/volume?{PER}&fila_id={id_nova}&formato=csv")
r = c_tmp.post(f"/admin/filas/{id_nova}/excluir", follow_redirects=True)
assert "exportações de relatório registradas com o filtro desta fila" in texto(r)
ok("Achado: fila sem senhas mas usada como filtro numa exportação não é mais excluída (antes, erro 500)")

# ===========================================================================
# 7) Fluxos protegidos não leem a área.
# ===========================================================================
for pasta in ("app/totem", "app/painel", "app/atendimento", "app/templates/totem", "app/templates/painel",
              "app/templates/atendimento"):
    for arq in glob.glob(f"{pasta}/**/*.*", recursive=True):
        if arq.endswith((".py", ".html")):
            assert "area_fila" not in open(arq, encoding="utf-8").read(), arq
ok("Totem, painel de TV e atendimento (rodízio, Iniciar Turno, Redirecionar) não referenciam area_fila")

print(f"\n{len(passos)} verificações OK")
