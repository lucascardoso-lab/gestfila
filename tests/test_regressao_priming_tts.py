"""
Teste de regressão: "priming" do motor de speechSynthesis no painel de TV
(adendo que documenta esta correção - causa DIFERENTE da 10.48).

Bug reportado após a 10.48 já estar aplicada (hardware trocado por uma
máquina mais potente, corte de início persistiu): os primeiros ~100-300ms de
CADA utterance eram engolidos (bug documentado do Chromium/SAPI Windows),
sistêmico - tanto em chamada por nome ("Lucas Cardoso" saindo como "cas
Cardoso") quanto por código de senha (ex.: "BSG009"). Diferente da 10.48
(CPU/reflow, contida por hardware), este é um atraso de inicialização do
próprio motor de TTS, independente de CPU.

Limitação de infraestrutura, mesmo motivo já registrado em outros testes de
JS do projeto (ex.: test_regressao_som_configuravel.py): sem runtime de
navegador aqui, não dá pra provar que o corte de fato sumiu - só que a
técnica de mitigação (prefixo neutro consumido pelo motor no lugar da
primeira sílaba real) está presente e aplicada no lugar certo do código
renderizado.

Execução: python -m tests.test_regressao_priming_tts (a partir da raiz do
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

    empresa = Empresa(nome="Clinica Priming TTS")
    db.session.add(empresa)
    db.session.commit()

    unidade = Unidade(empresa_id=empresa.id, nome="Unidade 1")
    db.session.add(unidade)
    db.session.commit()

    tv_normal = Tv(empresa_id=empresa.id, unidade_id=unidade.id, nome="TV Normal", slug="tv-priming-normal")
    tv_pregravado = Tv(
        empresa_id=empresa.id, unidade_id=unidade.id, nome="TV Pregravada", slug="tv-priming-pregravada",
        usa_audio_pregravado=True,
    )
    db.session.add_all([tv_normal, tv_pregravado])
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

# ===========================================================================
# A) TV normal (speechSynthesis): a constante de priming existe e o texto da
#    Utterance é PRIMING_TTS_PREFIXO + montarTextoChamada(dados) - único
#    ponto de composição da fala, cobre nome e código automaticamente sem
#    duplicar lógica (mesma função falarChamada() usada nos dois fluxos,
#    já provado por outros testes - ver 10.13/não-duplicação).
# ===========================================================================
resp = anon.get("/painel/tv/tv-priming-normal")
assert resp.status_code == 200
html = resp.data.decode("utf-8")

assert "const PRIMING_TTS_PREFIXO = \", \";" in html
ok("Constante PRIMING_TTS_PREFIXO definida com o prefixo neutro esperado (', ' - vírgula, não reticências)")

corpo_falar = corpo_da_funcao(html, "falarChamada")
assert "new SpeechSynthesisUtterance(PRIMING_TTS_PREFIXO + montarTextoChamada(dados))" in corpo_falar
ok("falarChamada() aplica PRIMING_TTS_PREFIXO na Utterance, cobrindo nome e código no mesmo lugar")

# ===========================================================================
# B) montarTextoChamada() continua só compondo o TEXTO da mensagem (nome ou
#    código + local), sem se misturar com a preocupação de motor de TTS - o
#    priming é uma responsabilidade de falarChamada(), não de composição de
#    texto (separação de camadas, ver comentário no código).
# ===========================================================================
corpo_montar = corpo_da_funcao(html, "montarTextoChamada")
assert "PRIMING_TTS_PREFIXO" not in corpo_montar
ok("montarTextoChamada() não conhece PRIMING_TTS_PREFIXO - responsabilidade isolada em falarChamada()")

# ===========================================================================
# C) TV com áudio pré-gravado (fallback .wav, não usa speechSynthesis) não é
#    afetada por este bug (não há motor de TTS nesse caminho) - o priming não
#    deve aparecer na função de composição de áudio pré-gravado.
# ===========================================================================
resp_pg = anon.get("/painel/tv/tv-priming-pregravada")
assert resp_pg.status_code == 200
html_pg = resp_pg.data.decode("utf-8")

corpo_pregravado = corpo_da_funcao(html_pg, "falarChamadaPregravada")
assert "PRIMING_TTS_PREFIXO" not in corpo_pregravado
ok("falarChamadaPregravada() (fallback .wav) não usa o priming - bug é específico do motor speechSynthesis")

print()
print(f"TODOS OS {len(passos)} TESTES DE PRIMING TTS PASSARAM")
