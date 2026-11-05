"""
Teste de regressão: melhorias do Dashboard (/dashboard, adendo 10.86).

Relógio fixo (patch de app.utils.tempo._instante_utc) em 08/10/2026 15:00 UTC
= 12:00 de Brasília: "hoje" é 08/10 da clínica.

Cobre:
1. Contagem por SENHA: senha realocada e senha chamada de novo depois de
   "Aguardar" contam 1 em "Senhas geradas" e em "Atendidas (das geradas)"; o
   cartão "Chamadas hoje" (e "Tempo médio de espera", "Filas ativas") saiu.
2. Filtro de data no dia civil da clínica (virada às 00h de Brasília, não às
   21h), período sem hoje (sem "agora", sem polling), inversão De > Até,
   recorte acima de 31 dias com aviso, data inválida vira hoje.
3. Área: filtra cartões, lista de filas e últimos atendimentos; área fora do
   escopo (outra unidade / atendente) é descartada.
4. RBAC/escopo: atendente só a própria unidade, sem escapar por querystring
   (página e polling); "ver detalhes" só para admin/gerencial.
5. Polling: mesmos filtros, unidade EXPLÍCITA (trocar a unidade em outra aba
   não muda esta, e o polling não grava a sessão); tempos e Prioritários x
   Normal só quando o período é exatamente hoje.
6. Coerência com o Tempos por senha pelo próprio link "ver detalhes":
   "Senhas geradas" = total do relatório; "Atendidas (das geradas)" =
   Concluída + Histórico parcial do CSV; média/mediana dos 3 cartões = resumo
   do relatório - hoje, com área e com unidade estreitada.
7. "Aguardando acima do tempo máximo" (regra do 10.52) e Prioritários x Normal
   por Senha.prioridade, com a nota de 04/11/2026.
8. Últimos atendimentos: só do período/área, fila e local do retrato do log
   (chamada na recepção de senha realocada sai como recepção), colunas Tipo e
   Área, só o primeiro nome, Observações nunca.
9. Navegador (Chromium, JS real): polling atualiza os cartões; gráfico com a
   cor do token nos temas claro e escuro; 1366x768 sem rolagem horizontal,
   linhas de cartões e tabela cabendo, com fonte padrão e larga.

Execução: python -m tests.test_regressao_dashboard_10_86 (a partir da raiz do
projeto, com o virtualenv ativado).
"""
import os

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

import csv
import html
import io
import json
import re
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

from app import create_app
from app.extensions import db
from app.models import AreaFila, Empresa, Fila, Local, LogAtendimento, Perfil, Senha, TipoSenha, Unidade, Usuario
from app.models.fila import DIAS_SEMANA

app = create_app("testing")

passos = []


def ok(msg):
    passos.append(msg)
    print("OK:", msg)


AGORA_UTC = datetime(2026, 10, 8, 15, 0)  # 12:00 de Brasília
RELOGIO = patch("app.utils.tempo._instante_utc", return_value=AGORA_UTC.replace(tzinfo=timezone.utc))
RELOGIO.start()

SEMPRE_ABERTA = {dia: {"manha": True, "tarde": True} for dia in DIAS_SEMANA}
T0 = datetime(2026, 10, 8, 13, 0)  # 10:00 de Brasília
OBS = "em jejum, pedido incompleto"

with app.app_context():
    db.create_all()
    perfis = {n: Perfil(nome=n) for n in ("admin", "gerencial", "atendente")}
    db.session.add_all(perfis.values())
    empresa = Empresa(nome="Clinica 1086")
    db.session.add(empresa)
    db.session.commit()
    matriz = Unidade(empresa_id=empresa.id, nome="Matriz", sigla="MAT")
    anexo = Unidade(empresa_id=empresa.id, nome="Anexo", sigla="ANX")
    db.session.add_all([matriz, anexo])
    db.session.commit()
    area_lab = AreaFila(empresa_id=empresa.id, unidade_id=matriz.id, nome="Laboratório")
    area_img = AreaFila(empresa_id=empresa.id, unidade_id=matriz.id, nome="Imagem")
    area_anx = AreaFila(empresa_id=empresa.id, unidade_id=anexo.id, nome="Anexo Geral")
    db.session.add_all([area_lab, area_img, area_anx])
    db.session.commit()

    def nova_fila(unidade, nome, sigla, area, limite=None):
        f = Fila(empresa_id=empresa.id, unidade_id=unidade.id, nome=nome, sigla=sigla, area_fila_id=area.id,
                 tempo_espera_maximo_min=limite, horario_funcionamento=dict(SEMPRE_ABERTA))
        db.session.add(f)
        db.session.commit()
        return f

    rec = nova_fila(matriz, "Recepção", "REC", area_lab, limite=10)
    col = nova_fila(matriz, "Coleta", "COL", area_lab)
    img = nova_fila(matriz, "Imagem", "IMG", area_img)
    anx = nova_fila(anexo, "Anexo Atend", "ANA", area_anx)

    def novo_local(unidade, nome, tipo):
        l = Local(empresa_id=empresa.id, unidade_id=unidade.id, nome=nome, nome_exibicao=nome,
                  identificador_admin=f"[{unidade.sigla}] {nome}", tipo=tipo)
        db.session.add(l)
        db.session.commit()
        return l

    guiche = novo_local(matriz, "Guichê 1", "recepcao")
    sala = novo_local(matriz, "Sala 1", "coleta")
    guiche_anx = novo_local(anexo, "Guichê A", "recepcao")

    idoso = TipoSenha(empresa_id=empresa.id, nome="Pessoa idosa", prefixo="PI", cor="#880808", peso_prioridade=2)
    db.session.add(idoso)
    db.session.commit()

    def novo_usuario(login, perfil, unidades):
        u = Usuario(empresa_id=empresa.id, nome=login, login=login)
        u.set_senha("senha123")
        u.perfis = [perfis[perfil]]
        u.unidades = unidades
        db.session.add(u)
        db.session.commit()
        return u.id

    op = novo_usuario("admin_1086", "admin", [])
    novo_usuario("gerencial_1086", "gerencial", [matriz])
    novo_usuario("atendente_1086", "atendente", [matriz])

    seq = iter(range(1, 999))

    def senha(fila, criada, status, eventos=(), local=None, **extra):
        n = next(seq)
        s = Senha(empresa_id=empresa.id, unidade_id=fila.unidade_id, fila_id=fila.id, codigo=f"{fila.sigla}{n:03d}",
                  sequencial=n, status=status, criada_em=criada, local_id=local.id if local else None,
                  prioridade=extra.pop("prioridade", False), **extra)
        db.session.add(s)
        db.session.commit()
        db.session.add(LogAtendimento(senha_id=s.id, evento="gerada", unidade_id=s.unidade_id, timestamp=criada,
                                      fila_id=eventos[0][2].id if eventos else fila.id))
        for minutos, evento, f, l in eventos:
            db.session.add(LogAtendimento(senha_id=s.id, evento=evento, unidade_id=s.unidade_id, usuario_id=op,
                                          timestamp=criada + timedelta(minutes=minutos), fila_id=f.id,
                                          local_id=l.id if l else None))
        db.session.commit()
        return s.codigo

    m = lambda x: timedelta(minutes=x)
    # Hoje (08/10 da clínica), Matriz
    S1 = senha(col, T0, "finalizada", local=sala, prioridade=True, tipo_senha_id=idoso.id,
               nome_paciente="Maria Aparecida Souza", observacoes=OBS, eventos=[
                   (0, "chamada_senha", rec, guiche) if False else (10, "chamada_senha", rec, guiche),
                   (15, "realocado", rec, guiche), (25, "chamada_senha", col, sala), (35, "fim_atendimento", col, sala)])
    S2 = senha(rec, T0 + m(5), "finalizada", local=guiche, eventos=[
        (6, "chamada_senha", rec, guiche), (7, "voltou_aguardar", rec, guiche),
        (15, "chamada_senha", rec, guiche), (25, "fim_atendimento", rec, guiche)])
    S3 = senha(rec, AGORA_UTC - m(15), "aguardando")  # 15 min > limite de 10 da Recepção
    S4 = senha(rec, AGORA_UTC - m(5), "aguardando")
    S5 = senha(img, AGORA_UTC - m(60), "aguardando")  # Imagem sem limite: nunca "acima"
    S6 = senha(img, T0 + m(1), "chamada", local=sala, eventos=[(39, "chamada_senha", img, sala)])
    # 08/10 00:10 de Brasília (03:10 UTC): já é HOJE na clínica
    P2 = senha(rec, datetime(2026, 10, 8, 3, 10), "finalizada", local=guiche, eventos=[
        (2, "chamada_senha", rec, guiche), (5, "fim_atendimento", rec, guiche)])
    # Hoje, Anexo
    S7 = senha(anx, T0 + m(2), "finalizada", local=guiche_anx, eventos=[
        (3, "chamada_senha", anx, guiche_anx), (6, "fim_atendimento", anx, guiche_anx)])
    # 07/10 23:50 de Brasília (08/10 02:50 UTC): ONTEM na clínica (em UTC já seria 08/10)
    P1 = senha(rec, datetime(2026, 10, 8, 2, 50), "finalizada", local=guiche, eventos=[
        (2, "chamada_senha", rec, guiche), (5, "fim_atendimento", rec, guiche)])
    # Bem antes (para o recorte de 31 dias)
    P3 = senha(rec, datetime(2026, 8, 20, 13, 0), "finalizada", local=guiche, eventos=[
        (2, "chamada_senha", rec, guiche), (5, "fim_atendimento", rec, guiche)])

    ids = dict(empresa=empresa.id, matriz=matriz.id, anexo=anexo.id, area_lab=area_lab.id, area_anx=area_anx.id,
               area_img=area_img.id)


def login(nome):
    c = app.test_client()
    resp = c.post("/login", data={"login": nome, "senha": "senha123"})
    assert resp.status_code == 302, nome
    return c


def pagina(c, url):
    resp = c.get(url)
    assert resp.status_code == 200, (url, resp.status_code)
    return resp.get_data(as_text=True)


def kpi(texto, nome):
    m = re.search(rf'id="kpi-{nome}">([^<]*)<', texto)
    return m.group(1) if m else None


def url_polling(texto):
    return json.loads(re.search(r"const URL_KPIS = (\"[^\"]*\");", texto).group(1))


def link_detalhes(texto):
    m = re.search(r'id="link-tempos-detalhes" href="([^"]*)"', texto)
    return html.unescape(m.group(1)) if m else None


c_admin = login("admin_1086")
c_ger = login("gerencial_1086")
c_atd = login("atendente_1086")

# ===========================================================================
# 1) Contagem por senha
# ===========================================================================
texto = pagina(c_admin, "/dashboard?unidade_id=")
assert kpi(texto, "geradas") == "8", kpi(texto, "geradas")  # S1..S7 + P2 (00h10 de hoje)
assert kpi(texto, "atendidas") == "4", kpi(texto, "atendidas")  # S1 (realocada), S2 (Aguardar), S7, P2
assert "Atendidas (das geradas)" in texto
for antigo in ("kpi-chamadas-hoje", "Chamadas hoje", "kpi-tempo-espera\"", "Tempo médio de espera", "kpi-filas-ativas"):
    assert antigo not in texto, antigo
ok("Por senha: 8 geradas, 4 atendidas - a realocada (2 chamadas) e a do 'Aguardar' (2 chamadas) contam 1 cada; "
   "'Chamadas hoje', 'Tempo médio de espera' e 'Filas ativas' saíram")

# ===========================================================================
# 2) Datas: dia da clínica, sem hoje, inversão, recorte, inválida
# ===========================================================================
texto_ontem = pagina(c_admin, "/dashboard?data_inicio=2026-10-07&data_fim=2026-10-07")
assert kpi(texto_ontem, "geradas") == "1", kpi(texto_ontem, "geradas")  # só P1 (23h50 de 07/10)
assert 'id="kpi-aguardando"' not in texto_ontem and 'id="kpi-acima-maximo"' not in texto_ontem
assert "const INCLUI_HOJE = false;" in texto_ontem and "aguardando</span>" not in texto_ontem
assert re.search(r'id="tempos-calculado-em">calculado às 12:00<', texto_ontem)
ok("07/10: 1 senha (a das 23h50 de Brasília, que em UTC já é 08/10); sem cartões de 'agora', sem polling, "
   "'calculado às 12:00'")

texto_inv = pagina(c_admin, "/dashboard?data_inicio=2026-10-08&data_fim=2026-10-07")
assert kpi(texto_inv, "geradas") == "9" and "07/10/2026 a 08/10/2026" in texto_inv
assert 'value="2026-10-07"' in texto_inv and 'value="2026-10-08"' in texto_inv
assert "Senhas geradas por dia" in texto_inv and 'id="kpi-aguardando"' in texto_inv  # inclui hoje
ok("De > Até inverte (07/10 a 08/10: 9 senhas, gráfico por dia, inclui hoje)")

texto_rec = pagina(c_admin, "/dashboard?data_inicio=2026-08-01&data_fim=2026-10-08")
assert "Período maior que 31 dias: mostrando 08/09/2026 a 08/10/2026." in texto_rec
assert kpi(texto_rec, "geradas") == "9"  # P3 (20/08) fica fora do recorte
assert 'value="2026-09-08"' in texto_rec
ok("Acima de 31 dias: recorta para os 31 dias que terminam em 'Até' (08/09 a 08/10), com aviso; P3 fora")

texto_inv_data = pagina(c_admin, "/dashboard?data_inicio=xx&data_fim=2026-13-40")
assert kpi(texto_inv_data, "geradas") == "8" and 'value="2026-10-08"' in texto_inv_data
assert "Senhas geradas por hora" in texto_inv_data
ok("Data inválida vira hoje (8 senhas, gráfico por hora)")

# ===========================================================================
# 3) Área
# ===========================================================================
texto_lab = pagina(c_admin, f"/dashboard?area_fila_id={ids['area_lab']}")
assert kpi(texto_lab, "geradas") == "5", kpi(texto_lab, "geradas")  # S1 (Coleta), S2, S3, S4, P2
assert kpi(texto_lab, "aguardando") == "2" and kpi(texto_lab, "acima-maximo") == "1"
assert "[MAT] Imagem" not in texto_lab.split("<h2>Filas", 1)[1].split("</div>\n      </div>", 1)[0]
ultimos_lab = texto_lab.split('id="tabela-ultimos"', 1)[1]
assert "IMG" not in ultimos_lab and "ANA" not in ultimos_lab
ok("Área Laboratório: 5 senhas, 2 aguardando (1 acima do máximo); lista de filas e últimos atendimentos só da área")

c_admin.get(f"/dashboard?unidade_id={ids['matriz']}")
texto_area_fora = pagina(c_admin, f"/dashboard?area_fila_id={ids['area_anx']}")
assert kpi(texto_area_fora, "geradas") == "7" and 'selected>[ANX]' not in texto_area_fora  # área do Anexo descartada
assert "[ANX] Anexo Geral" not in texto_area_fora  # nem aparece como opção
c_admin.get("/dashboard?unidade_id=")
ok("Admin estreitado na Matriz: Área do Anexo descartada (7 senhas da Matriz) e fora das opções")

# ===========================================================================
# 4) RBAC / escopo
# ===========================================================================
texto_atd = pagina(c_atd, "/dashboard")
assert kpi(texto_atd, "geradas") == "7" and 'id="contexto-unidade"' not in texto_atd
assert link_detalhes(texto_atd) is None
texto_atd_esc = pagina(c_atd, f"/dashboard?unidade_id={ids['anexo']}&area_fila_id={ids['area_anx']}")
assert kpi(texto_atd_esc, "geradas") == "7" and "Anexo" not in texto_atd_esc.split("<main>", 1)[1]
dados_atd = c_atd.get(f"/api/dashboard/kpis?unidade_id={ids['anexo']}&area_fila_id={ids['area_anx']}").get_json()
assert dados_atd["geradas"] == 7, dados_atd
texto_ger = pagina(c_ger, "/dashboard")
assert kpi(texto_ger, "geradas") == "8" and link_detalhes(texto_ger)
ok("Atendente: só a Matriz (7), sem seletor de unidade e sem 'ver detalhes'; unidade/área do Anexo na URL e no "
   "polling ignoradas; gerencial vê a empresa (8) e o link")

# ===========================================================================
# 5) Polling: filtros iguais, unidade explícita, sessão intacta, tempos só hoje
# ===========================================================================
texto_mat = pagina(c_admin, f"/dashboard?unidade_id={ids['matriz']}")
url_mat = url_polling(texto_mat)
assert f"unidade_id={ids['matriz']}" in url_mat and "data_inicio=2026-10-08" in url_mat, url_mat
pagina(c_admin, f"/dashboard?unidade_id={ids['anexo']}")  # "outra aba" troca a unidade da sessão
dados = c_admin.get(url_mat).get_json()
assert dados["geradas"] == 7 and dados["aguardando"] == 3 and dados["acima_maximo"] == 1, dados
assert kpi(pagina(c_admin, "/dashboard"), "geradas") == "1"  # a sessão continua no Anexo: o polling não gravou
assert "tempos" in dados and "prioritarios" in dados  # período = exatamente hoje
c_admin.get("/dashboard?unidade_id=")

url_2dias = url_polling(texto_inv)
dados_2dias = c_admin.get(url_2dias).get_json()
assert dados_2dias["geradas"] == 9 and "tempos" not in dados_2dias and dados_2dias["aguardando"] == 3
url_lab = url_polling(texto_lab)
assert c_admin.get(url_lab).get_json()["geradas"] == 5
ok("Polling: URL leva datas/área/unidade explícitas; trocar a unidade em outra aba não muda esta e o polling não "
   "grava a sessão; tempos só quando o período é exatamente hoje")

# ===========================================================================
# 6) Coerência com o Tempos por senha (pelo link "ver detalhes")
# ===========================================================================
ROTULOS = {"espera_inicial": "Espera inicial", "atendimento_executor": "Atendimento executor",
           "permanencia_total": "Permanência total"}


def conferir_coerencia(url_dashboard, contexto):
    texto = pagina(c_admin, url_dashboard)
    link = link_detalhes(texto)
    assert link, contexto
    relatorio = pagina(c_admin, link)
    total = int(re.search(r"<span>\d+ de (\d+) senhas</span>", relatorio).group(1))
    assert kpi(texto, "geradas") == str(total), (contexto, kpi(texto, "geradas"), total)
    for chave, rotulo in ROTULOS.items():
        m = re.search(rf"<strong>{rotulo}</strong>\s*<span>Média: ([^<]*)</span>\s*<span>Mediana: ([^<]*)</span>", relatorio)
        assert m, (contexto, rotulo)
        assert kpi(texto, f"tempo-{chave}") == m.group(1), (contexto, rotulo, kpi(texto, f"tempo-{chave}"), m.group(1))
        assert kpi(texto, f"tempo-{chave}-mediana") == m.group(2), (contexto, rotulo)
    bruto = c_admin.get(link + "&formato=csv").get_data().decode("utf-8-sig")
    situacoes = [linha["Situação"] for linha in csv.DictReader(io.StringIO(bruto), delimiter=";")]
    assert len(situacoes) == total, contexto
    atendidas = sum(1 for s in situacoes if s in ("Concluída", "Histórico parcial"))
    assert kpi(texto, "atendidas") == str(atendidas), (contexto, kpi(texto, "atendidas"), atendidas)
    return link


link_hoje = conferir_coerencia("/dashboard?unidade_id=", "hoje, todas as unidades")
assert "unidade_id" not in link_hoje
link_lab = conferir_coerencia(f"/dashboard?area_fila_id={ids['area_lab']}", "hoje, área Laboratório")
assert f"area_fila_id={ids['area_lab']}" in link_lab
link_mat = conferir_coerencia(f"/dashboard?unidade_id={ids['matriz']}", "hoje, Matriz")
assert f"unidade_id={ids['matriz']}" in link_mat
conferir_coerencia(f"/dashboard?unidade_id=&data_inicio=2026-10-07&data_fim=2026-10-08", "07 a 08/10")
c_admin.get("/dashboard?unidade_id=")
ok("Coerência pelo link 'ver detalhes': geradas = total do Tempos por senha, atendidas = Concluída + Histórico "
   "parcial do CSV, média e mediana dos 3 cartões = resumo do relatório (hoje, área, unidade, 2 dias)")

# ===========================================================================
# 7) Acima do tempo máximo + Prioritários x Normal
# ===========================================================================
assert kpi(texto, "aguardando") == "3" and kpi(texto, "acima-maximo") == "1"
assert 'class="kpi-numero alerta" id="kpi-acima-maximo"' in texto
dados_hoje = c_admin.get(url_polling(texto)).get_json()
prio = dados_hoje["prioritarios"]
assert prio["prioritario"]["geradas"] == 1 and prio["normal"]["geradas"] == 7, prio
assert prio["prioritario"]["media_texto"] == "10:00", prio  # S1: 10 min da emissão à 1ª chamada
assert 'id="prio-prioritario-geradas">1<' in texto and 'id="prio-normal-geradas">7<' in texto
assert "04/11/2026" in texto and "Não é falta de dado prioritário" in texto
texto_p1 = pagina(c_admin, "/dashboard?data_inicio=2026-10-07&data_fim=2026-10-07")
assert 'id="prio-prioritario-geradas">0<' in texto_p1 and 'id="prio-normal-geradas">1<' in texto_p1
ok("Acima do tempo máximo: 1 (Recepção 15 min > 10; Imagem sem limite nunca entra); Prioritários x Normal por "
   "Senha.prioridade (1 x 7, espera 10:00), nota de 04/11/2026; período antigo sai todo como Normal")

# ===========================================================================
# 8) Últimos atendimentos
# ===========================================================================
ultimos = texto.split('id="tabela-ultimos"', 1)[1].split("</table>", 1)[0]
assert "<th>Tipo</th><th>Fila</th><th>Área</th>" in ultimos
linhas_s1 = [l for l in re.findall(r"<tr>.*?</tr>", ultimos, re.S) if f"<td>{S1}</td>" in l]
assert len(linhas_s1) == 2, linhas_s1
assert any("[MAT] Recepção" in l and "[MAT] Guichê 1" in l for l in linhas_s1), linhas_s1  # retrato do log
assert any("[MAT] Coleta" in l and "[MAT] Sala 1" in l for l in linhas_s1), linhas_s1
assert all("[MAT] Laboratório" in l and "Pessoa idosa" in l and "tipo-senha-marcador" in l for l in linhas_s1)
assert "<td>Maria</td>" in ultimos and "Aparecida" not in texto and OBS not in texto and "jejum" not in texto
assert P1 not in ultimos  # chamada de 07/10: fora do período "hoje"
ok("Últimos atendimentos: do período, fila/local do retrato do log (chamada na Recepção de senha realocada sai "
   "como Recepção), Tipo e Área, só 'Maria', Observações nunca")

# ===========================================================================
# 9) Navegador: polling, cor do gráfico pelo token, 1366x768
# ===========================================================================
BASE = "http://gestfila.teste"
FONTE_LARGA = "body, input, button, select { font-family: Verdana, 'DejaVu Sans', sans-serif !important; }"


def abrir(navegador, cliente, url, tema):
    pg = navegador.new_page(viewport={"width": 1366, "height": 768})
    pg.add_init_script(f"localStorage.setItem('gestfila_tema', '{tema}')")

    def responder(route, request):
        partes = urlsplit(request.url)
        r = cliente.get(partes.path + (f"?{partes.query}" if partes.query else ""))
        route.fulfill(status=r.status_code, headers={"content-type": r.headers.get("Content-Type", "text/html")},
                      body=r.get_data())

    pg.route(f"{BASE}/**", responder)
    pg.goto(BASE + url)
    return pg


with sync_playwright() as pw:
    navegador = pw.chromium.launch()

    pg = abrir(navegador, c_admin, "/dashboard?unidade_id=", "light")
    assert pg.inner_text("#kpi-geradas") == "8"
    with app.app_context():
        e = db.session.get(Empresa, ids["empresa"])
        fila_rec = Fila.query.filter_by(sigla="REC").first()
        db.session.add(Senha(empresa_id=e.id, unidade_id=fila_rec.unidade_id, fila_id=fila_rec.id, codigo="REC900",
                             sequencial=900, status="aguardando", criada_em=AGORA_UTC - m(20), prioridade=True))
        db.session.commit()
    with pg.expect_response(lambda r: "/api/dashboard/kpis" in r.url):
        pg.evaluate("window.__atualizarKpisDashboard()")
    pg.wait_for_function("document.getElementById('kpi-geradas').textContent === '9'")
    assert pg.inner_text("#kpi-aguardando") == "4" and pg.inner_text("#kpi-acima-maximo") == "2"
    assert pg.inner_text("#prio-prioritario-geradas") == "2"
    pg.close()
    ok("JS real: um ciclo de polling atualiza geradas (9), aguardando (4), acima do máximo (2) e prioritários (2)")

    for tema in ("light", "dark"):
        for fonte in (None, FONTE_LARGA):
            pg = abrir(navegador, c_admin, "/dashboard?unidade_id=", tema)
            if fonte:
                pg.add_style_tag(content=fonte)
            med = pg.evaluate("""() => {
              const topo = (sel) => [...document.querySelectorAll(sel)].map(e => Math.round(e.getBoundingClientRect().top));
              const tab = document.getElementById('tabela-ultimos'); const card = tab.closest('.card');
              const est = getComputedStyle(card);
              const util = card.clientWidth - parseFloat(est.paddingLeft) - parseFloat(est.paddingRight);
              const prio = document.getElementById('tabela-prioritarios'); const cardP = prio.closest('.card');
              const estP = getComputedStyle(cardP);
              const utilP = cardP.clientWidth - parseFloat(estP.paddingLeft) - parseFloat(estP.paddingRight);
              const grafico = Chart.getChart(document.getElementById('grafico-senhas'));
              return {
                pagina: document.documentElement.scrollWidth, altura: document.documentElement.scrollHeight,
                volume: new Set(topo('#kpis-volume .kpi')).size, n_volume: topo('#kpis-volume .kpi').length,
                tempos: new Set(topo('#kpis-tempos .kpi')).size,
                tabela: tab.scrollWidth <= util + 1, prio: prio.scrollWidth <= utilP + 1,
                cor: grafico.data.datasets[0].backgroundColor,
                token: getComputedStyle(document.documentElement).getPropertyValue('--gf-primaria').trim(),
                tema: document.documentElement.getAttribute('data-theme'),
                fim_tempos: Math.round(document.getElementById('kpis-tempos').getBoundingClientRect().bottom),
              };
            }""")
            contexto = (tema, bool(fonte), med)
            assert med["tema"] == tema and med["pagina"] <= 1366, contexto
            assert med["n_volume"] == 4 and med["volume"] == 1 and med["tempos"] == 1, contexto  # cada linha numa linha só
            assert med["tabela"] and med["prio"], contexto
            assert med["cor"] == med["token"] and med["cor"], contexto
            if not fonte:
                print(f"   medida 1366x768 tema {tema}: página {med['altura']}px de altura; "
                      f"cartões de volume e tempos terminam em {med['fim_tempos']}px")
            pg.close()
    ok("1366x768, temas claro e escuro, fonte padrão e larga: sem rolagem horizontal, 4 cartões de volume e 3 de "
       "tempo cada um numa linha, tabelas cabendo; cor do gráfico = --gf-primaria do tema")
    navegador.close()

RELOGIO.stop()
print(f"\n{len(passos)} verificações OK")
