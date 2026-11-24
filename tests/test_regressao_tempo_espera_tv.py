"""
Teste de regressão do adendo 10.88: "Tempo de espera" no rodapé do painel de
TV - mediana da Espera inicial (emissão -> 1ª chamada) das senhas chamadas na
última hora, nas filas da TV, só nas TVs com `mostra_tempo_espera`.

Cobre: relógio simulado (virada do dia, abertura, pico, calmo, fim do dia);
equivalência com a coluna Espera inicial do Tempos por senha; senha realocada
contando na fila onde esperou (snapshot do segmento), não na fila atual; N
mínimo (4 esconde, 5 mostra); extremo não move a mediana; faixas de texto e
seus limites; escopo (TVs, empresas, inativa/slug errado 404, JSON só com
"texto", opção desligada sem elemento nem fetch); cache de 60 s por escopo;
rate limit 429; payload de `entrou`/`chamada` inalterado; JS e layout no
Chromium (1366x768/1920x1080, claro/escuro); prova por mutação (fila atual em
vez da fila do segmento; média em vez de mediana).

Execução: python -m tests.test_regressao_tempo_espera_tv (a partir da raiz do
projeto, com o virtualenv ativado).
"""
import json
import os
import re
import statistics
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

from playwright.sync_api import sync_playwright

from app import create_app
from app.atendimento.helpers import _payload_chamada
from app.config import TestingConfig, config_by_name
from app.extensions import db, limiter, socketio
from app.models import Empresa, Fila, LogAtendimento, Perfil, Senha, Tv, Unidade, Usuario
from app.painel import tempo_espera
from app.painel.tempo_espera import (
    MINIMO_ESPERAS_TV,
    esperas_na_janela,
    limpar_cache,
    mediana_espera_tv,
    texto_espera_tv,
    texto_tempo_espera,
)
from app.relatorios.helpers import Filtros
from app.relatorios.queries import tempos_por_senha
from app.utils.tempo import janela_dia_clinica_em_utc

app = create_app("testing")
passos = []


def ok(msg):
    passos.append(msg)
    print("OK:", msg)


FUSO = ZoneInfo("America/Sao_Paulo")
DIA = datetime(2026, 10, 8)  # quinta-feira


def local(h, m=0, dia=0):
    """Hora de Brasília do DIA (+dia) -> UTC naive (base dos logs/senhas)."""
    return (DIA.replace(tzinfo=FUSO) + timedelta(days=dia, hours=h, minutes=m)).astimezone(timezone.utc).replace(tzinfo=None)


# Relógio do servidor simulado (seam de app/utils/tempo.py).
RELOGIO = {"agora": local(10)}
patch("app.utils.tempo._instante_utc", side_effect=lambda: RELOGIO["agora"].replace(tzinfo=timezone.utc)).start()


def relogio(h, m=0, s=0, dia=0):
    RELOGIO["agora"] = local(h, m, dia) + timedelta(seconds=s)


with app.app_context():
    db.create_all()
    emp_a, emp_b = Empresa(nome="Clinica A"), Empresa(nome="Clinica B")
    db.session.add_all([emp_a, emp_b])
    db.session.commit()
    uni_a, uni_b = Unidade(empresa_id=emp_a.id, nome="Matriz"), Unidade(empresa_id=emp_b.id, nome="Outra")
    db.session.add_all([uni_a, uni_b])
    db.session.commit()

    def nova_fila(emp, uni, nome):
        f = Fila(empresa_id=emp.id, unidade_id=uni.id, nome=nome)
        db.session.add(f)
        db.session.commit()
        return f

    REC = nova_fila(emp_a, uni_a, "Recepcao")
    RES = nova_fila(emp_a, uni_a, "Resultado")
    COL = nova_fila(emp_a, uni_a, "Coleta")
    FB = nova_fila(emp_b, uni_b, "Fila B")

    def nova_tv(emp, uni, slug, filas, mostra=True, ativo=True):
        tv = Tv(empresa_id=emp.id, unidade_id=uni.id, nome=slug, slug=slug, mostra_tempo_espera=mostra, ativo=ativo)
        tv.filas = filas
        db.session.add(tv)
        db.session.commit()
        return tv.id

    ID = {
        "terreo": nova_tv(emp_a, uni_a, "terreo", [REC, RES, COL]),
        "espelho": nova_tv(emp_a, uni_a, "espelho", [REC, RES, COL]),  # mesmo escopo (cache)
        "coleta": nova_tv(emp_a, uni_a, "coleta", [COL]),              # só fila alimentada por realocação
        "desligada": nova_tv(emp_a, uni_a, "desligada", [REC, RES, COL], mostra=False),
        "inativa": nova_tv(emp_a, uni_a, "inativa", [REC], ativo=False),
        "b": nova_tv(emp_b, uni_b, "b", [FB]),
        # TV da empresa B com uma fila da A pendurada (defensivo: o cadastro
        # não deixa, mas o cálculo também filtra por tv.empresa_id).
        "b_intrusa": nova_tv(emp_b, uni_b, "b-intrusa", [REC]),
    }
    F = {"REC": REC.id, "RES": RES.id, "COL": COL.id, "FB": FB.id}

    seq = [0]

    def senha(fila, emitida, chamada, emp=emp_a, uni=uni_a, realocar_para=None, realocar_em=None):
        """Senha com 'gerada' e 1ª chamada (snapshot da fila da espera); com
        realocar_para, segue para outra fila (fila ATUAL muda) e é chamada lá."""
        seq[0] += 1
        s = Senha(empresa_id=emp.id, unidade_id=uni.id, fila_id=fila.id, codigo=f"S{seq[0]:03d}",
                  sequencial=seq[0], status="finalizada", criada_em=emitida, chamada_em=chamada)
        db.session.add(s)
        db.session.flush()
        logs = [("gerada", emitida, fila), ("chamada_senha", chamada, fila)]
        if realocar_para is not None:
            logs += [("realocado", realocar_em, fila), ("chamada_nome", realocar_em + timedelta(minutes=20), realocar_para)]
            s.fila_id = realocar_para.id
        else:
            logs.append(("fim_atendimento", chamada + timedelta(minutes=5), fila))
        for evento, quando, f in logs:
            db.session.add(LogAtendimento(senha_id=s.id, evento=evento, timestamp=quando, unidade_id=uni.id, fila_id=f.id))
        return s

    def esp(fila, h_cham, m_cham, espera_min, **kw):
        chamada = local(h_cham, m_cham)
        return senha(fila, chamada - timedelta(minutes=espera_min), chamada, **kw)

    # --- virada do dia: 23:50 de Brasília do dia anterior = 02:50 UTC do DIA
    #     (mesmo dia em UTC, outro dia na clínica) -> não entra; 00:05/00:10 entram.
    senha(REC, local(23, 50, dia=-1), local(0, 5))     # espera 15, emitida ONTEM na clínica
    senha(REC, local(23, 20, dia=-1), local(23, 55, dia=-1))
    for i, espera in enumerate((8, 9, 10, 11, 12)):
        esp(REC, 0, 15 + 2 * i, espera)

    # --- pico (janela 09:00-10:00): REC + RES, com um extremo (140 min) e uma
    #     senha realocada REC -> COL (espera inicial de 30 min na REC).
    for m, espera in ((5, 20), (12, 22), (20, 25), (28, 25), (35, 26), (44, 28), (52, 140)):
        esp(REC, 9, m, espera)
    REALOCADA = esp(REC, 9, 30, 30, realocar_para=COL, realocar_em=local(9, 40))
    esp(RES, 9, 15, 1)
    esp(RES, 9, 40, 1)

    # --- calmo: 4 chamadas 12:20-12:50 e a 5ª às 13:10.
    for m, espera in ((20, 2), (30, 3), (40, 3), (50, 4)):
        esp(REC, 12, m, espera)
    esp(REC, 13, 10, 3)

    # --- tarde/fim: últimas chamadas às 17:00-17:30.
    for m in (0, 10, 20, 30):
        esp(REC, 17, m, 12)

    # --- empresa B: esperas próprias (longas) às 09:xx.
    for m in (10, 20, 30, 40, 50):
        esp(FB, 9, m, 50, emp=emp_b, uni=uni_b)
    db.session.commit()
    REALOCADA_ID = REALOCADA.id
    EMP_A, EMP_B, UNI_A = emp_a.id, emp_b.id, uni_a.id


def tv(nome):
    return db.session.get(Tv, ID[nome])


def minutos(valores):
    return sorted(round(v / 60, 4) for v in valores)


# ===========================================================================
# 1) Texto e seus limites
# ===========================================================================
casos = {
    None: None, 0: "menos de 5 min", 299: "menos de 5 min", 299.4: "menos de 5 min",
    299.5: "~5 min",  # meio para cima (segundos_arredondados) -> 300 s
    300: "~5 min", 449: "~5 min", 450: "~10 min", 1530: "~25 min", 1650: "~30 min",
    3449: "~55 min", 3450: "~60 min", 3600: "~60 min", 3601: "mais de 1 hora", 8400: "mais de 1 hora",
}
for segundos, esperado in casos.items():
    assert texto_tempo_espera(segundos) == esperado, (segundos, texto_tempo_espera(segundos), esperado)
ok("Texto: <5 min 'menos de 5 min' (4:59), 5:00 '~5', 7:29 '~5', 7:30 '~10' (meio para cima), 60:00 '~60', 60:01 'mais de 1 hora'")


# ===========================================================================
# 2) Relógio simulado: virada do dia/abertura, pico, calmo, fim do dia
# ===========================================================================
def verificar_cenarios():
    with app.app_context():
        limpar_cache()
        relogio(0, 30)
        assert minutos(esperas_na_janela(tv("terreo"))) == [8, 9, 10, 11, 12], minutos(esperas_na_janela(tv("terreo")))
        assert texto_espera_tv(tv("terreo")) == "~10 min"

        limpar_cache()
        relogio(10)
        valores = esperas_na_janela(tv("terreo"))
        assert minutos(valores) == [1, 1, 20, 22, 25, 25, 26, 28, 30, 140], minutos(valores)
        assert mediana_espera_tv(tv("terreo")) == 25 * 60, mediana_espera_tv(tv("terreo"))
        assert texto_espera_tv(tv("terreo")) == "~25 min"
        # a realocada esperou na REC: não conta na TV da Coleta (fila atual)
        assert esperas_na_janela(tv("coleta")) == [], esperas_na_janela(tv("coleta"))
        assert texto_espera_tv(tv("coleta")) is None

        limpar_cache()
        relogio(13)
        assert len(esperas_na_janela(tv("terreo"))) == 4 and texto_espera_tv(tv("terreo")) is None
        limpar_cache()
        relogio(13, 15)
        assert len(esperas_na_janela(tv("terreo"))) == 5 and texto_espera_tv(tv("terreo")) == "menos de 5 min"

        limpar_cache()
        relogio(19)
        assert esperas_na_janela(tv("terreo")) == [] and texto_espera_tv(tv("terreo")) is None


verificar_cenarios()
ok("00:30: senha emitida 23:50 da noite anterior (02:50 UTC do mesmo dia) fora; as 5 de hoje -> '~10 min'")
ok("10:00 (pico): 10 esperas, mediana 25:00 -> '~25 min'; realocada REC->COL conta na REC, TV só da Coleta sem número")
ok(f"Calmo: 13:00 com 4 esperas esconde, 13:15 com 5 mostra 'menos de 5 min' (MINIMO_ESPERAS_TV={MINIMO_ESPERAS_TV})")
ok("19:00 (fim do dia): nenhuma chamada na última hora -> None")

with app.app_context():
    limpar_cache()
    relogio(10)
    sem_extremo = sorted(esperas_na_janela(tv("terreo")))[:-1]
    assert statistics.median(sem_extremo) == mediana_espera_tv(tv("terreo")) == 25 * 60
    assert statistics.fmean(sem_extremo) != statistics.fmean(esperas_na_janela(tv("terreo")))
ok("Extremo de 140 min não move a mediana (25:00 com e sem ele; a média mudaria)")


# ===========================================================================
# 3) Equivalência com a coluna Espera inicial do Tempos por senha
# ===========================================================================
with app.app_context():
    limpar_cache()
    relogio(23)
    inicio, fim = janela_dia_clinica_em_utc()
    filtros = Filtros(empresa_id=EMP_A,
                      unidades_permitidas=None, unidade_id=None, fila_id=None, operador_id=None,
                      data_inicio=inicio, data_fim_exclusivo=fim, data_inicio_str="", data_fim_str="")
    linhas, _ = tempos_por_senha(filtros)
    filas_tv = {F["REC"], F["RES"], F["COL"]}
    relatorio = sorted(l["espera_inicial_segundos"] for l in linhas
                       if l["espera_inicial_segundos"] is not None and l["fila_inicial_id"] in filas_tv)
    _, esperas = tempo_espera._esperas_iniciais_do_escopo(
        EMP_A, frozenset({UNI_A}), inicio, fim, RELOGIO["agora"])
    nossas = sorted(s for fila_id, _, s in esperas if fila_id in filas_tv)
    assert nossas == relatorio and len(nossas) == 24, (len(nossas), len(relatorio))
    realocada = next(l for l in linhas if l["senha_id"] == REALOCADA_ID)
    assert realocada["fila_inicial_id"] == F["REC"] and realocada["fila_final_id"] == F["COL"]
    assert realocada["espera_inicial_segundos"] == 30 * 60
ok("Mesmas 24 esperas (segundos) da coluna Espera inicial do Tempos por senha no dia; realocada: fila inicial REC, final COL")


# ===========================================================================
# 4) Escopo: TVs, empresas, opção, endpoint
# ===========================================================================
cliente = app.test_client()
with app.app_context():
    limpar_cache()
    relogio(10)
    assert texto_espera_tv(tv("b")) == "~50 min"
    assert esperas_na_janela(tv("b_intrusa")) == [] and texto_espera_tv(tv("b_intrusa")) is None
    assert texto_espera_tv(tv("desligada")) is None
    assert esperas_na_janela(tv("desligada")) != []  # há dado - só a opção esconde
ok("Isolamento: empresa B só vê a própria fila ('~50 min'); fila da A pendurada numa TV da B é ignorada; opção desligada -> None")

relogio(10)
resp = cliente.get("/painel/tv/terreo/espera")
assert resp.status_code == 200 and resp.get_json() == {"texto": "~25 min"}, resp.get_data(as_text=True)
assert set(json.loads(resp.get_data(as_text=True))) == {"texto"}
assert resp.headers["Cache-Control"] == "no-store"
assert cliente.get("/painel/tv/coleta/espera").get_json() == {"texto": None}
assert cliente.get("/painel/tv/desligada/espera").get_json() == {"texto": None}
assert cliente.get("/painel/tv/inativa/espera").status_code == 404
assert cliente.get("/painel/tv/nao-existe/espera").status_code == 404
ok("Endpoint: só {'texto'} (no-store); sem amostra/desligada -> null; TV inativa e slug inexistente -> 404")

html_on = cliente.get("/painel/tv/terreo").get_data(as_text=True)
html_off = cliente.get("/painel/tv/desligada").get_data(as_text=True)
html_inativa = cliente.get("/painel/tv/inativa").get_data(as_text=True)
assert '<span id="tempo-espera" hidden></span>' in html_on and "/painel/tv/terreo/espera" in html_on
assert 'id="contador-chamadas"' not in html_on
for html in (html_off, html_inativa):
    assert "tempo-espera" not in html.replace("#tempo-espera[hidden]", "") and "/espera" not in html
assert "Esta TV está desativada" in html_inativa and "<footer>" not in html_inativa


def rodape(html):
    trecho = html[html.index("<footer>"):html.index("</footer>") + len("</footer>")]
    return " ".join(re.sub(r"<!--.*?-->", "", trecho, flags=re.S).split())


# Rodapé de antes do 10.88 (HEAD), literal - a TV desligada não pode mudar.
RODAPE_ANTES = ('<footer> <span class="rodape-mensagem">Tenha em mãos seu documento e sua carteirinha.</span> '
                '<span id="contador-chamadas">0 chamadas hoje</span> '
                '<span id="ultima-atualizacao">Última atualização: —</span> </footer>')
assert rodape(html_off) == RODAPE_ANTES, rodape(html_off)
assert rodape(html_on) == RODAPE_ANTES.replace(
    '<span id="contador-chamadas">0 chamadas hoje</span>', '<span id="tempo-espera" hidden></span>'), rodape(html_on)
ok("HTML: opção ligada troca o contador pelo span hidden (com fetch); desligada com o rodapé idêntico ao de antes; "
   "indisponivel.html sem elemento nem fetch")


# ===========================================================================
# 5) Cache: um cálculo por escopo a cada 60 s, recortado por TV
# ===========================================================================
original = tempo_espera._segmentos_espera_por_senha
chamadas = []


def contador(filtros):
    chamadas.append(filtros.empresa_id)
    return original(filtros)


with app.app_context(), patch("app.painel.tempo_espera._segmentos_espera_por_senha", side_effect=contador):
    limpar_cache()
    relogio(10)
    assert texto_espera_tv(tv("terreo")) == "~25 min"
    assert texto_espera_tv(tv("espelho")) == "~25 min"
    assert texto_espera_tv(tv("coleta")) is None  # mesmo escopo, recorte diferente
    assert len(chamadas) == 1, chamadas
    relogio(10, 0, 59)
    texto_espera_tv(tv("espelho"))
    assert len(chamadas) == 1, chamadas
    relogio(10, 1, 1)
    texto_espera_tv(tv("terreo"))
    assert len(chamadas) == 2, chamadas
    texto_espera_tv(tv("b"))
    assert chamadas == [EMP_A, EMP_A, EMP_B], chamadas
ok("Cache: 3 TVs do mesmo escopo = 1 cálculo; reaproveitado aos 59 s, recalculado aos 61 s; outra empresa = escopo próprio")


# ===========================================================================
# 7) Payload de entrou/chamada inalterado (opção ligada x desligada)
# ===========================================================================
relogio(10)
with app.app_context():
    recebido = {}
    for nome in ("terreo", "desligada"):
        ws = socketio.test_client(app, namespace="/painel")
        ws.get_received("/painel")
        ws.emit("entrar_tv", {"tv_id": ID[nome]}, namespace="/painel")
        evento = next(r for r in ws.get_received("/painel") if r["name"] == "entrou")
        recebido[nome] = evento["args"][0]
        ws.disconnect(namespace="/painel")
    on, off = recebido["terreo"], recebido["desligada"]
    assert set(on) == set(off) == {"tv_id", "salas", "historico", "total_hoje"}, set(on)
    assert on["historico"] == off["historico"] and on["total_hoje"] == off["total_hoje"] and on["historico"]
    for item in on["historico"]:
        assert set(item) == {"tipo", "local", "senha", "local_audio_slug"}, item
    assert set(_payload_chamada(db.session.get(Senha, REALOCADA_ID))) == {"tipo", "local", "senha", "local_audio_slug"}
ok("Payload: 'entrou' com as mesmas 4 chaves e o mesmo histórico com a opção ligada ou desligada; 'chamada' com as chaves de sempre")


# ===========================================================================
# 8) Prova por mutação
# ===========================================================================
def mutacao_falha(nome, alvo, substituto):
    with patch(alvo, substituto):
        try:
            verificar_cenarios()
        except AssertionError:
            ok(f"Mutação '{nome}' derruba o teste")
            return
    raise AssertionError(f"mutação '{nome}' passou despercebida")


def pela_fila_atual(filtros):
    segmentos = original(filtros)
    atual = dict(db.session.query(Senha.id, Senha.fila_id))
    return [(a, f, atual[a.senha_id], u) for a, f, _fila, u in segmentos]


mutacao_falha("fila atual da senha em vez da fila do segmento",
              "app.painel.tempo_espera._segmentos_espera_por_senha", pela_fila_atual)
mutacao_falha("média em vez de mediana", "app.painel.tempo_espera.median", statistics.fmean)
verificar_cenarios()  # sem mutação, volta a passar


# ===========================================================================
# 9) Chromium: JS (fetch, null esconde, 10 min sem resposta esconde, sem
#    fetch com a opção desligada) e layout (1366/1920, claro/escuro)
# ===========================================================================
STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "static")
STUB_SOCKET_IO = """(function(){const H={};window.io=function(){return{on:(e,f)=>(H[e]=H[e]||[]).push(f),emit:()=>{}}};
window.__emitir=(e,d)=>(H[e]||[]).forEach(f=>f(d));})();"""

MEDIR_RODAPE = r"""() => {
  const footer = document.querySelector('footer');
  const s = getComputedStyle(footer);
  const itens = [...footer.children].filter(el => !el.hidden);
  const larguras = itens.reduce((t, el) => t + el.getBoundingClientRect().width, 0);
  const lum = (cor) => { const [r,g,b] = cor.match(/[\d.]+/g).slice(0,3).map(v => { v = v/255; return v <= 0.03928 ? v/12.92 : Math.pow((v+0.055)/1.055, 2.4); });
    return 0.2126*r + 0.7152*g + 0.0722*b; };
  const el = document.getElementById('tempo-espera');
  const cs = getComputedStyle(el);
  const [a, b] = [lum(cs.color), lum(s.backgroundColor)].sort((x,y) => y-x);
  return {
    itens: itens.length, fonte_item: cs.fontSize, fonte_footer: s.fontSize,
    uma_linha: itens.every(i => i.getBoundingClientRect().height <= parseFloat(s.fontSize) * 1.5),
    folga: Math.round(footer.clientWidth - larguras - parseFloat(s.paddingLeft) - parseFloat(s.paddingRight)),
    estouro: document.documentElement.scrollWidth > innerWidth || footer.scrollWidth > footer.clientWidth,
    contraste: (a + 0.05) / (b + 0.05), ordem: itens.map(i => i.id || i.className), familia: cs.fontFamily,
  };
}"""


def preparar(pagina, html, resposta_espera):
    def estatico(rota):
        caminho = os.path.join(STATIC, rota.request.url.split("/static/", 1)[1].split("?")[0])
        if os.path.isfile(caminho):
            rota.fulfill(path=caminho)
        else:
            rota.fulfill(body="", status=200)

    pagina.route("http://painel.test/static/**", estatico)
    pagina.route("**/socket.io.min.js*", lambda r: r.fulfill(body=STUB_SOCKET_IO, content_type="application/javascript"))
    pagina.route("http://painel.test/painel/tv/*/espera", resposta_espera)
    pagina.route("http://painel.test/painel/tv/terreo", lambda r: r.fulfill(body=html, content_type="text/html"))
    pagina.route("http://painel.test/painel/tv/desligada", lambda r: r.fulfill(body=html, content_type="text/html"))


# Fonte larga forçada (mesmo recurso do 10.84/10.86): Verdana no Windows,
# DejaVu Sans no runner Linux - a TV real pode ter fonte/zoom diferentes.
FONTE_LARGA = "html, body { font-family: Verdana, 'DejaVu Sans', sans-serif !important; }"
FOLGA_MINIMA_RODAPE_PX = 40

def responder_com(texto):
    corpo = json.dumps({"texto": texto})
    return lambda rota: rota.fulfill(body=corpo, content_type="application/json")


medidas = {}
with sync_playwright() as pw:
    nav = pw.chromium.launch()

    # --- JS do rodapé com relógio do navegador controlado
    estado = {"modo": "texto", "texto": "~15 min", "pedidos": 0}

    def responder(rota):
        estado["pedidos"] += 1
        if estado["modo"] == "falha":
            rota.abort()
        else:
            rota.fulfill(body=json.dumps({"texto": estado["texto"] if estado["modo"] == "texto" else None}),
                         content_type="application/json")

    pg = nav.new_page(viewport={"width": 1366, "height": 768})
    pg.clock.install()
    preparar(pg, html_on, responder)
    pg.goto("http://painel.test/painel/tv/terreo")
    visivel = "() => !document.getElementById('tempo-espera').hidden"
    pg.wait_for_function(visivel)
    assert pg.text_content("#tempo-espera") == "Espera: ~15 min"
    assert estado["pedidos"] == 1
    estado["modo"] = "null"
    pg.clock.run_for(2 * 60 * 1000)
    pg.wait_for_function("() => document.getElementById('tempo-espera').hidden")
    estado.update(modo="texto", texto="mais de 1 hora")
    pg.clock.run_for(2 * 60 * 1000)
    pg.wait_for_function(visivel)
    assert pg.text_content("#tempo-espera") == "Espera: mais de 1 hora"
    assert estado["pedidos"] == 3
    estado["modo"] = "falha"
    pg.clock.run_for(8 * 60 * 1000)  # 4 falhas, 8 min desde a última resposta válida
    pg.wait_for_timeout(200)
    assert pg.evaluate(visivel), "escondeu antes de 10 min sem resposta"
    pg.clock.run_for(4 * 60 * 1000)  # 12 min sem resposta válida
    pg.wait_for_function("() => document.getElementById('tempo-espera').hidden")
    assert estado["pedidos"] >= 7
    pg.close()
    ok("JS: fetch ao abrir e a cada 2 min; null esconde; volta ao receber texto; falhas mantêm até 10 min e escondem depois")

    pedidos_off = []
    pg = nav.new_page()
    preparar(pg, html_off, lambda r: (pedidos_off.append(r.request.url), r.fulfill(body='{"texto": "~5 min"}')))
    pg.goto("http://painel.test/painel/tv/desligada")
    pg.wait_for_timeout(500)
    assert pedidos_off == [] and pg.evaluate("document.getElementById('tempo-espera')") is None
    pg.close()
    ok("JS: opção desligada não faz nenhum fetch nem cria o elemento")

    # --- chamada ao vivo (WebSocket) com e sem o contador na tela: card,
    #     lista e mensagem da lista vazia (10.27) iguais, sem erro de JS.
    for nome, html in (("terreo", html_on), ("desligada", html_off)):
        erros = []
        pg = nav.new_page(viewport={"width": 1366, "height": 768})
        pg.on("pageerror", lambda e: erros.append(str(e)))
        preparar(pg, html, responder_com(None))
        pg.goto(f"http://painel.test/painel/tv/{nome}")
        vazio = "() => getComputedStyle(document.getElementById('mensagem-vazio')).display === 'none' ? null : document.getElementById('mensagem-vazio').textContent"
        pg.evaluate("() => __emitir('entrou', {total_hoje: 0, historico: []})")
        assert pg.evaluate(vazio) == "Aguardando chamadas…", pg.evaluate(vazio)
        pg.evaluate("() => __emitir('chamada', {tipo: 'senha', local: 'Guichê 1', senha: 'A001'})")
        pg.wait_for_function("() => document.getElementById('chamada-valor').textContent === 'A001'", timeout=20000)
        assert pg.evaluate(vazio) == "Nenhuma chamada anterior hoje.", pg.evaluate(vazio)
        pg.evaluate("() => __emitir('chamada', {tipo: 'senha', local: 'Guichê 2', senha: 'A002'})")
        pg.wait_for_function("() => document.getElementById('chamada-valor').textContent === 'A002'", timeout=20000)
        assert pg.evaluate("document.querySelectorAll('#lista-ultimas li').length") == 1
        assert pg.evaluate(vazio) is None
        contador = pg.evaluate("document.getElementById('contador-chamadas') && document.getElementById('contador-chamadas').textContent")
        assert contador == (None if nome == "terreo" else "2 chamadas hoje"), contador
        assert erros == [], erros
        pg.close()
    ok("Chromium: chamadas ao vivo atualizam card, lista e 'Aguardando chamadas…'/'Nenhuma chamada anterior hoje.' "
       "sem erro de JS, sem o contador (opção ligada) e com ele (desligada: '2 chamadas hoje')")

    # --- layout no pior caso: os dois textos mais longos, "Última
    #     atualização" com dígitos largos, fonte padrão e fonte larga forçada
    #     (Verdana/DejaVu Sans, como no 10.84), 1366x768 e 1920x1080, claro e
    #     escuro. Rodapé com 3 itens: frase | Espera | Última atualização.
    for largura, altura in ((1366, 768), (1920, 1080)):
        for tema in ("dark", "light"):
            for fonte in ("padrao", "larga"):
                for texto in ("menos de 5 min", "mais de 1 hora"):
                    pg = nav.new_page(viewport={"width": largura, "height": altura})
                    pg.add_init_script(f"localStorage.setItem('gestfila_tema', '{tema}');")
                    preparar(pg, html_on, responder_com(texto))
                    pg.goto("http://painel.test/painel/tv/terreo")
                    if fonte == "larga":
                        pg.add_style_tag(content=FONTE_LARGA)
                    pg.evaluate("""() => { __emitir('entrou', {total_hoje: 8888, historico: [{tipo: 'senha', local: 'Guichê 12', senha: 'RECP123'}]});
                                           document.getElementById('ultima-atualizacao').textContent = 'Última atualização: 18:58:58'; }""")
                    pg.wait_for_function(visivel)
                    assert pg.evaluate("document.documentElement.dataset.theme") == tema
                    assert pg.evaluate("document.getElementById('contador-chamadas')") is None
                    m = pg.evaluate(MEDIR_RODAPE)
                    if fonte == "larga":
                        assert "Verdana" in m["familia"] or "DejaVu" in m["familia"], m["familia"]
                    medidas[(largura, tema, fonte, texto)] = m
                    assert m["itens"] == 3 and m["ordem"] == ["rodape-mensagem", "tempo-espera", "ultima-atualizacao"], m
                    assert m["uma_linha"] and not m["estouro"], m
                    assert m["folga"] >= FOLGA_MINIMA_RODAPE_PX, (largura, tema, fonte, texto, m)
                    assert m["fonte_item"] == m["fonte_footer"], m
                    assert m["contraste"] >= 4.5, m
                    pg.close()
    nav.close()

for largura in (1366, 1920):
    for fonte in ("padrao", "larga"):
        grupo = {k: m for k, m in medidas.items() if k[0] == largura and k[2] == fonte}
        pior = min(grupo.items(), key=lambda kv: kv[1]["folga"])
        print(f"   {largura} fonte {fonte}: folga mínima {pior[1]['folga']} px ({pior[0][3]!r}, {pior[0][1]}), "
              f"fonte {pior[1]['fonte_item']}, contraste {min(m['contraste'] for m in grupo.values()):.1f}:1 ou mais")
ok(f"Layout (Chromium): frase | Espera | Última atualização, uma linha, sem estouro, folga >= "
   f"{FOLGA_MINIMA_RODAPE_PX} px no pior caso (fonte padrão e larga), mesma fonte clamp() e "
   "contraste >= 4,5:1 em 1366x768/1920x1080, claro e escuro")

# ===========================================================================
# 9b) Cadastro admin: checkbox grava e desgrava a opção
# ===========================================================================
with app.app_context():
    perfil_admin = Perfil(nome="admin")
    db.session.add(perfil_admin)
    db.session.commit()
    adm = Usuario(empresa_id=EMP_A, nome="adm", login="adm_espera_tv")
    adm.set_senha("senha123")
    adm.perfis = [perfil_admin]
    db.session.add(adm)
    db.session.commit()
c_adm = app.test_client()
assert c_adm.post("/login", data={"login": "adm_espera_tv", "senha": "senha123"}).status_code in (200, 302)
form_html = c_adm.get(f"/admin/tvs/{ID['desligada']}/editar").get_data(as_text=True)
assert 'name="mostra_tempo_espera"' in form_html and "Mostrar tempo de espera no rodapé" in form_html
assert "o rodapé desta TV deixa de mostrar &#34;N chamadas hoje&#34;" in form_html or     'o rodapé desta TV deixa de mostrar "N chamadas hoje"' in form_html
base_form = {"nome": "desligada", "unidade_id": UNI_A, "volume": 100, "filas": [F["REC"]]}
c_adm.post(f"/admin/tvs/{ID['desligada']}/editar", data={**base_form, "mostra_tempo_espera": "on"})
with app.app_context():
    assert tv("desligada").mostra_tempo_espera is True
c_adm.post(f"/admin/tvs/{ID['desligada']}/editar", data=base_form)
with app.app_context():
    assert tv("desligada").mostra_tempo_espera is False
c_adm.post("/admin/tvs/nova?empresa_id=%d" % EMP_A, data={**base_form, "nome": "Nova Espera", "mostra_tempo_espera": "on"})
with app.app_context():
    nova = Tv.query.filter_by(nome="Nova Espera").one()
    assert nova.mostra_tempo_espera is True and Tv.query.filter_by(nome="Nova Espera").count() == 1
ok("Admin: checkbox 'Mostrar tempo de espera no rodapé' no formulário, com o aviso de que o rodapé deixa de mostrar "
   "'N chamadas hoje'; editar liga/desliga e nova TV grava a opção")


# ===========================================================================
# 10) Rate limit 60/min por IP (por último: create_app de outro app
#     re-registra o socketio, que os blocos acima usam)
# ===========================================================================
class TestingConfigComRateLimit(TestingConfig):
    RATELIMIT_ENABLED = True


config_by_name["testing_rate_limit_espera_tv"] = TestingConfigComRateLimit
app_rl = create_app("testing_rate_limit_espera_tv")
with app_rl.app_context():
    db.create_all()
    e = Empresa(nome="RL")
    db.session.add(e)
    db.session.commit()
    u = Unidade(empresa_id=e.id, nome="U")
    db.session.add(u)
    db.session.commit()
    db.session.add(Tv(empresa_id=e.id, unidade_id=u.id, nome="rl", slug="rl", mostra_tempo_espera=True))
    db.session.commit()
    limiter.reset()
c_rl = app_rl.test_client()
codigos = [c_rl.get("/painel/tv/rl/espera").status_code for _ in range(61)]
assert codigos[:60] == [200] * 60 and codigos[60] == 429, codigos
with app_rl.app_context():
    limiter.reset()
ok("Rate limit: 60 requisições/min por IP aceitas, a 61ª recebe 429")



print()
print(f"TODOS OS {len(passos)} TESTES DO TEMPO DE ESPERA NA TV PASSARAM")
