"""
Teste de regressão: cartões "Espera total (todas as etapas)" e "Atendimento
atendente" no Dashboard (/dashboard, adendo 10.90).

Relógio fixo (patch de app.utils.tempo._instante_utc) em 08/10/2026 15:00 UTC
= 12:00 de Brasília: "hoje" é 08/10 da clínica.

Cobre:
1. Os 5 cartões de tempo, na ordem 3+2 por tipo de tempo (Espera inicial ·
   Espera total (todas as etapas) · Permanência total / Atendimento atendente
   · Atendimento executor), cada um com a dica (title) aprovada no cartão inteiro (aparece sobre o número).
2. Coerência com o Tempos por senha pelo próprio link "ver detalhes": média e
   mediana dos 5 cartões = resumo do relatório e, no polling, a quantidade de
   cada cartão = o "N de M" do relatório - hoje, com área, com unidade
   estreitada e em período de 2 dias.
3. Senha realocada atendente -> executor: Espera total (inicial +
   intermediária) > Espera inicial.
4. "N de M": senha só no executor (coleta) NÃO entra na média de Atendimento
   atendente - o zero de apresentação do 10.87 fica fora do Dashboard.
5. Polling: os 5 cartões só voltam no JSON quando o período é exatamente hoje.
6. RBAC inalterado: atendente vê os cartões só da própria unidade, sem
   escapar por querystring (página e polling), sem "ver detalhes".
7. Prova por mutação: trocar a chave de um cartão, trocar o valor de um cartão
   pelo de outra coluna ou deixar o zero de apresentação entrar no cálculo
   derruba as verificações acima.
8. Navegador (Chromium, JS real): um ciclo de polling atualiza os cartões
   novos só com período = hoje; layout 3+2 em 1366x768 e 1920x1080, temas
   claro e escuro, fonte padrão e larga, com o pior caso "10:12:25" sem
   estourar o cartão nem rolagem horizontal; tela estreita volta ao auto-fit;
   cores pelos tokens do tema.

Execução: python -m tests.test_regressao_dashboard_10_90 (a partir da raiz do
projeto, com o virtualenv ativado).
"""
import os

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

import html
import json
import re
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

from app import create_app
from app.extensions import db
from app.models import AreaFila, Empresa, Fila, Local, LogAtendimento, Perfil, Senha, Unidade, Usuario
from app.models.fila import DIAS_SEMANA
from app.relatorios.queries import TEMPOS_POR_SENHA
from app.relatorios.routes import zerar_atendimento_nao_aplicavel
import app.core.dashboard_queries as dashboard_queries
import app.core.routes as core_routes

app = create_app("testing")

passos = []


def ok(msg):
    passos.append(msg)
    print("OK:", msg)


AGORA_UTC = datetime(2026, 10, 8, 15, 0)  # 12:00 de Brasília
RELOGIO = patch("app.utils.tempo._instante_utc", return_value=AGORA_UTC.replace(tzinfo=timezone.utc))
RELOGIO.start()

SEMPRE_ABERTA = {dia: {"manha": True, "tarde": True} for dia in DIAS_SEMANA}
HOJE = datetime(2026, 10, 8, 13, 0)  # 10:00 de Brasília
ONTEM = datetime(2026, 10, 7, 13, 0)
DIA_N_DE_M = datetime(2026, 10, 6, 13, 0)  # só R e C: N de M do Atendimento atendente
DIA_REALOCADA = datetime(2026, 10, 5, 13, 0)  # só a realocada atendente -> executor

ORDEM = ["espera_inicial", "espera_total", "permanencia_total", "atendimento_atendente", "atendimento_executor"]
ROTULOS = {"espera_inicial": "Espera inicial", "espera_total": "Espera total (todas as etapas)",
           "permanencia_total": "Permanência total", "atendimento_atendente": "Atendimento atendente",
           "atendimento_executor": "Atendimento executor"}
DICAS = {
    "espera_inicial": "Da emissão da senha até a 1ª chamada.",
    "espera_total": ("Soma de todas as esperas da senha: a inicial e as intermediárias "
                     "(depois de realocação ou de \"Aguardar\")."),
    "permanencia_total": "Tempo real na clínica, da emissão da senha à finalização.",
    "atendimento_atendente": ("Tempo com o atendente (locais do tipo Atendente). "
                              "Só entram as senhas que passaram por essa etapa."),
    "atendimento_executor": ("Tempo na coleta/execução (locais do tipo Executor). "
                             "Só entram as senhas que passaram por essa etapa."),
}
NO_RELATORIO = {chave: rotulo for rotulo, chave in TEMPOS_POR_SENHA}  # rótulo do resumo do Tempos por senha

with app.app_context():
    db.create_all()
    perfis = {n: Perfil(nome=n) for n in ("admin", "atendente")}
    db.session.add_all(perfis.values())
    empresa = Empresa(nome="Clinica 1090")
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

    def nova_fila(unidade, nome, sigla, area):
        f = Fila(empresa_id=empresa.id, unidade_id=unidade.id, nome=nome, sigla=sigla, area_fila_id=area.id,
                 horario_funcionamento=dict(SEMPRE_ABERTA))
        db.session.add(f)
        db.session.commit()
        return f

    rec = nova_fila(matriz, "Recepção", "REC", area_lab)
    col = nova_fila(matriz, "Coleta", "COL", area_lab)
    img = nova_fila(matriz, "Imagem", "IMG", area_img)
    anx = nova_fila(anexo, "Anexo Atend", "ANA", area_anx)

    def novo_local(unidade, nome, tipo):
        l = Local(empresa_id=empresa.id, unidade_id=unidade.id, nome=nome, nome_exibicao=nome,
                  identificador_admin=f"[{unidade.sigla}] {nome}", tipo=tipo)
        db.session.add(l)
        db.session.commit()
        return l

    guiche = novo_local(matriz, "Guichê 1", "recepcao")  # tipo "Atendente" na interface (10.31)
    sala = novo_local(matriz, "Sala 1", "coleta")  # tipo "Executor"
    guiche_anx = novo_local(anexo, "Guichê A", "recepcao")

    def novo_usuario(login, perfil, unidades):
        u = Usuario(empresa_id=empresa.id, nome=login, login=login)
        u.set_senha("senha123")
        u.perfis = [perfis[perfil]]
        u.unidades = unidades
        db.session.add(u)
        db.session.commit()
        return u.id

    op = novo_usuario("admin_1090", "admin", [])
    novo_usuario("atendente_1090", "atendente", [matriz])

    seq = iter(range(1, 999))

    def senha(fila, criada, status, eventos=(), local=None):
        """eventos: (minutos desde a emissão, evento, fila, local)."""
        n = next(seq)
        s = Senha(empresa_id=empresa.id, unidade_id=fila.unidade_id, fila_id=fila.id, codigo=f"{fila.sigla}{n:03d}",
                  sequencial=n, status=status, criada_em=criada, local_id=local.id if local else None)
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

    def realocada(criada):
        """Atendente -> executor: espera inicial 10, atendente 5, espera intermediária 10, executor 10."""
        return senha(col, criada, "finalizada", local=sala, eventos=[
            (10, "chamada_senha", rec, guiche), (15, "realocado", rec, guiche),
            (25, "chamada_senha", col, sala), (35, "fim_atendimento", col, sala)])

    def so_atendente(criada, espera, atendimento, fila=rec, local=guiche):
        return senha(fila, criada, "finalizada", local=local, eventos=[
            (espera, "chamada_senha", fila, local), (espera + atendimento, "fim_atendimento", fila, local)])

    def so_executor(criada, espera, atendimento, fila=col):
        return senha(fila, criada, "finalizada", local=sala, eventos=[
            (espera, "chamada_senha", fila, sala), (espera + atendimento, "fim_atendimento", fila, sala)])

    m = lambda x: timedelta(minutes=x)
    # Hoje, Matriz
    realocada(HOJE)
    so_atendente(HOJE + m(5), 2, 6)
    so_executor(HOJE + m(1), 4, 12)
    senha(rec, HOJE + m(6), "finalizada", local=guiche, eventos=[  # "Aguardar" e chamada de novo
        (3, "chamada_senha", rec, guiche), (4, "voltou_aguardar", rec, guiche),
        (10, "chamada_senha", rec, guiche), (13, "fim_atendimento", rec, guiche)])
    senha(rec, AGORA_UTC - m(3), "aguardando")
    so_executor(HOJE + m(2), 7, 13, fila=img)  # área Imagem
    # Hoje, Anexo - valores grandes: se o escopo do atendente vazasse, as médias mudariam
    so_atendente(HOJE + m(2), 50, 70, fila=anx, local=guiche_anx)
    # Ontem
    so_atendente(ONTEM, 30, 10)
    realocada(ONTEM + m(20))
    # 06/10: R (atendente 6 min) e C (só executor) - Atendimento atendente = 1 de 2, média 06:00
    so_atendente(DIA_N_DE_M, 2, 6)
    so_executor(DIA_N_DE_M + m(1), 4, 12)
    # 05/10: só a realocada
    realocada(DIA_REALOCADA)

    ids = dict(empresa=empresa.id, matriz=matriz.id, anexo=anexo.id, area_lab=area_lab.id, area_anx=area_anx.id,
               rec=rec.id)


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


def cartoes(texto):
    """{chave: (média, mediana)} dos cartões de tempo da página."""
    return {chave: (kpi(texto, f"tempo-{chave}"), kpi(texto, f"tempo-{chave}-mediana")) for chave in ORDEM}


def url_polling(texto):
    return json.loads(re.search(r"const URL_KPIS = (\"[^\"]*\");", texto).group(1))


def link_detalhes(texto):
    m = re.search(r'id="link-tempos-detalhes" href="([^"]*)"', texto)
    return html.unescape(m.group(1)) if m else None


def resumo_relatorio(relatorio):
    """{chave: (média, mediana, N, M)} do resumo do Tempos por senha."""
    resumo = {}
    for chave in ORDEM:
        m = re.search(rf"<strong>{NO_RELATORIO[chave]}</strong>\s*<span>Média: ([^<]*)</span>\s*"
                      rf"<span>Mediana: ([^<]*)</span>\s*<span>(\d+) de (\d+) senhas</span>", relatorio)
        assert m, chave
        resumo[chave] = (m.group(1), m.group(2), int(m.group(3)), int(m.group(4)))
    return resumo


c_admin = login("admin_1090")
c_atd = login("atendente_1090")

# ===========================================================================
# 1) Os 5 cartões: ordem 3+2 por tipo de tempo, rótulos e dicas
# ===========================================================================
assert [c for c, *_ in dashboard_queries.TEMPOS_DASHBOARD] == ORDEM
texto = pagina(c_admin, "/dashboard?unidade_id=")
bloco = texto.split('id="kpis-tempos"', 1)[1].split('<div class="card">', 1)[0]
assert re.findall(r'id="kpi-tempo-([a-z_]+)">', bloco) == ORDEM, re.findall(r'id="kpi-tempo-([a-z_]+)">', bloco)
rotulos = re.findall(r'<div class="kpi-label">([^<]*)</div>', bloco)
assert [html.unescape(r) for r in rotulos] == [ROTULOS[c] for c in ORDEM], rotulos
# dica no cartão inteiro (o tooltip aparece também sobre o número), não só no rótulo
dicas = re.findall(r'<div class="kpi" title="([^"]*)">\s*<div class="kpi-numero" id="kpi-tempo-([a-z_]+)">', bloco)
assert [(html.unescape(d), c) for d, c in dicas] == [(DICAS[c], c) for c in ORDEM], dicas
assert "Recepção)" not in bloco and "Coleta)" not in bloco  # o tipo do Local é "Atendente"/"Executor" (10.31)
ok("5 cartões na ordem Espera inicial · Espera total (todas as etapas) · Permanência total / Atendimento atendente "
   "· Atendimento executor, com as dicas aprovadas (tipos de Local 'Atendente'/'Executor')")

# ===========================================================================
# 2) Coerência com o Tempos por senha pelo link "ver detalhes"
# ===========================================================================


def conferir_coerencia(url_dashboard, contexto, polling=False):
    texto = pagina(c_admin, url_dashboard)
    link = link_detalhes(texto)
    assert link, contexto
    resumo = resumo_relatorio(pagina(c_admin, link))
    valores = cartoes(texto)
    for chave in ORDEM:
        assert valores[chave] == resumo[chave][:2], (contexto, chave, valores[chave], resumo[chave])
    if polling:  # o JSON do polling traz a quantidade: = o N do "N de M"
        dados = c_admin.get(url_polling(texto)).get_json()
        for chave in ORDEM:
            t = dados["tempos"][chave]
            assert (t["media_texto"] or "—", t["mediana_texto"] or "—", t["quantidade"]) == resumo[chave][:3], (
                contexto, chave, t, resumo[chave])
    return valores, resumo


hoje_todas, resumo_hoje = conferir_coerencia("/dashboard?unidade_id=", "hoje, todas", polling=True)
assert hoje_todas["espera_total"][0] != "—" and hoje_todas["atendimento_atendente"][0] != "—"
conferir_coerencia(f"/dashboard?unidade_id=&area_fila_id={ids['area_lab']}", "hoje, área Laboratório", polling=True)
hoje_matriz, _ = conferir_coerencia(f"/dashboard?unidade_id={ids['matriz']}", "hoje, Matriz", polling=True)
conferir_coerencia("/dashboard?unidade_id=&data_inicio=2026-10-07&data_fim=2026-10-08", "07 a 08/10")
c_admin.get("/dashboard?unidade_id=")
ok("Coerência pelo 'ver detalhes': média e mediana dos 5 cartões = resumo do Tempos por senha, e a quantidade do "
   "polling = o N do 'N de M' (hoje, área, unidade estreitada e 2 dias)")

# ===========================================================================
# 3) Realocada atendente -> executor: Espera total > Espera inicial
# ===========================================================================
valores_rc, resumo_rc = conferir_coerencia("/dashboard?unidade_id=&data_inicio=2026-10-05&data_fim=2026-10-05",
                                           "05/10, só a realocada")
assert valores_rc["espera_inicial"] == ("10:00", "10:00"), valores_rc
assert valores_rc["espera_total"] == ("20:00", "20:00"), valores_rc  # 10 inicial + 10 depois da realocação
assert valores_rc["atendimento_atendente"] == ("05:00", "05:00") and valores_rc["atendimento_executor"] == ("10:00", "10:00")
assert valores_rc["permanencia_total"] == ("35:00", "35:00"), valores_rc
ok("Realocada atendente -> executor: Espera total 20:00 > Espera inicial 10:00; atendente 05:00, executor 10:00, "
   "permanência 35:00")

# ===========================================================================
# 4) "N de M": senha só no executor não entra no Atendimento atendente
# ===========================================================================
URL_N_DE_M = "/dashboard?unidade_id=&data_inicio=2026-10-06&data_fim=2026-10-06"


def conferir_n_de_m():
    valores, resumo = conferir_coerencia(URL_N_DE_M, "06/10, R + C")
    assert resumo["atendimento_atendente"][2:] == (1, 2), resumo["atendimento_atendente"]
    assert resumo["atendimento_executor"][2:] == (1, 2), resumo["atendimento_executor"]
    assert valores["atendimento_atendente"] == ("06:00", "06:00"), valores  # com o zero do 10.87 seria 03:00
    assert valores["atendimento_executor"] == ("12:00", "12:00"), valores


conferir_n_de_m()
with app.app_context():  # o mesmo número direto da função, sem a tela
    periodo = dashboard_queries.montar_periodo(ids["empresa"], {ids["matriz"], ids["anexo"]},
                                               datetime(2026, 10, 6).date(), datetime(2026, 10, 6).date())
    tempos_06 = dashboard_queries.calcular_tempos(periodo.filtros)["tempos"]
assert tempos_06["atendimento_atendente"]["quantidade"] == 1 and tempos_06["atendimento_atendente"]["media_segundos"] == 360
ok("N de M: a senha só no executor fica fora do Atendimento atendente (1 de 2, média 06:00 - não 03:00 com o zero "
   "de apresentação do 10.87), e a só no atendente fora do executor (1 de 2, 12:00)")

# ===========================================================================
# 5) Polling: tempos só com período = exatamente hoje
# ===========================================================================
dados_hoje = c_admin.get(url_polling(texto)).get_json()
assert sorted(dados_hoje["tempos"]) == sorted(ORDEM), dados_hoje["tempos"]
texto_2dias = pagina(c_admin, "/dashboard?unidade_id=&data_inicio=2026-10-07&data_fim=2026-10-08")
assert "const INCLUI_HOJE = true;" in texto_2dias and 'id="tempos-calculado-em"' in texto_2dias
assert "tempos" not in c_admin.get(url_polling(texto_2dias)).get_json()
texto_ontem = pagina(c_admin, "/dashboard?unidade_id=&data_inicio=2026-10-07&data_fim=2026-10-07")
assert "const INCLUI_HOJE = false;" in texto_ontem
assert "tempos" not in c_admin.get(url_polling(texto_ontem)).get_json()
assert kpi(texto_ontem, "tempo-espera_total") not in (None, "—")  # calculado no carregamento
ok("Polling: os 5 cartões voltam no JSON só com período = hoje; 07 a 08/10 e só 07/10 ficam com o valor do "
   "carregamento ('calculado às')")

# ===========================================================================
# 6) RBAC: atendente só a própria unidade
# ===========================================================================
texto_atd = pagina(c_atd, "/dashboard")
assert cartoes(texto_atd) == hoje_matriz, (cartoes(texto_atd), hoje_matriz)
assert hoje_matriz["espera_total"] != hoje_todas["espera_total"]  # o Anexo mudaria os números
assert hoje_matriz["atendimento_atendente"] != hoje_todas["atendimento_atendente"]
assert link_detalhes(texto_atd) is None
texto_atd_esc = pagina(c_atd, f"/dashboard?unidade_id={ids['anexo']}&area_fila_id={ids['area_anx']}")
assert cartoes(texto_atd_esc) == hoje_matriz
dados_atd = c_atd.get(f"/api/dashboard/kpis?unidade_id={ids['anexo']}&area_fila_id={ids['area_anx']}").get_json()
assert {c: (dados_atd["tempos"][c]["media_texto"] or "—", dados_atd["tempos"][c]["mediana_texto"] or "—")
        for c in ORDEM} == hoje_matriz, dados_atd["tempos"]
ok("RBAC: atendente vê os 5 cartões só da Matriz (= admin estreitado na Matriz, diferente de todas as unidades), "
   "sem 'ver detalhes'; Anexo na URL e no polling ignorado")

# ===========================================================================
# 7) Prova por mutação
# ===========================================================================


def deve_falhar(descricao, verificacao):
    try:
        verificacao()
    except AssertionError:
        return
    raise AssertionError(f"mutação não detectada: {descricao}")


def coerencia_hoje():
    conferir_coerencia("/dashboard?unidade_id=", "mutação", polling=True)


trocada = tuple(("espera_inicial", *resto) if chave == "espera_total" else (chave, *resto)
                for chave, *resto in dashboard_queries.TEMPOS_DASHBOARD)
with patch.object(dashboard_queries, "TEMPOS_DASHBOARD", trocada), patch.object(core_routes, "TEMPOS_DASHBOARD", trocada):
    deve_falhar("cartão Espera total com a chave da Espera inicial", coerencia_hoje)

calcular_original = dashboard_queries.calcular_tempos


def valor_de_outra_coluna(filtros):
    dados = calcular_original(filtros)
    dados["tempos"]["atendimento_atendente"] = dados["tempos"]["atendimento_executor"]
    return dados


with patch.object(dashboard_queries, "calcular_tempos", valor_de_outra_coluna):
    deve_falhar("Atendimento atendente com o valor do Atendimento executor", coerencia_hoje)

tempos_original = dashboard_queries.tempos_por_senha


def com_zero_de_apresentacao(filtros):
    linhas, etapas = tempos_original(filtros)
    return [zerar_atendimento_nao_aplicavel(l) for l in linhas], etapas


with patch.object(dashboard_queries, "tempos_por_senha", com_zero_de_apresentacao):
    deve_falhar("zero de apresentação do 10.87 no cálculo dos cartões", conferir_n_de_m)
conferir_coerencia("/dashboard?unidade_id=", "depois das mutações", polling=True)
ok("Mutação: chave trocada, valor de outra coluna e zero de apresentação no cálculo - cada uma derruba a "
   "verificação; sem mutação, volta a passar")

# ===========================================================================
# 8) Navegador: polling, layout 3+2, pior caso, tela estreita, tokens
# ===========================================================================
BASE = "http://gestfila.teste"
FONTE_LARGA = "body, input, button, select { font-family: Verdana, 'DejaVu Sans', sans-serif !important; }"


def abrir(navegador, cliente, url, tema="light", largura=1366, altura=768):
    pg = navegador.new_page(viewport={"width": largura, "height": altura})
    pg.add_init_script(f"localStorage.setItem('gestfila_tema', '{tema}')")

    def responder(route, request):
        partes = urlsplit(request.url)
        r = cliente.get(partes.path + (f"?{partes.query}" if partes.query else ""))
        route.fulfill(status=r.status_code, headers={"content-type": r.headers.get("Content-Type", "text/html")},
                      body=r.get_data())

    pg.route(f"{BASE}/**", responder)
    pg.goto(BASE + url)
    return pg


def cartoes_na_pagina(pg):
    return {c: (pg.inner_text(f"#kpi-tempo-{c}"), pg.inner_text(f"#kpi-tempo-{c}-mediana")) for c in ORDEM}


MEDIR = """(pior) => {
  const r = (e) => e.getBoundingClientRect();
  if (pior) document.querySelectorAll('#kpis-tempos .kpi-numero').forEach(n => n.textContent = '10:12:25');
  const cartao = (c) => document.getElementById('kpi-tempo-' + c).closest('.kpi');
  const ordem = %s;
  const grade = document.getElementById('kpis-tempos');
  const conteudo = r(document.getElementById('kpis-volume'));
  const token = (nome, prop) => {
    const sonda = document.createElement('div'); sonda.style[prop] = 'var(' + nome + ')';
    document.body.appendChild(sonda); const v = getComputedStyle(sonda)[prop]; sonda.remove(); return v;
  };
  const primeiro = cartao(ordem[0]);
  return {
    pagina: document.documentElement.scrollWidth, janela: innerWidth,
    colunas: getComputedStyle(grade).gridTemplateColumns.split(' ').length,
    topos: ordem.map(c => Math.round(r(cartao(c)).top)),
    esquerdas: ordem.map(c => Math.round(r(cartao(c)).left)),
    larguras: ordem.map(c => Math.round(r(cartao(c)).width)),
    direita_max: Math.max(...ordem.map(c => r(cartao(c)).right)), conteudo_direita: conteudo.right,
    estouro: ordem.filter(c => { const n = document.getElementById('kpi-tempo-' + c); return n.scrollWidth > n.clientWidth + 1; }),
    alturas_numero: ordem.map(c => Math.round(r(document.getElementById('kpi-tempo-' + c)).height)),
    fim_tempos: Math.round(r(grade).bottom),
    cor_numero: getComputedStyle(document.getElementById('kpi-tempo-' + ordem[0])).color,
    cor_rotulo: getComputedStyle(primeiro.querySelector('.kpi-label')).color,
    dica_sobre_numero: ordem.map(c => (document.getElementById('kpi-tempo-' + c).closest('[title]') || {}).title),
    fundo: getComputedStyle(primeiro).backgroundColor,
    token_primaria: token('--gf-primaria', 'color'), token_texto2: token('--gf-texto-secundario', 'color'),
    token_superficie: token('--gf-superficie', 'backgroundColor'),
    tema: document.documentElement.getAttribute('data-theme'),
  };
}""" % json.dumps(ORDEM)

with sync_playwright() as pw:
    navegador = pw.chromium.launch()

    # Polling: só o período = hoje atualiza os cartões de tempo
    pg_hoje = abrir(navegador, c_admin, "/dashboard?unidade_id=")
    pg_2dias = abrir(navegador, c_admin, "/dashboard?unidade_id=&data_inicio=2026-10-07&data_fim=2026-10-08")
    antes_hoje, antes_2dias = cartoes_na_pagina(pg_hoje), cartoes_na_pagina(pg_2dias)
    geradas_2dias = int(pg_2dias.inner_text("#kpi-geradas"))
    with app.app_context():
        e = db.session.get(Empresa, ids["empresa"])
        f = db.session.get(Fila, ids["rec"])
        g_local = Local.query.filter_by(nome="Guichê 1").first()
        # local_id na senha: o atendimento final é classificado pelo local ATUAL da senha (10.3/10.83)
        s = Senha(empresa_id=e.id, unidade_id=f.unidade_id, fila_id=f.id, codigo="REC900", sequencial=900,
                  status="finalizada", criada_em=HOJE + timedelta(minutes=30), local_id=g_local.id)
        db.session.add(s)
        db.session.commit()
        for minutos, evento in ((0, "gerada"), (40, "chamada_senha"), (60, "fim_atendimento")):
            db.session.add(LogAtendimento(senha_id=s.id, evento=evento, unidade_id=s.unidade_id, fila_id=f.id,
                                          usuario_id=op, local_id=None if evento == "gerada" else g_local.id,
                                          timestamp=s.criada_em + timedelta(minutes=minutos)))
        db.session.commit()
    for pg in (pg_hoje, pg_2dias):
        with pg.expect_response(lambda r: "/api/dashboard/kpis" in r.url):
            pg.evaluate("window.__atualizarKpisDashboard()")
    pg_2dias.wait_for_function(f"document.getElementById('kpi-geradas').textContent === '{geradas_2dias + 1}'")
    esperado = cartoes(pagina(c_admin, "/dashboard?unidade_id="))
    pg_hoje.wait_for_function(
        f"document.getElementById('kpi-tempo-espera_total').textContent === {json.dumps(esperado['espera_total'][0])}")
    depois_hoje = cartoes_na_pagina(pg_hoje)
    assert depois_hoje == esperado, (depois_hoje, esperado)
    assert depois_hoje["espera_total"] != antes_hoje["espera_total"]
    assert depois_hoje["atendimento_atendente"] != antes_hoje["atendimento_atendente"]
    assert cartoes_na_pagina(pg_2dias) == antes_2dias  # contagem atualizou, tempos não
    pg_hoje.close()
    pg_2dias.close()
    ok("JS real: um ciclo de polling atualiza Espera total e Atendimento atendente (= recarregar a página) só com "
       "período = hoje; em 07 a 08/10 a contagem atualiza e os tempos ficam")

    # Layout 3+2 nas 8 combinações + pior caso
    tokens = {}
    for largura, altura in ((1366, 768), (1920, 1080)):
        for tema in ("light", "dark"):
            for fonte in (None, FONTE_LARGA):
                for pior in (False, True):
                    pg = abrir(navegador, c_admin, "/dashboard?unidade_id=", tema, largura, altura)
                    if fonte:
                        pg.add_style_tag(content=fonte)
                    med = pg.evaluate(MEDIR, pior)
                    contexto = (largura, tema, bool(fonte), pior, med)
                    t = med["topos"]
                    assert med["tema"] == tema and med["pagina"] <= med["janela"], contexto  # sem rolagem horizontal
                    assert med["colunas"] == 3, contexto
                    assert t[0] == t[1] == t[2] < t[3] == t[4], contexto  # 3+2
                    e = med["esquerdas"]
                    assert e[0] < e[1] < e[2] and e[3] == e[0] and e[4] == e[1], contexto
                    assert max(med["larguras"]) - min(med["larguras"]) <= 1, contexto  # colunas iguais
                    assert med["direita_max"] <= med["conteudo_direita"] + 1, contexto  # não passa da borda
                    assert not med["estouro"] and len(set(med["alturas_numero"])) == 1, contexto  # número numa linha
                    assert med["cor_numero"] == med["token_primaria"], contexto
                    assert med["cor_rotulo"] == med["token_texto2"], contexto
                    assert med["dica_sobre_numero"] == [DICAS[c] for c in ORDEM], contexto  # tooltip sobre o número
                    assert med["fundo"] == med["token_superficie"], contexto
                    if largura == 1366:
                        assert med["fim_tempos"] <= altura, contexto  # cabe na primeira tela
                    tokens[tema] = med["token_superficie"]
                    if not pior:
                        print(f"   {largura}x{altura} {tema:5s} {'larga ' if fonte else 'padrão'}: cartões de "
                              f"{med['larguras'][0]}px, linha de tempos termina em {med['fim_tempos']}px")
                    pg.close()
    assert tokens["light"] != tokens["dark"]
    ok("Layout 3+2 em 1366x768 e 1920x1080, claro e escuro, fonte padrão e larga, com e sem o pior caso "
       "'10:12:25': colunas iguais, número numa linha, nada passa da borda, sem rolagem horizontal, cabe na "
       "primeira tela em 1366x768; número, rótulo e fundo pelos tokens do tema")

    pg = abrir(navegador, c_admin, "/dashboard?unidade_id=", "light", 600, 900)
    med = pg.evaluate(MEDIR, True)
    # A página em si rola ~29 px em 600 px por causa do link "Sair (...)" da barra do topo (anterior ao 10.90,
    # registrado no adendo); aqui se confere só a grade de tempos.
    assert med["colunas"] < 3 and med["direita_max"] <= med["janela"] and not med["estouro"], med
    pg.close()
    ok(f"Tela estreita (600 px): volta ao auto-fit ({med['colunas']} colunas), cartões dentro da tela, sem estouro")
    navegador.close()

RELOGIO.stop()
print(f"\nTODOS OS {len(passos)} TESTES DOS CARTÕES DE ESPERA TOTAL E ATENDIMENTO ATENDENTE (10.90) PASSARAM")
