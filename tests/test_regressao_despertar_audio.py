"""
Teste de regressão (mínimo, estrutural): "despertar" o hardware de saída de
áudio via Web Audio API antes de cada chamada no painel de TV (adendo que
documenta esta correção - EXPERIMENTAL, quarta tentativa pra corte de
início, causa diferente de 10.48/10.49/10.50: hardware de áudio, não motor
de TTS).

Limitação de infraestrutura, mesmo motivo já registrado em outros testes de
JS do projeto: sem runtime de navegador aqui, só confirma presença/
encadeamento da técnica no código renderizado, não que o corte sumiu.

Execução: python -m tests.test_regressao_despertar_audio (a partir da raiz
do projeto, com o virtualenv ativado).
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

    empresa = Empresa(nome="Clinica Despertar Audio")
    db.session.add(empresa)
    db.session.commit()

    unidade = Unidade(empresa_id=empresa.id, nome="Unidade 1")
    db.session.add(unidade)
    db.session.commit()

    tv = Tv(empresa_id=empresa.id, unidade_id=unidade.id, nome="TV 1", slug="tv-despertar-audio")
    db.session.add(tv)
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

resp = anon.get("/painel/tv/tv-despertar-audio")
assert resp.status_code == 200
html = resp.data.decode("utf-8")

# A) função existe. Desde a fila real de chamadas de voz (adendo 10.58,
#    BLUEPRINT.md), socket.on('chamada') não chama mais despertarSaidaAudio()/
#    falarChamada() diretamente - só enfileira (enfileirarChamada());
#    processarProximaDaFila() é quem decide, por turno, se toca o tom (só na
#    1ª chamada de uma rajada - pipeline ocioso, ver adendo) e chama
#    despertarSaidaAudio() envolvendo falarChamada().
assert "function despertarSaidaAudio(callback)" in html
ok("despertarSaidaAudio() presente")

corpo_handler = html[html.index('socket.on("chamada"'):html.index("Smart TVs ficam com o navegador aberto")]
assert "enfileirarChamada(dados)" in corpo_handler
ok("socket.on('chamada') enfileira a chamada (fila real, adendo 10.58) em vez de chamar áudio direto")

corpo_processar_fila = corpo_da_funcao(html, "processarProximaDaFila")
assert "despertarSaidaAudio(executarTurno)" in corpo_processar_fila
assert "falarChamada(item.dados, finalizarTurno)" in corpo_processar_fila
ok("processarProximaDaFila() chama despertarSaidaAudio() envolvendo falarChamada() em cada turno da fila")

# B) encadeamento real via onended (não setTimeout estimado).
corpo_despertar = corpo_da_funcao(html, "despertarSaidaAudio")
assert "oscilador.onended = () => {" in corpo_despertar
assert "chamarUmaVez()" in corpo_despertar
ok("onended dispara chamarUmaVez() (evento real, não setTimeout estimado)")

# C) fallback gracioso: sem AudioContext, e em catch/try, cai pra chamarUmaVez() -> callback().
assert "window.AudioContext || window.webkitAudioContext" in corpo_despertar
assert corpo_despertar.count("chamarUmaVez()") >= 4  # sem suporte + catch de resume() + catch externo + onended
ok("Fallback gracioso presente em todos os caminhos de falha (sem Web Audio API, resume() rejeitado, erro externo)")

# C.1) Guard de dupla-chamada (mesmo padrão de `feito` em
#      falarChamadaPregravada()): callback() só é invocado de DENTRO de
#      chamarUmaVez(), nunca direto - garante que falarChamada() não dispara
#      duas vezes pro mesmo evento numa janela de corrida entre o catch
#      externo e o onended real.
assert "let chamado = false;" in corpo_despertar
assert "if (chamado) return;" in corpo_despertar
assert "chamado = true;" in corpo_despertar
# a única ocorrência real de "callback()" (chamada, não referência de parâmetro)
# deve estar dentro da definição de chamarUmaVez - nenhum outro ponto da função
# chama callback() diretamente, sempre passando por chamarUmaVez() primeiro.
assert corpo_despertar.count("callback();") == 1
ok("Guard de dupla-chamada presente: callback() só é invocado de dentro de chamarUmaVez(), uma única vez garantida")

# D) heartbeat (10.50) não foi tocado - continua existindo, função separada.
assert "function dispararHeartbeatTts(" in html
ok("dispararHeartbeatTts() (10.50) continua presente e intocada - convive com despertarSaidaAudio()")

print()
print(f"TODOS OS {len(passos)} TESTES DE DESPERTAR AUDIO PASSARAM")
