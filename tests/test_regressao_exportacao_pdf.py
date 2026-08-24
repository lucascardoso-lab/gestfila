"""
Teste de regressão: exportação de relatórios em PDF (adendo 10.67,
complementar ao CSV/Excel já coberto por test_regressao_exportacao_
relatorios.py).

Cobre:
1. `?formato=pdf` gera um PDF de verdade (mimetype application/pdf,
   Content-Disposition attachment, bytes começam com a assinatura %PDF) - via
   o subprocess isolado (app/relatorios/_pdf_worker.py, Chromium/Playwright) -
   não um mock, o PDF é gerado de ponta a ponta e o texto é extraído de
   volta (pypdf) pra confirmar o conteúdo real.
2. O cabeçalho do PDF contém os filtros aplicados por extenso (nome da
   unidade, período) - não os valores brutos/IDs.
3. Log de auditoria (`logs_exportacao_relatorio`) gravado com os campos
   corretos para os TRÊS formatos (CSV, Excel, PDF) - novo pra CSV/Excel
   também (não existia antes deste adendo).
4. RBAC idêntico ao das rotas de exportação existentes - perfil sem acesso a
   relatórios (nem admin/gerencial) recebe 403, mesmo pedindo `formato=pdf`.
5. Rodapé "Gerado em" usa o FUSO DA CLÍNICA (`agora_na_clinica()`), não UTC
   do servidor - mesma classe de bug já corrigida 3x neste projeto (10.37/
   10.39/10.54). Relógio simulado (patch de `app.utils.tempo._instante_utc`,
   mesmo padrão de test_regressao_fuso_horario.py) num instante UTC onde a
   hora de Brasília é visivelmente diferente.
6. Se `renderizar_pdf()` falhar, o log de auditoria NÃO é gravado (revisão
   antes da aprovação - `registrar_exportacao()` só roda após o sucesso,
   não mais antes de tentar gerar o PDF).

Execução: python -m tests.test_regressao_exportacao_pdf (a partir da raiz do
projeto, com o virtualenv ativado - requer `playwright install chromium` já
rodado nesta máquina, ver README).
"""
import os

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

import io
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from pypdf import PdfReader

from app import create_app
from app.extensions import db
from app.models import Empresa, Fila, LogExportacaoRelatorio, Perfil, Senha, Unidade, Usuario
from app.relatorios.pdf import FalhaGeracaoPdf

UTC = ZoneInfo("UTC")

app = create_app("testing")

passos = []


def ok(msg):
    passos.append(msg)
    print("OK:", msg)


with app.app_context():
    db.create_all()

    perfil_admin = Perfil(nome="admin")
    perfil_atendente = Perfil(nome="atendente")
    db.session.add_all([perfil_admin, perfil_atendente])
    db.session.commit()

    empresa = Empresa(nome="Clinica PDF")
    db.session.add(empresa)
    db.session.commit()

    unidade = Unidade(empresa_id=empresa.id, nome="Unidade Central")
    db.session.add(unidade)
    db.session.commit()

    fila = Fila(empresa_id=empresa.id, unidade_id=unidade.id, nome="Coleta Matinal", sigla="CM")
    db.session.add(fila)
    db.session.commit()

    admin = Usuario(empresa_id=empresa.id, nome="Admin PDF", login="admin_pdf")
    admin.set_senha("senha123")
    admin.perfis = [perfil_admin]
    db.session.add(admin)

    atendente = Usuario(empresa_id=empresa.id, nome="Atendente PDF", login="atendente_pdf")
    atendente.set_senha("senha123")
    atendente.perfis = [perfil_atendente]
    atendente.unidades = [unidade]
    db.session.add(atendente)
    db.session.commit()

    for i in range(3):
        db.session.add(Senha(
            empresa_id=empresa.id, unidade_id=unidade.id, fila_id=fila.id,
            codigo=f"CM00{i}", sequencial=i, prioridade=False, status="finalizada",
        ))
    db.session.commit()

    ids = dict(empresa=empresa.id, unidade=unidade.id, fila=fila.id, admin=admin.id)


def login_como(login):
    c = app.test_client()
    resp = c.post("/login", data={"login": login, "senha": "senha123"}, follow_redirects=True)
    assert resp.status_code == 200, (login, resp.status_code)
    return c


c_admin = login_como("admin_pdf")
c_atendente = login_como("atendente_pdf")

# ===========================================================================
# 1) PDF gerado de verdade: mimetype, Content-Disposition, assinatura %PDF.
# ===========================================================================
resp = c_admin.get(f"/relatorios/volume?formato=pdf&unidade_id={ids['unidade']}")
assert resp.status_code == 200, resp.status_code
assert resp.mimetype == "application/pdf"
assert 'volume_senhas.pdf' in resp.headers["Content-Disposition"]
corpo = resp.get_data()
assert corpo.startswith(b"%PDF"), corpo[:20]
ok("GET /relatorios/volume?formato=pdf gera um PDF de verdade (mimetype, nome de arquivo, assinatura %PDF)")

# ===========================================================================
# 2) Cabeçalho do PDF contém os filtros aplicados por extenso (não IDs crus).
# ===========================================================================
leitor = PdfReader(io.BytesIO(corpo))
texto_pdf = "".join(pagina.extract_text() or "" for pagina in leitor.pages)
assert "Unidade Central" in texto_pdf
assert "Unidade: Unidade Central" in texto_pdf
assert "Período:" in texto_pdf
assert "Volume de senhas" in texto_pdf  # título do relatório
assert "Coleta Matinal" in texto_pdf  # dado real da tabela, mesmo template da tela
ok("Cabeçalho do PDF traz os filtros aplicados por extenso (Unidade Central, Período) e o dado real da tabela")

# ===========================================================================
# 3) Log de auditoria gravado para os 3 formatos (CSV, Excel, PDF).
# ===========================================================================
with app.app_context():
    antes = LogExportacaoRelatorio.query.count()

resp_csv = c_admin.get(f"/relatorios/volume?formato=csv&unidade_id={ids['unidade']}")
assert resp_csv.status_code == 200
resp_xlsx = c_admin.get(f"/relatorios/volume?formato=xlsx&unidade_id={ids['unidade']}")
assert resp_xlsx.status_code == 200
resp_pdf2 = c_admin.get(f"/relatorios/volume?formato=pdf&unidade_id={ids['unidade']}")
assert resp_pdf2.status_code == 200

with app.app_context():
    depois = LogExportacaoRelatorio.query.count()
    assert depois - antes == 3, (antes, depois)

    ultimos_3 = (
        LogExportacaoRelatorio.query.order_by(LogExportacaoRelatorio.id.desc()).limit(3).all()
    )
    formatos_gravados = {log.formato for log in ultimos_3}
    assert formatos_gravados == {"csv", "xlsx", "pdf"}, formatos_gravados

    for log in ultimos_3:
        assert log.usuario_id == ids["admin"]
        assert log.empresa_id == ids["empresa"]
        assert log.tipo_relatorio == "volume_senhas"
        assert log.unidade_id == ids["unidade"]
ok("Log de auditoria (logs_exportacao_relatorio) gravado corretamente pros 3 formatos - CSV, Excel e PDF")

# ===========================================================================
# 4) RBAC: perfil sem acesso a relatórios recebe 403, mesmo pedindo PDF.
# ===========================================================================
resp = c_atendente.get("/relatorios/volume?formato=pdf")
assert resp.status_code == 403
ok("RBAC: perfil sem acesso a relatórios (atendente) recebe 403 também pedindo formato=pdf")

# ===========================================================================
# 5) Rodapé do PDF usa o FUSO DA CLÍNICA (agora_na_clinica()), não UTC do
#    servidor - mesmo bug de 3h já corrigido 3x neste projeto (10.37/10.39/
#    10.54). 2024-01-08 23:47 UTC = 20:47 em Brasília (UTC-3, sem DST desde
#    2019) - se o código usasse datetime.utcnow(), o rodapé mostraria "23:47".
# ===========================================================================
with patch("app.utils.tempo._instante_utc", return_value=datetime(2024, 1, 8, 23, 47, tzinfo=UTC)):
    resp_fuso = c_admin.get(f"/relatorios/volume?formato=pdf&unidade_id={ids['unidade']}")
assert resp_fuso.status_code == 200
texto_fuso = "".join(
    p.extract_text() or "" for p in PdfReader(io.BytesIO(resp_fuso.get_data())).pages
)
assert "20:47" in texto_fuso, texto_fuso
assert "23:47" not in texto_fuso, texto_fuso
ok("Rodapé 'Gerado em' usa o fuso da clínica (20:47), não a hora UTC do servidor (23:47) - mesmo bug do 10.37/10.39, corrigido aqui antes de vazar")

# ===========================================================================
# 6) Se renderizar_pdf() falhar, o log de auditoria NÃO é gravado (a
#    exportação não aconteceu de verdade - registrar isso como se tivesse
#    ocorrido seria impreciso pra um log de auditoria LGPD).
# ===========================================================================
with app.app_context():
    antes_falha = LogExportacaoRelatorio.query.count()

with patch("app.relatorios.routes.renderizar_pdf", side_effect=FalhaGeracaoPdf("falha simulada")):
    resp_falha = c_admin.get(f"/relatorios/volume?formato=pdf&unidade_id={ids['unidade']}", follow_redirects=True)
assert resp_falha.status_code == 200  # redirect seguido de volta pra tela normal

with app.app_context():
    depois_falha = LogExportacaoRelatorio.query.count()
    assert depois_falha == antes_falha, (antes_falha, depois_falha)
ok("Falha na geração do PDF (FalhaGeracaoPdf) NÃO grava log de auditoria - só exportação bem-sucedida é registrada")

print()
print(f"TODOS OS {len(passos)} TESTES DE EXPORTACAO EM PDF PASSARAM")
