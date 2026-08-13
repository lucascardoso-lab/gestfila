"""
Teste de regressão: "heartbeat" periódico do motor de speechSynthesis no
painel de TV (adendo que documenta esta correção - EXPERIMENTAL, causa e
mecanismo diferentes da 10.48 e da 10.49).

Dado de campo confirmado com o cliente: o corte de início voltava a
acontecer toda vez que a TV ficava um tempo sem chamar nenhuma senha (não
só 1x por dia/sessão) - a PRIMEIRA chamada depois de um período ocioso
cortava, as seguintes saíam normais. Consistente com o motor de TTS
"esfriando" após período sem uso - mesma classe de bug já descrita na 10.13,
recorrente ao longo do dia em vez de só uma vez no load.

NÃO é o workaround de pause()/resume() já tentado e revertido na 10.13 -
este heartbeat só chama speak() de uma utterance própria, curtíssima e quase
inaudível, só quando speechSynthesis.speaking é false (nunca por cima de
fala real em andamento).

Limitação de infraestrutura, mesmo motivo já registrado em outros testes de
JS do projeto: sem runtime de navegador aqui, não dá pra provar que o corte
de fato sumiu, nem que o heartbeat não interfere audivelmente - só que a
técnica (setInterval + checagem de speechSynthesis.speaking + volume baixo)
está presente e aplicada do jeito descrito no código renderizado.

Execução: python -m tests.test_regressao_heartbeat_tts (a partir da raiz do
projeto, com o virtualenv ativado).
"""
import os

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

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

    empresa = Empresa(nome="Clinica Heartbeat TTS")
    db.session.add(empresa)
    db.session.commit()

    unidade = Unidade(empresa_id=empresa.id, nome="Unidade 1")
    db.session.add(unidade)
    db.session.commit()

    tv_normal = Tv(empresa_id=empresa.id, unidade_id=unidade.id, nome="TV Normal", slug="tv-heartbeat-normal")
    tv_som_off = Tv(
        empresa_id=empresa.id, unidade_id=unidade.id, nome="TV Som Off", som_ativo=False,
        slug="tv-heartbeat-som-off",
    )
    tv_pregravado = Tv(
        empresa_id=empresa.id, unidade_id=unidade.id, nome="TV Pregravada", slug="tv-heartbeat-pregravada",
        usa_audio_pregravado=True,
    )
    db.session.add_all([tv_normal, tv_som_off, tv_pregravado])
    db.session.commit()


def corpo_da_funcao(texto, nome_funcao):
    inicio = texto.index(f"function {nome_funcao}(")
    abre = texto.index("{", inicio)
    profundidade = 0
    i = abre
    while True:
        if texto[i] == "{":
            profundidade += 1
        elif texto[i] == "}":
            profundidade -= 1
            if profundidade == 0:
                return texto[abre:i + 1]
        i += 1


anon = app.test_client()

resp = anon.get("/painel/tv/tv-heartbeat-normal")
assert resp.status_code == 200
html = resp.data.decode("utf-8")

# ===========================================================================
# A) Constantes do heartbeat presentes com os valores decididos: intervalo
#    dentro da faixa 10-15s pedida, volume baixo mas NÃO zero (documentado no
#    código: volume 0 arrisca não exercitar o pipeline de áudio de verdade),
#    texto é um fonema real (vogal), não pontuação/espaço puro (esses são
#    usados de propósito pelo PRIMING_TTS_PREFIXO, com objetivo oposto).
# ===========================================================================
assert "const HEARTBEAT_TTS_INTERVALO_MS = 12000;" in html
assert 10000 <= 12000 <= 15000
ok("HEARTBEAT_TTS_INTERVALO_MS = 12000ms, dentro da faixa 10-15s pedida")

assert "const HEARTBEAT_TTS_VOLUME = 0.02;" in html
ok("HEARTBEAT_TTS_VOLUME > 0 (não usa volume 0, que arriscaria não exercitar o pipeline de áudio)")

assert 'const HEARTBEAT_TTS_TEXTO = "a";' in html
ok("HEARTBEAT_TTS_TEXTO é um fonema real (vogal), não pontuação/espaço puro")

# ===========================================================================
# B) dispararHeartbeatTts() checa speechSynthesis.speaking ANTES de disparar
#    (não é um timer solto) - nunca deve tocar por cima de uma fala real (ou
#    de outro heartbeat) em andamento.
# ===========================================================================
corpo_heartbeat = corpo_da_funcao(html, "dispararHeartbeatTts")
assert "speechSynthesis.speaking" in corpo_heartbeat
# a checagem precisa vir ANTES do speak() dentro da própria função, não depois
pos_checagem = corpo_heartbeat.index("speechSynthesis.speaking")
pos_speak = corpo_heartbeat.index("speechSynthesis.speak(aquecimento)")
assert pos_checagem < pos_speak
ok("dispararHeartbeatTts() checa speechSynthesis.speaking ANTES de chamar speak() - nunca por cima de fala em andamento")

assert "HEARTBEAT_TTS_VOLUME" in corpo_heartbeat and "HEARTBEAT_TTS_TEXTO" in corpo_heartbeat
ok("dispararHeartbeatTts() usa as constantes de volume/texto do heartbeat, não valores soltos")

# mesmos gates de falarChamada(): som desligado, áudio pré-gravado ou sem
# suporte a speechSynthesis - heartbeat não faz sentido em nenhum desses casos.
assert "TV_SOM_ATIVO" in corpo_heartbeat
assert "TV_USA_AUDIO_PREGRAVADO" in corpo_heartbeat
ok("dispararHeartbeatTts() respeita os mesmos gates de falarChamada() (som ativo, não-pré-gravado)")

# ===========================================================================
# C) setInterval liga o heartbeat de fato - só dentro do bloco guardado por
#    "speechSynthesis" in window (mesmo escopo do resto da configuração de
#    voz), com o intervalo configurado.
# ===========================================================================
assert "setInterval(dispararHeartbeatTts, HEARTBEAT_TTS_INTERVALO_MS)" in html
ok("setInterval liga dispararHeartbeatTts() no intervalo configurado")

# ===========================================================================
# D) Log de debug (atrás de ?debug=1, já existente desde a 10.48) dentro da
#    própria função de heartbeat - dá pra ver na máquina real quando o
#    heartbeat de fato disparou.
# ===========================================================================
assert "DEBUG_TIMING" in corpo_heartbeat
assert "heartbeat TTS disparado" in corpo_heartbeat
ok("Log de heartbeat atrás de DEBUG_TIMING (?debug=1) presente dentro de dispararHeartbeatTts()")

# ===========================================================================
# E) TV com som desligado: mesmo HTML, mas os gates continuam presentes no
#    corpo da função (a decisão de não disparar é em RUNTIME, não uma
#    variação de código gerado por TV) - confirma que o servidor não precisa
#    (nem faz) nenhuma lógica especial de heartbeat por TV além de injetar
#    TV_SOM_ATIVO, já usado por falarChamada() também.
# ===========================================================================
resp_off = anon.get("/painel/tv/tv-heartbeat-som-off")
assert resp_off.status_code == 200
html_off = resp_off.data.decode("utf-8")
assert "const TV_SOM_ATIVO = false;" in html_off
assert "function dispararHeartbeatTts(" in html_off
ok("TV com som desligado: mesma função dispararHeartbeatTts() presente, decide em runtime via TV_SOM_ATIVO")

# ===========================================================================
# F) TV com áudio pré-gravado: dispararHeartbeatTts() existe (mesmo HTML
#    renderizado, mesma função), mas o gate TV_USA_AUDIO_PREGRAVADO garante
#    que ela nunca chama speechSynthesis nesse caminho - o heartbeat é
#    específico do motor de TTS, o fallback .wav não sofre desse bug.
# ===========================================================================
resp_pg = anon.get("/painel/tv/tv-heartbeat-pregravada")
assert resp_pg.status_code == 200
html_pg = resp_pg.data.decode("utf-8")
assert "const TV_USA_AUDIO_PREGRAVADO = true;" in html_pg
corpo_pregravado_heartbeat = corpo_da_funcao(html_pg, "dispararHeartbeatTts")
assert "TV_USA_AUDIO_PREGRAVADO" in corpo_pregravado_heartbeat
ok("TV com áudio pré-gravado: dispararHeartbeatTts() presente, mas gate TV_USA_AUDIO_PREGRAVADO evita uso de speechSynthesis")

print()
print(f"TODOS OS {len(passos)} TESTES DE HEARTBEAT TTS PASSARAM")
