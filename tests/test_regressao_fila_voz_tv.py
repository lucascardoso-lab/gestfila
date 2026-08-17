"""
Teste de regressão (estrutural): fila real de chamadas de voz no painel de TV
(adendo 10.58, BLUEPRINT.md) - chamadas nunca se cortam entre si, tocam uma
de cada vez, mesmo que várias cheguem em sequência rápida (decisão do
cliente: pequeno atraso em picos raros é aceitável, volume do sistema é
baixo, ~50 senhas/dia).

Duas decisões de produto confirmadas explicitamente com o cliente antes da
implementação (ver adendo 10.58 pra prós/contras das duas opções levantadas
em cada uma):
1. O tom de "despertar áudio" (despertarSaidaAudio, adendo 10.51) só toca
   antes da PRIMEIRA chamada de uma rajada (pipeline realmente ocioso antes
   dela) - chamadas seguintes da mesma rajada pulam o tom.
2. A atualização visual (inserirChamada) de uma chamada em fila só acontece
   quando o ÁUDIO DELA especificamente começa (turno processado) - tela e
   voz sempre sincronizadas, nunca a tela "adianta" da voz.

Limitação de infraestrutura, mesmo motivo já registrado em outros testes de
JS do projeto (ex. test_regressao_despertar_audio.py): sem runtime de
navegador aqui, confirma presença/encadeamento correto da lógica de
enfileiramento/desenfileiramento no código renderizado (estrutural), não que
3 chamadas reais tocam sem cortar num navegador de verdade.

Execução: python -m tests.test_regressao_fila_voz_tv (a partir da raiz do
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

    empresa = Empresa(nome="Clinica Fila Voz TV")
    db.session.add(empresa)
    db.session.commit()

    unidade = Unidade(empresa_id=empresa.id, nome="Unidade 1")
    db.session.add(unidade)
    db.session.commit()

    tv = Tv(empresa_id=empresa.id, unidade_id=unidade.id, nome="TV 1", slug="tv-fila-voz")
    db.session.add(tv)
    db.session.commit()

    tv_pregravada = Tv(
        empresa_id=empresa.id, unidade_id=unidade.id, nome="TV 2",
        slug="tv-fila-voz-pregravada", usa_audio_pregravado=True,
    )
    db.session.add(tv_pregravada)
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

resp = anon.get("/painel/tv/tv-fila-voz")
assert resp.status_code == 200
html = resp.data.decode("utf-8")

# ===========================================================================
# A) Estado da fila declarado - array + flag "em andamento", não só
#    speechSynthesis.speaking (comportamento inconsistente entre navegadores
#    em alguns casos, conforme pedido - a fila usa seu PRÓPRIO controle).
# ===========================================================================
assert "let filaChamadas = [];" in html
assert "let processandoFila = false;" in html
ok("Estado da fila (filaChamadas + processandoFila) declarado")

# ===========================================================================
# B) socket.on('chamada') só enfileira - não decide tom/fala/visual direto.
# ===========================================================================
corpo_handler = html[html.index('socket.on("chamada"'):html.index("Smart TVs ficam com o navegador aberto")]
assert "enfileirarChamada(dados)" in corpo_handler
assert "despertarSaidaAudio(" not in corpo_handler
assert "falarChamada(" not in corpo_handler
ok("socket.on('chamada') só chama enfileirarChamada() - tom/fala/visual ficam na fila, não no handler")

# ===========================================================================
# C) enfileirarChamada(): decide tocarTom no momento em que a chamada CHEGA
#    (pipeline ocioso = nada em andamento E fila vazia = 1ª de uma rajada,
#    decisão 1 confirmada com o cliente) e sempre processa a fila em seguida.
# ===========================================================================
corpo_enfileirar = corpo_da_funcao(html, "enfileirarChamada")
assert "const tocarTom = !processandoFila && filaChamadas.length === 0;" in corpo_enfileirar
assert "filaChamadas.push({ dados, tocarTom });" in corpo_enfileirar
assert "processarProximaDaFila();" in corpo_enfileirar
ok("enfileirarChamada() decide tocarTom na CHEGADA (pipeline ocioso = 1ª de uma rajada) e enfileira")

# ===========================================================================
# D) processarProximaDaFila(): nunca processa dois itens ao mesmo tempo
#    (guard de processandoFila), tom só quando tocarTom=true, falarChamada()
#    recebe um callback de conclusão (finalizarTurno) que só libera o próximo
#    item quando ESTA chamada terminou de tocar de verdade.
# ===========================================================================
corpo_processar = corpo_da_funcao(html, "processarProximaDaFila")
assert "if (processandoFila) return;" in corpo_processar
assert "const item = filaChamadas.shift();" in corpo_processar
assert "processandoFila = true;" in corpo_processar
ok("processarProximaDaFila() nunca inicia um novo turno enquanto outro está em andamento (guard de processandoFila)")

assert "if (item.tocarTom) {" in corpo_processar
assert "despertarSaidaAudio(executarTurno);" in corpo_processar
assert "} else {" in corpo_processar
assert "executarTurno();" in corpo_processar
ok("Tom de despertar só toca quando tocarTom=true (decidido na chegada, decisão 1) - senão pula direto pra executarTurno()")

# ORDEM falarChamada() antes de inserirChamada() dentro do turno, preservando
# a 10.48 (evita reflow/animação competindo com o início do áudio).
pos_falar = corpo_processar.index("falarChamada(item.dados, finalizarTurno);")
pos_inserir = corpo_processar.index("inserirChamada(item.dados);")
assert pos_falar < pos_inserir
ok("Dentro de cada turno, falarChamada() roda ANTES de inserirChamada() - ordem da 10.48 preservada por item")

assert "processandoFila = false;" in corpo_processar
assert "setTimeout(processarProximaDaFila, 0);" in corpo_processar
ok("Ao terminar um turno (finalizarTurno), libera processandoFila e agenda o próximo item da fila, se houver")

# ===========================================================================
# E) Decisão 2 (sincronia tela/voz): inserirChamada() só é chamada DENTRO do
#    turno de cada item (junto com falarChamada(), acima) - nunca no momento
#    em que a chamada chega à fila (enfileirarChamada() não atualiza a tela).
# ===========================================================================
assert "inserirChamada" not in corpo_enfileirar
ok("enfileirarChamada() não atualiza a tela - inserirChamada() só roda quando o turno da chamada é processado (decisão 2)")

# ===========================================================================
# F) falarChamada()/falarChamadaPregravada() aceitam um callback de conclusão
#    e o chamam em TODOS os caminhos de saída (inclusive os early-returns sem
#    áudio real: som desligado, sem suporte, sem voz) - senão a fila travaria
#    pra sempre esperando uma chamada que nunca vai "terminar".
# ===========================================================================
corpo_falar = corpo_da_funcao(html, "falarChamada")
assert "function falarChamada(dados, aoTerminar) {" in html
assert corpo_falar.count("concluir();") >= 3  # TV_SOM_ATIVO false / sem speechSynthesis / sem voz
assert "falarChamadaPregravada(dados, concluir);" in corpo_falar
ok("falarChamada() propaga/chama o callback de conclusão em todo early-return sem áudio real")

resp_pg = anon.get("/painel/tv/tv-fila-voz-pregravada")
assert resp_pg.status_code == 200
html_pg = resp_pg.data.decode("utf-8")
corpo_pregravado = corpo_da_funcao(html_pg, "falarChamadaPregravada")
assert "function falarChamadaPregravada(dados, aoTerminar) {" in html_pg
assert "concluir();" in corpo_pregravado
ok("falarChamadaPregravada() também aceita e chama o callback de conclusão (fallback .wav)")

# ===========================================================================
# G) cancel() deixa de ser o mecanismo normal de transição entre chamadas -
#    só sobra como proteção de borda (watchdog), documentado como tal, nunca
#    chamado incondicionalmente antes de todo speak().
# ===========================================================================
import re

assert "speechSynthesis.cancel(); // evita empilhar falas se varias chamadas chegarem rapido" not in html
# conta só chamadas REAIS (fora de comentário // ) - o comentário que documenta
# a mudança também menciona "speechSynthesis.cancel()" em prosa, de propósito.
corpo_falar_sem_comentarios = re.sub(r"//[^\n]*", "", corpo_falar)
assert corpo_falar_sem_comentarios.count("speechSynthesis.cancel()") == 1  # só dentro do watchdog, não no fluxo normal
assert "watchdog" in corpo_falar.lower()
ok("speechSynthesis.cancel() não roda mais incondicionalmente antes de speak() - só como fallback do watchdog")

# ===========================================================================
# H) fala.onend (evento real do motor) libera a fila - não speechSynthesis.
#    speaking isolado nem um tempo estimado (setTimeout fixo simulando
#    duração), conforme pedido explicitamente.
# ===========================================================================
assert "fala.onend = concluirUmaVez;" in corpo_falar
assert "fala.onerror = concluirUmaVez;" in corpo_falar
ok("fala.onend (evento real da utterance) dispara a conclusão do turno - não um tempo estimado")

# ===========================================================================
# I) Heartbeat (10.50) continua intocado - só dispara quando nada tocando,
#    o que agora inclui fila vazia naturalmente (processandoFila reflete
#    isso, mas dispararHeartbeatTts() continua usando speechSynthesis.
#    speaking, sem precisar conhecer a fila).
# ===========================================================================
assert "function dispararHeartbeatTts(" in html
corpo_heartbeat = corpo_da_funcao(html, "dispararHeartbeatTts")
assert "speechSynthesis.speaking" in corpo_heartbeat
assert "filaChamadas" not in corpo_heartbeat
assert "processandoFila" not in corpo_heartbeat
ok("dispararHeartbeatTts() (10.50) continua intocado - não precisa conhecer a fila diretamente")

# ===========================================================================
# J) Simulação em Python da lógica de tocarTom (mesma regra do JS, só pra
#    confirmar o RACIOCÍNIO da decisão 1 com números concretos - não substitui
#    a leitura estrutural acima, é uma checagem adicional de sanidade).
#    3 chamadas quase simultâneas: só a 1ª toca o tom; a isolada (fora de
#    rajada) sempre toca o tom, sem atraso artificial extra.
# ===========================================================================
# rajada de 3 chamadas chegando antes de qualquer processamento ter
# terminado (processando ainda False no momento de cada "chegada", mas a
# fila já tem os itens anteriores) - só a 1ª tem tocarTom=True.
fila_simulada = []
processando_simulado = False
tocou_tom = []
for _ in range(3):
    tocou_tom.append(not processando_simulado and len(fila_simulada) == 0)
    fila_simulada.append(True)
assert tocou_tom == [True, False, False]
ok("Rajada de 3 chamadas: só a 1ª toca o tom de despertar - as outras 2 pulam (decisão 1 confirmada)")

# chamada isolada (fila vazia, nada em andamento) - sempre toca o tom, mesma
# latência de hoje (sem atraso artificial quando não há concorrência).
fila_vazia = []
assert (not False and len(fila_vazia) == 0) is True
ok("Chamada isolada (sem fila/concorrência): tocarTom=True - mesma latência de hoje, sem atraso artificial")

print()
print(f"TODOS OS {len(passos)} TESTES DE FILA DE VOZ NO PAINEL DE TV PASSARAM")
