"""
Teste de regressão: identificação da fila por unidade nos relatórios (adendo
10.69). Bug real de produção: o select "Fila" dos relatórios listava várias
"Coleta" sem indicar a unidade de cada uma, e não respeitava a Unidade
escolhida no filtro.

Cobre:
1) rotulos_filas(): "[SIGLA] Nome"; colisão residual (fallback de sigla igual
   em duas unidades sem sigla cadastrada - "Anápolis - X"/"Anápolis - Y" ->
   "[ANÁ]" - e nome repetido na MESMA unidade, case/accent-insensitive) ganha
   " (id)" em TODOS os membros do grupo; calculado sobre a empresa inteira (um
   gerencial que só vê uma das unidades recebe o MESMO rótulo) e inclui filas
   INATIVAS (rótulo estável ao ativar/desativar outra fila do grupo).
2) Select "Fila" nas 4 telas: <optgroup> por unidade (nome completo) +
   option com o rótulo + data-unidade-id; o script de filtro por unidade
   está presente.
3) Servidor: fila_id de outra unidade (incompatível com unidade_id) ou de
   outra empresa é descartado - nunca filtra/vaza dado de outro tenant.
4) Mesmo rótulo na tabela (volume, tempo de atendimento), nos dois
   dashboards e no PDF (tabela + cabeçalho filtros_legiveis).
5) CSV/Excel: Fila com o nome puro + coluna "unidade" separada (inclusive a
   nova coluna em tempo_atendimento).

Execução: python -m tests.test_regressao_relatorios_fila_unidade (a partir da
raiz do projeto, com o virtualenv ativado).
"""
import os

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

import html
import io
import re
from datetime import date, datetime, timedelta

import pandas as pd
from pypdf import PdfReader

from app import create_app
from app.extensions import db
from app.models import Empresa, Fila, LogAtendimento, Perfil, Senha, Unidade, Usuario
from app.relatorios.helpers import rotulos_filas

app = create_app("testing")

passos = []


def ok(msg):
    passos.append(msg)
    print("OK:", msg)


with app.app_context():
    db.create_all()

    perfil_admin = Perfil(nome="admin")
    perfil_gerencial = Perfil(nome="gerencial")
    db.session.add_all([perfil_admin, perfil_gerencial])
    db.session.commit()

    empresa = Empresa(nome="Clinica Rotulos")
    empresa_b = Empresa(nome="Clinica Outra")
    db.session.add_all([empresa, empresa_b])
    db.session.commit()

    matriz = Unidade(empresa_id=empresa.id, nome="Matriz", sigla="MAT")
    laranjeiras = Unidade(empresa_id=empresa.id, nome="Laranjeiras", sigla="LAR")
    # Sem sigla cadastrada: fallback de 3 letras colide ("ANÁ" nas duas).
    anapolis_cm = Unidade(empresa_id=empresa.id, nome="Anápolis - Centro Médico")
    anapolis_lab = Unidade(empresa_id=empresa.id, nome="Anápolis - Laboratório")
    unidade_b = Unidade(empresa_id=empresa_b.id, nome="Unidade Outra", sigla="OUT")
    db.session.add_all([matriz, laranjeiras, anapolis_cm, anapolis_lab, unidade_b])
    db.session.commit()

    def nova_fila(unidade, nome):
        f = Fila(empresa_id=unidade.empresa_id, unidade_id=unidade.id, nome=nome)
        db.session.add(f)
        db.session.commit()
        return f

    coleta_mat = nova_fila(matriz, "Coleta")
    coleta_lar = nova_fila(laranjeiras, "Coleta")
    coleta_ana_cm = nova_fila(anapolis_cm, "Coleta")
    coleta_ana_lab = nova_fila(anapolis_lab, "Coleta")
    # Mesmo nome DENTRO da mesma unidade, diferindo só em caixa/acento -
    # colide pela collation utf8mb4_0900_ai_ci (grupo "b" da investigação).
    recepcao_mat = nova_fila(matriz, "Recepção")
    recepcao_mat_2 = nova_fila(matriz, "RECEPCAO")
    coleta_b = nova_fila(unidade_b, "Coleta")
    # Colisão envolvendo uma fila INATIVA: ela entra no desempate, para o
    # rótulo da ativa não mudar quando a outra for ativada/desativada (e
    # relatórios de períodos antigos continuarem distinguíveis).
    deve_material_inativa = nova_fila(matriz, "Deve Material")
    deve_material_inativa.ativo = False
    deve_material_ativa = nova_fila(matriz, "Deve Material")
    db.session.commit()

    admin = Usuario(empresa_id=empresa.id, nome="Admin Rotulos", login="admin_rotulos")
    admin.set_senha("senha123")
    admin.perfis = [perfil_admin]
    # Gerencial restrito a UMA das unidades "ANÁ" - continua recebendo o rótulo
    # com "(id)", porque o desempate é calculado sobre a empresa inteira.
    gerencial = Usuario(empresa_id=empresa.id, nome="Gerencial Anapolis", login="gerencial_ana")
    gerencial.set_senha("senha123")
    gerencial.perfis = [perfil_gerencial]
    gerencial.unidades = [anapolis_cm]
    db.session.add_all([admin, gerencial])
    db.session.commit()

    agora = datetime.utcnow()

    def nova_senha(fila, codigo, status="finalizada"):
        s = Senha(
            empresa_id=fila.empresa_id, unidade_id=fila.unidade_id, fila_id=fila.id,
            codigo=codigo, sequencial=1, prioridade=False, status=status,
        )
        db.session.add(s)
        db.session.commit()
        return s

    s_mat_1 = nova_senha(coleta_mat, "MAT001")
    nova_senha(coleta_mat, "MAT002", status="aguardando")
    nova_senha(coleta_lar, "LAR001")
    nova_senha(coleta_ana_cm, "ANA001")
    nova_senha(coleta_ana_lab, "ANB001")
    nova_senha(coleta_b, "OUT001")

    # Um atendimento completo na Coleta da Matriz: alimenta tempo_atendimento
    # e as "últimas chamadas" dos dois dashboards.
    for evento, delta in (("chamada_senha", 0), ("fim_atendimento", 90)):
        db.session.add(LogAtendimento(
            senha_id=s_mat_1.id, evento=evento, usuario_id=admin.id, unidade_id=matriz.id,
            fila_id=coleta_mat.id, setor="coleta", timestamp=agora - timedelta(minutes=5) + timedelta(seconds=delta),
        ))
    db.session.commit()

    ids = dict(
        empresa=empresa.id, empresa_b=empresa_b.id,
        matriz=matriz.id, laranjeiras=laranjeiras.id, ana_cm=anapolis_cm.id, ana_lab=anapolis_lab.id,
        coleta_mat=coleta_mat.id, coleta_lar=coleta_lar.id, coleta_ana_cm=coleta_ana_cm.id,
        coleta_ana_lab=coleta_ana_lab.id, recepcao_mat=recepcao_mat.id, recepcao_mat_2=recepcao_mat_2.id,
        coleta_b=coleta_b.id,
    )

    # =======================================================================
    # 1) rotulos_filas: prefixo + desempate por id em todos os membros.
    # =======================================================================
    rotulos = rotulos_filas(empresa)
    assert rotulos[ids["coleta_mat"]] == "[MAT] Coleta", rotulos
    assert rotulos[ids["coleta_lar"]] == "[LAR] Coleta", rotulos
    assert rotulos[ids["coleta_ana_cm"]] == f"[ANÁ] Coleta ({ids['coleta_ana_cm']})", rotulos
    assert rotulos[ids["coleta_ana_lab"]] == f"[ANÁ] Coleta ({ids['coleta_ana_lab']})", rotulos
    ok("Fallback de sigla colidindo ('[ANÁ]' em duas unidades sem sigla): ' (id)' em TODOS os membros do grupo")

    assert rotulos[ids["recepcao_mat"]] == f"[MAT] Recepção ({ids['recepcao_mat']})", rotulos
    assert rotulos[ids["recepcao_mat_2"]] == f"[MAT] RECEPCAO ({ids['recepcao_mat_2']})", rotulos
    ok("Nome repetido na MESMA unidade (só caixa/acento diferente, collation ai_ci): ' (id)' nos dois")

    rotulo_dm_ativa = f"[MAT] Deve Material ({deve_material_ativa.id})"
    rotulo_dm_inativa = f"[MAT] Deve Material ({deve_material_inativa.id})"
    assert rotulos[deve_material_ativa.id] == rotulo_dm_ativa, rotulos
    assert rotulos[deve_material_inativa.id] == rotulo_dm_inativa, rotulos
    deve_material_inativa.ativo = True
    db.session.commit()
    assert rotulos_filas(empresa)[deve_material_ativa.id] == rotulo_dm_ativa
    deve_material_inativa.ativo = False
    db.session.commit()
    assert rotulos_filas(empresa)[deve_material_ativa.id] == rotulo_dm_ativa
    ok("Fila INATIVA entra no desempate: rótulo da ativa é estável ao ativar/desativar a outra")

    assert ids["coleta_b"] not in rotulos
    assert rotulos_filas(empresa_b) == {ids["coleta_b"]: "[OUT] Coleta"}
    ok("rotulos_filas é por empresa: fila de outra empresa nunca entra nem influencia o desempate")

ROTULO_MAT = "[MAT] Coleta"
ROTULO_LAR = "[LAR] Coleta"
ROTULO_ANA_CM = f"[ANÁ] Coleta ({ids['coleta_ana_cm']})"

hoje = date.today()
PERIODO = f"data_inicio={(hoje - timedelta(days=1)).isoformat()}&data_fim={(hoje + timedelta(days=1)).isoformat()}"


def login_como(login):
    c = app.test_client()
    resp = c.post("/login", data={"login": login, "senha": "senha123"}, follow_redirects=True)
    assert resp.status_code == 200, (login, resp.status_code)
    return c


def texto(resp):
    assert resp.status_code == 200, resp.status_code
    return html.unescape(resp.get_data(as_text=True))


def tabela(pagina):
    """Só o <table> do relatório - pra não confundir com o texto do <select>."""
    m = re.search(r"<table>.*?</table>", pagina, re.S)
    return m.group(0) if m else ""


def option_fila(pagina, fila_id):
    m = re.search(rf'<option value="{fila_id}" data-unidade-id="(\d+)"\s*(selected)?\s*>([^<]*)</option>', pagina)
    assert m, f"option da fila {fila_id} não encontrada"
    return int(m.group(1)), bool(m.group(2)), m.group(3)


c_admin = login_como("admin_rotulos")

# ===========================================================================
# 2) Select "Fila" nas 4 telas: optgroup + rótulo + data-unidade-id + script.
# ===========================================================================
TELAS = ("tempo-espera", "tempo-atendimento", "volume", "ranking-operadores")
for tela in TELAS:
    pagina = texto(c_admin.get(f"/relatorios/{tela}?{PERIODO}"))
    assert f'<optgroup label="Matriz" data-unidade-id="{ids["matriz"]}">' in pagina, tela
    assert f'<optgroup label="Laranjeiras" data-unidade-id="{ids["laranjeiras"]}">' in pagina, tela
    assert f'<optgroup label="Anápolis - Centro Médico" data-unidade-id="{ids["ana_cm"]}">' in pagina, tela
    assert option_fila(pagina, ids["coleta_mat"]) == (ids["matriz"], False, ROTULO_MAT), tela
    assert option_fila(pagina, ids["coleta_lar"]) == (ids["laranjeiras"], False, ROTULO_LAR), tela
    assert option_fila(pagina, ids["coleta_ana_cm"]) == (ids["ana_cm"], False, ROTULO_ANA_CM), tela
    assert f'<option value="{ids["coleta_b"]}"' not in pagina, tela
    assert "filtrarFilasPorUnidade" in pagina, tela
ok("Select 'Fila' nas 4 telas: <optgroup> por unidade, '[SIGLA] Nome', data-unidade-id e script de filtro")

# ===========================================================================
# 3) Servidor descarta fila_id incompatível com a unidade ou de outra empresa.
# ===========================================================================
# Fila da Laranjeiras com Unidade=Matriz: antes, relatório vazio sem aviso;
# agora o fila_id é descartado e o relatório sai com a Matriz inteira.
pagina = texto(c_admin.get(f"/relatorios/volume?{PERIODO}&unidade_id={ids['matriz']}&fila_id={ids['coleta_lar']}"))
assert option_fila(pagina, ids["coleta_lar"])[1] is False  # não ficou marcada
assert ROTULO_MAT in tabela(pagina) and ROTULO_LAR not in tabela(pagina)
ok("fila_id de OUTRA unidade que a filtrada é descartado (relatório da unidade inteira, não vazio)")

# Mesma unidade: o filtro de fila continua valendo normalmente.
pagina = texto(c_admin.get(f"/relatorios/volume?{PERIODO}&unidade_id={ids['matriz']}&fila_id={ids['coleta_mat']}"))
assert option_fila(pagina, ids["coleta_mat"])[1] is True
ok("fila_id compatível com a unidade continua aplicado e marcado no select")

pagina = texto(c_admin.get(f"/relatorios/volume?{PERIODO}&fila_id={ids['coleta_b']}"))
t = tabela(pagina)
assert ROTULO_MAT in t and ROTULO_LAR in t  # filtro ignorado: empresa inteira
assert "[OUT]" not in pagina and "OUT001" not in pagina
ok("fila_id de OUTRA empresa é ignorado - nunca filtra nem vaza dado de outro tenant")

# ===========================================================================
# 4) Mesmo rótulo na tabela, nos dashboards e no PDF.
# ===========================================================================
pagina = texto(c_admin.get(f"/relatorios/volume?{PERIODO}"))
t = tabela(pagina)
assert ROTULO_MAT in t and ROTULO_LAR in t and ROTULO_ANA_CM in t
ok("Tabela de volume mostra o rótulo '[SIGLA] Nome' (com '(id)' no grupo colidente)")

pagina = texto(c_admin.get(f"/relatorios/tempo-atendimento?{PERIODO}"))
assert f"<td>{ROTULO_MAT}</td>" in tabela(pagina)
ok("Tabela de tempo de atendimento mostra '[SIGLA] Nome' na coluna Fila")

pagina = texto(c_admin.get(f"/relatorios/dashboard?empresa_id={ids['empresa']}"))
assert pagina.count(f"<td>{ROTULO_MAT}</td>") == 2  # aguardando por fila + últimas chamadas
assert f"<td>{ROTULO_ANA_CM}</td>" in pagina
ok("relatorios/dashboard: mesmo rótulo em 'Senhas aguardando por fila' e 'Últimas chamadas'")

pagina = texto(c_admin.get(f"/dashboard?empresa_id={ids['empresa']}"))
assert f'<span class="nome-fila">{ROTULO_MAT}</span>' in pagina
assert f'<span class="nome-fila">{ROTULO_ANA_CM}</span>' in pagina
assert f"<td>{ROTULO_MAT}</td>" in pagina  # últimos atendimentos
ok("core/dashboard: mesmo rótulo na lista de filas e em 'Últimos atendimentos'")

resp = c_admin.get(f"/relatorios/tempo-atendimento?{PERIODO}&fila_id={ids['coleta_mat']}&formato=pdf")
assert resp.status_code == 200 and resp.mimetype == "application/pdf", resp.status_code
texto_pdf = "".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(resp.get_data())).pages)
assert f"Fila: {ROTULO_MAT}" in texto_pdf, texto_pdf  # cabeçalho filtros_legiveis
assert texto_pdf.count(ROTULO_MAT) >= 2, texto_pdf  # cabeçalho + tabela
ok("PDF: 'Fila: [SIGLA] Nome' no cabeçalho (filtros_legiveis) e o mesmo rótulo na tabela")

# Desempate calculado sobre a empresa inteira: o gerencial só vê UMA das
# unidades "ANÁ", mas recebe exatamente o mesmo rótulo que o admin.
c_gerencial = login_como("gerencial_ana")
pagina = texto(c_gerencial.get(f"/relatorios/volume?{PERIODO}"))
assert option_fila(pagina, ids["coleta_ana_cm"])[2] == ROTULO_ANA_CM
assert ROTULO_ANA_CM in tabela(pagina)
assert f'<option value="{ids["coleta_ana_lab"]}"' not in pagina and ROTULO_MAT not in pagina
ok("Gerencial restrito a uma unidade recebe o MESMO rótulo (desempate sobre a empresa inteira), sem ver as demais")

# ===========================================================================
# 5) CSV/Excel: fila com nome puro + coluna "unidade" separada.
# ===========================================================================
resp = c_admin.get(f"/relatorios/tempo-atendimento?{PERIODO}&formato=csv")
resp.direct_passthrough = False
df = pd.read_csv(io.StringIO(resp.get_data().decode("utf-8-sig")))
assert "unidade" in df.columns, list(df.columns)
assert list(df["unidade"]) == ["Matriz"] and list(df["fila"]) == ["Coleta"], df
ok("CSV de tempo_atendimento ganhou a coluna 'unidade' (fila com o nome puro ao lado)")

resp = c_admin.get(f"/relatorios/volume?{PERIODO}&formato=xlsx")
resp.direct_passthrough = False
df = pd.read_excel(io.BytesIO(resp.get_data()), engine="openpyxl")
pares = set(zip(df["unidade"], df["fila"]))
assert ("Matriz", "Coleta") in pares and ("Laranjeiras", "Coleta") in pares, pares
assert ("Anápolis - Centro Médico", "Coleta") in pares, pares
ok("Excel de volume: fila com o nome puro + coluna 'unidade' (sem prefixo dentro da célula)")

print(f"\n{len(passos)} verificações OK")
