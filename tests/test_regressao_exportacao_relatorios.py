"""
Teste de regressão: exportação de relatórios em CSV/Excel (app/relatorios/export.py
+ o parâmetro `formato=csv|xlsx` nas rotas de app/relatorios/routes.py).

Gap de cobertura identificado na auditoria de testes: exportar() nunca tinha
sido exercitada por nenhum teste automatizado, apesar de usar pandas/openpyxl
(risco real de quebra silenciosa em mudança de schema/coluna). Cobre:

1) Unitário: exportar() gera CSV com BOM utf-8-sig (acentuação abre certo no
   Excel/Windows) e Excel (.xlsx) via openpyxl, cada um com mimetype,
   Content-Disposition e conteúdo (linhas/colunas) corretos.
2) Integração HTTP: /relatorios/volume?formato=csv|xlsx respeita o mesmo RBAC
   e isolamento de tenant já cobertos noutros testes de relatórios - aqui o
   foco é que o parâmetro `formato` desvia a rota do render_template normal
   pra exportar(), com os dados já filtrados/legíveis (nome da fila/unidade,
   não IDs crus) chegando ao arquivo baixado.
3) `formato` inválido/ausente não aciona a exportação - continua renderizando
   a página HTML normalmente (comportamento por exclusão em
   `request.args.get("formato") in ("csv", "xlsx")`).

Execução: python -m tests.test_regressao_exportacao_relatorios (a partir da
raiz do projeto, com o virtualenv ativado).
"""
import io
import os

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

import pandas as pd

from app import create_app
from app.extensions import db
from app.models import Empresa, Fila, Perfil, Senha, Unidade, Usuario
from app.relatorios.export import exportar

app = create_app("testing")

passos = []


def ok(msg):
    passos.append(msg)
    print("OK:", msg)


# ===========================================================================
# A) Unitário: exportar() isolado, sem precisar de request/contexto de rota.
# ===========================================================================
with app.test_request_context():
    linhas = [
        {"fila": "Coleta", "unidade": "Unidade A1", "quantidade": 3, "tempo_medio_segundos": 120},
        {"fila": "Recepção Geral", "unidade": "Unidade A1", "quantidade": 5, "tempo_medio_segundos": 90},
    ]

    resp_csv = exportar(linhas, "tempo_medio_espera", "csv")
    assert resp_csv.mimetype == "text/csv"
    assert resp_csv.headers["Content-Disposition"].startswith("attachment;")
    assert "tempo_medio_espera.csv" in resp_csv.headers["Content-Disposition"]
    resp_csv.direct_passthrough = False
    corpo_csv = resp_csv.get_data()
    assert corpo_csv.startswith(b"\xef\xbb\xbf")  # BOM utf-8-sig
    texto_csv = corpo_csv.decode("utf-8-sig")
    assert "Coleta" in texto_csv and "Recepção Geral" in texto_csv
    assert "120" in texto_csv and "90" in texto_csv
    ok("exportar(..., 'csv') gera CSV com BOM utf-8-sig, nome de arquivo e conteúdo corretos")

    resp_xlsx = exportar(linhas, "tempo_medio_espera", "xlsx")
    assert resp_xlsx.mimetype == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert "tempo_medio_espera.xlsx" in resp_xlsx.headers["Content-Disposition"]
    resp_xlsx.direct_passthrough = False
    df_lido = pd.read_excel(io.BytesIO(resp_xlsx.get_data()), engine="openpyxl")
    assert list(df_lido["fila"]) == ["Coleta", "Recepção Geral"]
    assert list(df_lido["quantidade"]) == [3, 5]
    ok("exportar(..., 'xlsx') gera um .xlsx válido (relido via pandas/openpyxl) com as mesmas linhas")

    resp_vazio = exportar([], "vazio", "csv")
    assert resp_vazio.status_code == 200
    ok("exportar() não quebra com lista de linhas vazia (relatório sem resultado no período)")

# ===========================================================================
# B) Integração HTTP: /relatorios/volume?formato=csv|xlsx
# ===========================================================================
with app.app_context():
    db.create_all()

    perfil_admin = Perfil(nome="admin")
    db.session.add(perfil_admin)
    db.session.commit()

    empresa = Empresa(nome="Clinica A")
    db.session.add(empresa)
    db.session.commit()

    unidade = Unidade(empresa_id=empresa.id, nome="Unidade A1")
    db.session.add(unidade)
    db.session.commit()

    fila = Fila(empresa_id=empresa.id, unidade_id=unidade.id, nome="Coleta Matinal", sigla="CM")
    db.session.add(fila)
    db.session.commit()

    admin = Usuario(empresa_id=empresa.id, nome="Admin A", login="admin_export")
    admin.set_senha("senha123")
    admin.perfis = [perfil_admin]
    db.session.add(admin)
    db.session.commit()

    # criada_em usa datetime.utcnow() por padrão no model - cai dentro da janela
    # default do relatório (últimos 30 dias até hoje, ver resolver_filtros()).
    for i in range(3):
        db.session.add(Senha(
            empresa_id=empresa.id, unidade_id=unidade.id, fila_id=fila.id,
            codigo=f"CM00{i}", sequencial=i, prioridade=False, status="finalizada",
        ))
    db.session.commit()

    ids = dict(empresa=empresa.id, unidade=unidade.id, fila=fila.id)


def login_como(login):
    c = app.test_client()
    resp = c.post("/login", data={"login": login, "senha": "senha123"}, follow_redirects=True)
    assert resp.status_code == 200, (login, resp.status_code)
    return c


c_admin = login_como("admin_export")

resp = c_admin.get("/relatorios/volume?formato=csv")
assert resp.status_code == 200
assert resp.mimetype == "text/csv"
texto = resp.get_data().decode("utf-8-sig")
assert "Coleta Matinal" in texto  # nome legível da fila, não o fila_id cru
assert "Unidade A1" in texto
ok("GET /relatorios/volume?formato=csv baixa um CSV com nomes legíveis de fila/unidade (não IDs)")

resp = c_admin.get("/relatorios/volume?formato=xlsx")
assert resp.status_code == 200
assert resp.mimetype == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
df_lido = pd.read_excel(io.BytesIO(resp.get_data()), engine="openpyxl")
assert "Coleta Matinal" in list(df_lido["fila"])
assert int(df_lido[df_lido["fila"] == "Coleta Matinal"]["total"].iloc[0]) == 3
ok("GET /relatorios/volume?formato=xlsx baixa um .xlsx com o total de senhas correto (3)")

resp = c_admin.get("/relatorios/volume")
assert resp.status_code == 200
assert resp.mimetype == "text/html"
ok("Sem `formato` (ou valor inválido) na querystring, a rota renderiza a página HTML normalmente, sem exportar")

resp = c_admin.get("/relatorios/volume?formato=pdf")
assert resp.status_code == 200
assert resp.mimetype == "text/html"
ok("`formato` desconhecido (ex.: 'pdf') é ignorado - continua renderizando HTML, não quebra")

print()
print(f"TODOS OS {len(passos)} TESTES DE EXPORTACAO DE RELATORIOS (CSV/XLSX) PASSARAM")
