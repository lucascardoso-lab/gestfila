"""
Teste de regressão (adendo 10.81): painel de TV aberto DO ZERO sem interação
(Chrome reaberto, com ou sem reboot) não pode travar a fila de voz.

Causa raiz reproduzida: sem "user activation", o AudioContext do tom de
despertar (10.51) nasce 'suspended' e o resume() do Chrome fica PENDENTE para
sempre - o tom nunca tocava, o callback nunca rodava e `processandoFila`
ficava true para sempre (a tela também parava: inserirChamada() roda dentro do
turno). Um clique depois não destravava, porque ninguém chamava resume() de
novo dentro do gesto.

Roda o JS REAL de painel/tv.html no Chromium do Playwright (o mesmo do runner
do GitHub - nunca o Google Chrome da máquina) com a política de autoplay do
Chrome de desktop (--autoplay-policy=document-user-activation-required).

Armadilha medida: page.evaluate, wait_for_function, wait_for_selector e
inner_text concedem user activation à página e escondem o bug. Por isso o
roteiro roda DENTRO da página (stub do socket.io) e avisa cada etapa por
console.log; o Python só espera essas mensagens (expect_console_message, que
não toca na página) e dá o clique de mouse real. Nenhuma espera fixa no lado
do Python - os timeouts são só teto.

Execução: python -m tests.test_regressao_som_bloqueado_autoplay (a partir da
raiz do projeto, com o virtualenv ativado).
"""
import io
import json
import os
import wave

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

from playwright.sync_api import sync_playwright

from app import create_app
from app.extensions import db
from app.models import Empresa, Tv, Unidade

app = create_app("testing")

passos = []


def ok(msg):
    passos.append(msg)
    print("OK:", msg)


with app.app_context():
    db.create_all()
    empresa = Empresa(nome="Clinica Som Bloqueado")
    db.session.add(empresa)
    db.session.commit()
    unidade = Unidade(empresa_id=empresa.id, nome="Unidade 1")
    db.session.add(unidade)
    db.session.commit()
    db.session.add_all([
        Tv(empresa_id=empresa.id, unidade_id=unidade.id, nome="TV voz", slug="tv-voz"),
        Tv(empresa_id=empresa.id, unidade_id=unidade.id, nome="TV wav", slug="tv-wav",
           usa_audio_pregravado=True),
        Tv(empresa_id=empresa.id, unidade_id=unidade.id, nome="TV inativa", slug="tv-inativa", ativo=False),
    ])
    db.session.commit()

cliente = app.test_client()
html_voz = cliente.get("/painel/tv/tv-voz").get_data(as_text=True)
html_wav = cliente.get("/painel/tv/tv-wav").get_data(as_text=True)
html_inativa = cliente.get("/painel/tv/tv-inativa").get_data(as_text=True)

# ---------------------------------------------------------------------------
# 1) Estrutural: os quatro mecanismos do 10.81 estão no template.
# ---------------------------------------------------------------------------
assert "const DESPERTAR_AUDIO_TIMEOUT_MS = 1000;" in html_voz
assert "timeoutTom = setTimeout(" in html_voz
ok("Tempo máximo no tom de despertar (DESPERTAR_AUDIO_TIMEOUT_MS)")
assert "const TURNO_FILA_TIMEOUT_MS = DESPERTAR_AUDIO_TIMEOUT_MS + WATCHDOG_FALA_TIMEOUT_MS + 4000;" in html_voz
assert "let watchdogTurno = setTimeout(verificarTurno, TURNO_FILA_TIMEOUT_MS);" in html_voz
assert "if (turnoEncerrado) return;" in html_voz
ok("Autocorreção da fila: watchdog do turno acima do tom + watchdog da fala, com finalização única")


def corpo_da_funcao(texto, inicio_marcador):
    inicio = texto.index(inicio_marcador)
    i = texto.index("{", inicio)
    profundidade = 0
    while True:
        profundidade += {"{": 1, "}": -1}.get(texto[i], 0)
        if profundidade == 0:
            return texto[inicio:i + 1]
        i += 1


def sem_comentarios(js):
    return "\n".join(linha for linha in js.splitlines() if not linha.strip().startswith("//"))


# O watchdog do turno nunca chama cancel(); o único cancel() do fluxo real é
# o watchdog da PRÓPRIA fala travada (10.58).
assert "cancel()" not in sem_comentarios(corpo_da_funcao(html_voz, "const verificarTurno = () =>"))
corpo_falar = sem_comentarios(corpo_da_funcao(html_voz, "function falarChamada("))
assert corpo_falar.count("speechSynthesis.cancel()") == 1
assert corpo_falar.index("speechSynthesis.cancel()") > corpo_falar.index("const watchdog = setTimeout(")
ok("Watchdog do turno não chama cancel(); falarChamada() só cancela no próprio watchdog da fala")

assert "function destravarAudioNoGesto()" in html_voz
assert '["pointerdown", "keydown", "touchstart"]' in html_voz
ok("resume() + destravar a fila no primeiro clique/tecla/toque")
assert 'id="aviso-som-bloqueado"' in html_voz and 'evento.error === "not-allowed"' in html_voz
assert 'erro.name === "NotAllowedError"' in html_wav
ok("Aviso de som bloqueado: AudioContext suspenso, fala 'not-allowed' ou play() com NotAllowedError")

assert 'id="aviso-som-bloqueado"' not in html_inativa and "Som bloqueado pelo navegador" not in html_inativa
assert ".aviso-som-bloqueado[hidden] { display: none; }" in html_inativa  # só o CSS, do layout comum
ok("indisponivel.html (TV inativa) não tem o HTML da faixa - só herda o CSS do layout")

# ---------------------------------------------------------------------------
# 2) Comportamento real no Chromium, sem user activation.
# ---------------------------------------------------------------------------
def wav_silencioso():
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 800)  # 0,1 s
    return buf.getvalue()


WAV = wav_silencioso()

# Antes dos scripts da página: conta falas reais (ignora o aquecimento " " e
# o heartbeat "a") e play() de clipes; ?sem_webaudio=1 remove a Web Audio API
# para o aviso só poder vir do play() recusado.
INIT = """
window.__toques = {falas: 0, plays: 0, recusados: 0, resolvidos: 0};
if (location.search.indexOf('sem_webaudio=1') !== -1) {
  delete window.AudioContext; delete window.webkitAudioContext;
}
if (window.speechSynthesis) {
  const falar = speechSynthesis.speak.bind(speechSynthesis);
  speechSynthesis.speak = function (u) { if ((u.text || '').trim().length > 1) __toques.falas++; return falar(u); };
}
const tocar = HTMLMediaElement.prototype.play;
HTMLMediaElement.prototype.play = function () {
  __toques.plays++;
  const p = tocar.call(this);
  p.then(() => __toques.resolvidos++, (e) => { if (e && e.name === 'NotAllowedError') __toques.recusados++; });
  return p;
};
"""

# Roteiro dentro da página, avançando por condição (nunca por tempo fixo):
# reload com histórico -> A001 -> A002 -> PRONTO_CLIQUE (Python clica) ->
# aviso some -> POS_CLIQUE (nenhuma chamada tocada pelo gesto) -> A003 -> FIM.
STUB_SOCKET_IO = """
(function () {
  const H = {};
  window.io = function () { return {
    on: function (ev, fn) { (H[ev] = H[ev] || []).push(fn); }, emit: function () {} }; };
  const emitir = (ev, d) => (H[ev] || []).forEach((fn) => fn(d));
  const card = () => document.getElementById('chamada-valor').textContent;
  const aviso = () => !document.getElementById('aviso-som-bloqueado').hidden;
  const estado = () => JSON.stringify(Object.assign({card: card(), aviso: aviso(),
    ativo: navigator.userActivation.hasBeenActive}, __toques));
  const quando = (cond) => new Promise((ok) => { const t = setInterval(() => { if (cond()) { clearInterval(t); ok(); } }, 20); });
  const pausa = (ms) => new Promise((ok) => setTimeout(ok, ms));
  const chamada = (senha) => emitir('chamada', {tipo: 'senha', local: 'Guiche 1', senha: senha});
  const wav = location.search.indexOf('wav=1') !== -1;
  window.addEventListener('load', async () => {
    emitir('entrou', {total_hoje: 2, historico: [
      {tipo: 'senha', local: 'Guiche 1', senha: 'H001'}, {tipo: 'senha', local: 'Guiche 1', senha: 'H002'}]});
    await quando(() => card() === 'H002');
    console.log('HISTORICO ' + estado());
    chamada('A001');
    await quando(() => card() === 'A001');
    chamada('A002');
    await quando(() => card() === 'A002' && aviso() && (!wav || __toques.recusados >= 2));
    console.log('PRONTO_CLIQUE ' + estado());
    await quando(() => navigator.userActivation.hasBeenActive && !aviso());
    const antes = Object.assign({}, __toques);
    await pausa(1500); // > tempo máximo do tom: janela para o gesto tocar algo, se tocasse
    console.log('POS_CLIQUE ' + JSON.stringify({antes: antes, depois: __toques}));
    chamada('A003');
    await quando(() => card() === 'A003' && (!wav || __toques.resolvidos >= 1));
    console.log('FIM ' + estado());
  });
})();
"""


def rodar(html, query):
    with sync_playwright() as pw:
        nav = pw.chromium.launch(args=["--autoplay-policy=document-user-activation-required"])
        pg = nav.new_page()
        msgs = {}
        pg.on("console", lambda m: msgs.setdefault(m.text.split(" ", 1)[0], m.text.split(" ", 1)[-1]))
        pg.add_init_script(INIT)
        pg.route("http://painel.test/static/**", lambda r: r.fulfill(body="", status=200))
        pg.route("http://painel.test/static/audio/**",
                 lambda r: r.fulfill(body=WAV, content_type="audio/wav"))
        pg.route("**/socket.io.min.js*",
                 lambda r: r.fulfill(body=STUB_SOCKET_IO, content_type="application/javascript"))
        pg.route("http://painel.test/painel/**", lambda r: r.fulfill(body=html, content_type="text/html"))

        with pg.expect_console_message(lambda m: m.text.startswith("PRONTO_CLIQUE "), timeout=20000):
            pg.goto("http://painel.test/painel/tv/x" + query)
        with pg.expect_console_message(lambda m: m.text.startswith("FIM "), timeout=20000):
            pg.mouse.click(400, 400)  # gesto real do usuário

        resultado = {k: json.loads(msgs[k]) for k in ("HISTORICO", "PRONTO_CLIQUE", "POS_CLIQUE", "FIM")}

        # contraste da faixa nos dois temas do painel (já depois do roteiro -
        # o evaluate concede user activation, aqui não importa mais)
        contrastes = {}
        for tema in ("dark", "light"):
            contrastes[tema] = pg.evaluate("""(tema) => {
              document.documentElement.setAttribute('data-theme', tema);
              const el = document.getElementById('aviso-som-bloqueado');
              el.hidden = false;
              const s = getComputedStyle(el);
              const lum = (cor) => {
                const [r, g, b] = cor.match(/\\d+/g).slice(0, 3).map((v) => {
                  v = v / 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); });
                return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
              const [a, b] = [lum(s.color), lum(s.backgroundColor)].sort((x, y) => y - x);
              return {razao: (a + 0.05) / (b + 0.05), display: s.display};
            }""", tema)
        nav.close()
        return resultado, contrastes


# --- TV com voz (speechSynthesis) ---
r, contrastes = rodar(html_voz, "")
assert r["HISTORICO"]["falas"] == 0 and r["HISTORICO"]["plays"] == 0, r["HISTORICO"]
antes = r["PRONTO_CLIQUE"]
assert antes["ativo"] is False and antes["aviso"] is True and antes["card"] == "A002", antes
ok("TV com voz, sem interação: reload com histórico não fala; A001 e A002 passam pela fila "
   "(tela atualizou) e o aviso de som bloqueado está visível")
assert r["POS_CLIQUE"]["antes"] == r["POS_CLIQUE"]["depois"], r["POS_CLIQUE"]
ok("1º clique só destrava: nenhuma chamada do histórico (nem as que saíram mudas) é falada de novo")
assert r["FIM"]["card"] == "A003" and r["FIM"]["aviso"] is False, r["FIM"]
ok("Depois do 1º clique: aviso some e a chamada seguinte (A003) é processada normalmente")

for tema, c in contrastes.items():
    assert c["display"] != "none" and c["razao"] >= 4.5, (tema, c)
ok(f"Faixa legível nos dois temas do painel (contraste escuro {contrastes['dark']['razao']:.1f}:1, "
   f"claro {contrastes['light']['razao']:.1f}:1)")

# --- TV com áudio pré-gravado, sem Web Audio API: o aviso só pode vir do play() ---
r, _ = rodar(html_wav, "?wav=1&sem_webaudio=1")
assert r["HISTORICO"]["plays"] == 0, r["HISTORICO"]
antes = r["PRONTO_CLIQUE"]
assert antes["ativo"] is False and antes["card"] == "A002", antes
assert antes["recusados"] >= 2 and antes["resolvidos"] == 0 and antes["aviso"] is True, antes
ok("TV pré-gravada, sem interação: play() recusado (NotAllowedError) mostra o aviso e não trava a fila")
assert r["POS_CLIQUE"]["antes"] == r["POS_CLIQUE"]["depois"], r["POS_CLIQUE"]
assert r["FIM"]["card"] == "A003" and r["FIM"]["resolvidos"] >= 1 and r["FIM"]["aviso"] is False, r["FIM"]
ok("TV pré-gravada: o clique não toca nada sozinho; aviso some e os clipes da A003 tocam")

print(f"\n{len(passos)} verificações OK")
