"""
Teste de regressão do redesign do painel de chamada (TV) - modernização de
layout/hierarquia visual pedida em sessão de trabalho dedicada (tabela de
2 colunas -> card central "Chamando agora" + lista "Últimas chamadas" +
cabeçalho com relógio/status + animação de nova chamada), SEM alterar
nenhuma rota, WebSocket, regra de negócio ou lógica de tempo real.

Cobre especificamente os requisitos de não-regressão do redesign (as 3
correções da seção 10.13 continuam valendo, agora sobre a estrutura nova):

1. Áudio (falarChamada) nunca é chamado a partir da reconstrução de
   histórico (reconexão/reload) - só a partir de evento 'chamada' ao vivo.
2. A animação de nova chamada segue a mesma regra: nunca dispara em
   reconexão/reload, só em chamada nova ou 'Tocar' (repetir).
3. 'Tocar' (repetir=True) nunca duplica a chamada atual na lista "Últimas
   chamadas" (equivalente à não-duplicação de linha do layout antigo).
4. Chamada por nome, chamada com cor de tipo prioritário e múltiplas filas
   na mesma TV continuam funcionando com a estrutura nova.
5. `painel/indisponivel.html` (TV desativada) não foi afetado pelo redesign.

Limitação de infraestrutura (mesmo motivo já registrado em
test_regressao_som_configuravel.py/test_regressao_tv_historico.py): sem
runtime de navegador nos testes, os itens 1-2 são verificados checando que
o CÓDIGO/CONTRATO corretos chegam até o JS (presença/ausência de trechos no
HTML servido), não que o efeito visual/sonoro de fato ocorreu.

Execução: python -m tests.test_regressao_painel_tv_redesign (a partir da
raiz do projeto, com o virtualenv ativado).
"""
import os

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

from app import create_app
from app.extensions import db, socketio
from app.models import Empresa, Fila, Local, Perfil, Senha, TipoSenha, Tv, Unidade, Usuario

app = create_app("testing")

passos = []


def ok(msg):
    passos.append(msg)
    print("OK:", msg)


NAMESPACE = "/painel"

with app.app_context():
    db.create_all()

    perfil_atendente = Perfil(nome="atendente")
    db.session.add(perfil_atendente)
    db.session.commit()

    empresa = Empresa(nome="Clinica Redesign")
    db.session.add(empresa)
    db.session.commit()

    unidade = Unidade(empresa_id=empresa.id, nome="Unidade 1")
    db.session.add(unidade)
    db.session.commit()

    fila_a = Fila(empresa_id=empresa.id, unidade_id=unidade.id, nome="Fila A", sigla="FA")
    fila_b = Fila(empresa_id=empresa.id, unidade_id=unidade.id, nome="Fila B", sigla="FB")
    db.session.add_all([fila_a, fila_b])
    db.session.commit()

    local = Local(empresa_id=empresa.id, unidade_id=unidade.id, nome="Guiche 01")
    db.session.add(local)
    db.session.commit()

    tipo_prioritario = TipoSenha(empresa_id=empresa.id, nome="Idoso", cor="#E67E22", peso_prioridade=1)
    # reproduz o cenário real que causou o bug em produção local: um tipo NÃO
    # prioritário ("Normal", peso_prioridade=0) com cor cadastrada mesmo assim
    # (id=1/"Normal"/cor='#FF0000' encontrado no banco local durante o
    # diagnóstico do Problema 1) - a chamada não deve ficar vermelha por causa
    # disso, já que peso_prioridade=0 e a senha em si não é prioritária.
    tipo_normal_com_cor = TipoSenha(empresa_id=empresa.id, nome="Normal", cor="#FF0000", peso_prioridade=0)
    db.session.add_all([tipo_prioritario, tipo_normal_com_cor])
    db.session.commit()

    tv = Tv(empresa_id=empresa.id, unidade_id=unidade.id, nome="TV Redesign", slug="tv-redesign")
    tv.filas = [fila_a, fila_b]
    tv_inativa = Tv(empresa_id=empresa.id, unidade_id=unidade.id, nome="TV Desligada", slug="tv-desligada", ativo=False)
    db.session.add_all([tv, tv_inativa])
    db.session.commit()

    senha_normal = Senha(
        empresa_id=empresa.id, unidade_id=unidade.id, fila_id=fila_a.id,
        codigo="FA001", sequencial=1, prioridade=False, status="aguardando",
    )
    senha_com_nome = Senha(
        empresa_id=empresa.id, unidade_id=unidade.id, fila_id=fila_a.id,
        codigo="FA002", sequencial=2, prioridade=False, status="aguardando",
        nome_paciente="Carlos Andrade",
    )
    senha_prioritaria = Senha(
        empresa_id=empresa.id, unidade_id=unidade.id, fila_id=fila_a.id,
        codigo="FA003", sequencial=3, prioridade=True, status="aguardando",
        tipo_senha_id=tipo_prioritario.id,
    )
    senha_tipo_normal_vermelho = Senha(
        empresa_id=empresa.id, unidade_id=unidade.id, fila_id=fila_a.id,
        codigo="FA004", sequencial=4, prioridade=False, status="aguardando",
        tipo_senha_id=tipo_normal_com_cor.id,
    )
    # Problema 2 (correção): nomes de comprimento variável - 1 palavra, nome
    # composto de 4+ palavras com acento, e o caso real que motivou a correção
    # (capitalização "errada" já vem assim do banco, não é bug de CSS).
    senha_nome_1_palavra = Senha(
        empresa_id=empresa.id, unidade_id=unidade.id, fila_id=fila_a.id,
        codigo="FA005", sequencial=5, prioridade=False, status="aguardando",
        nome_paciente="Ana",
    )
    senha_nome_composto_acentuado = Senha(
        empresa_id=empresa.id, unidade_id=unidade.id, fila_id=fila_a.id,
        codigo="FA006", sequencial=6, prioridade=False, status="aguardando",
        nome_paciente="Maria Aparecida dos Santos Ção",
    )
    senha_nome_capitalizacao_original = Senha(
        empresa_id=empresa.id, unidade_id=unidade.id, fila_id=fila_a.id,
        codigo="FA007", sequencial=7, prioridade=False, status="aguardando",
        # nome sintético - preserva de propósito a capitalização mista/
        # incomum (2 maiúsculas + minúsculas na primeira palavra, tudo
        # minúsculo na segunda) que o Problema 2 testa: o texto livre digitado
        # pelo operador no totem não passa por nenhuma normalização de caixa,
        # então o nome tem que chegar ao painel exatamente como foi digitado.
        nome_paciente="FUlano teste",
    )
    senha_fila_b = Senha(
        empresa_id=empresa.id, unidade_id=unidade.id, fila_id=fila_b.id,
        codigo="FB001", sequencial=1, prioridade=False, status="aguardando",
    )
    db.session.add_all([
        senha_normal, senha_com_nome, senha_prioritaria, senha_tipo_normal_vermelho,
        senha_nome_1_palavra, senha_nome_composto_acentuado, senha_nome_capitalizacao_original,
        senha_fila_b,
    ])
    db.session.commit()

    # Problema 3 (correção): fila/TV isoladas pra testar a progressão do
    # histórico "de hoje" sem interferir na sequência de chamadas já usada
    # pelos outros cenários acima.
    fila_c = Fila(empresa_id=empresa.id, unidade_id=unidade.id, nome="Fila C", sigla="FC")
    db.session.add(fila_c)
    db.session.commit()

    tv_p3 = Tv(empresa_id=empresa.id, unidade_id=unidade.id, nome="TV Problema3", slug="tv-problema3")
    tv_p3.filas = [fila_c]
    db.session.add(tv_p3)
    db.session.commit()

    senha_p3_1 = Senha(
        empresa_id=empresa.id, unidade_id=unidade.id, fila_id=fila_c.id,
        codigo="FC001", sequencial=1, prioridade=False, status="aguardando",
    )
    senha_p3_2 = Senha(
        empresa_id=empresa.id, unidade_id=unidade.id, fila_id=fila_c.id,
        codigo="FC002", sequencial=2, prioridade=False, status="aguardando",
    )
    senha_p3_3 = Senha(
        empresa_id=empresa.id, unidade_id=unidade.id, fila_id=fila_c.id,
        codigo="FC003", sequencial=3, prioridade=False, status="aguardando",
    )
    db.session.add_all([senha_p3_1, senha_p3_2, senha_p3_3])
    db.session.commit()

    usuario = Usuario(empresa_id=empresa.id, nome="Atendente Redesign", login="atendente_redesign")
    usuario.set_senha("senha123")
    usuario.perfis = [perfil_atendente]
    usuario.unidades = [unidade]
    db.session.add(usuario)
    db.session.commit()

    ids = dict(
        fila_a=fila_a.id, fila_b=fila_b.id, fila_c=fila_c.id, local=local.id, tv=tv.id, tv_p3=tv_p3.id,
        senha_normal=senha_normal.id, senha_com_nome=senha_com_nome.id,
        senha_prioritaria=senha_prioritaria.id, senha_fila_b=senha_fila_b.id,
    )

c = app.test_client()
resp = c.post("/login", data={"login": "atendente_redesign", "senha": "senha123"}, follow_redirects=True)
assert resp.status_code == 200
with c.session_transaction() as sess:
    sess["atendimento_local_id"] = ids["local"]

# ===========================================================================
# A) A rota pública (por slug) continua abrindo e a página traz a estrutura
#    nova (card central + lista + cabeçalho) e as funções-chave do redesign.
# ===========================================================================
resp = app.test_client().get("/painel/tv/tv-redesign")
assert resp.status_code == 200
html = resp.data.decode("utf-8")
for trecho in [
    'id="chamada-atual"', 'id="chamada-valor"', 'id="chamada-local"',
    'id="lista-ultimas"', 'id="relogio"', 'id="status-conexao"',
    "function recarregarHistorico", "function inserirChamada",
    "function dispararAnimacaoDestaque", "function valorPrincipal",
    "function vozPtPreferida", "function falarChamada",
]:
    assert trecho in html, trecho
ok("GET /painel/tv/<slug> retorna 200 com a estrutura nova (card central, lista, cabeçalho)")

# ===========================================================================
# B) Áudio (falarChamada) e animação (dispararAnimacaoDestaque) nunca são
#    disparados a partir da reconstrução de histórico (reconexão/reload) -
#    só de dentro de inserirChamada (evento 'chamada' ao vivo).
# ===========================================================================
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


corpo_recarregar = corpo_da_funcao(html, "recarregarHistorico")
corpo_inserir = corpo_da_funcao(html, "inserirChamada")

assert "falarChamada" not in corpo_recarregar
assert "dispararAnimacaoDestaque" not in corpo_recarregar
ok("recarregarHistorico() (reconexão/reload) não chama falarChamada nem dispararAnimacaoDestaque")

assert corpo_inserir.count("dispararAnimacaoDestaque") == 2  # branch 'repetir' + branch de chamada nova
ok("inserirChamada() dispara a animação tanto numa chamada nova quanto num 'Tocar' (repetir)")

# ===========================================================================
# C) 'Tocar' (repetir=True) retorna ANTES de qualquer chamada a
#    adicionarAoHistorico - preserva a não-duplicação da seção 10.13, agora
#    sobre a lista "Últimas chamadas" em vez da tabela antiga.
# ===========================================================================
bloco_repetir = corpo_inserir[corpo_inserir.index("if (dados.repetir)"):]
bloco_repetir = bloco_repetir[:bloco_repetir.index("return;") + len("return;")]
assert "adicionarAoHistorico" not in bloco_repetir
ok("'Tocar' (repetir=True) nunca chama adicionarAoHistorico - não duplica na lista")

# ===========================================================================
# D) Chamada normal (por código) -> evento 'chamada' tipo 'senha'; reconectar
#    -> histórico "de hoje" traz a chamada (contrato da seção 10.13 intacto
#    sob a estrutura nova).
# ===========================================================================
ws = socketio.test_client(app, namespace=NAMESPACE)
ws.get_received(NAMESPACE)
ws.emit("entrar_tv", {"tv_id": ids["tv"]}, namespace=NAMESPACE)
ws.get_received(NAMESPACE)

resp = c.post(f"/atendimento/recepcao/filas/{ids['fila_a']}/chamar", data={"codigo": "FA001"}, follow_redirects=True)
assert resp.status_code == 200
recebidos = ws.get_received(NAMESPACE)
evento = next(ev for ev in recebidos if ev["name"] == "chamada")
assert evento["args"][0] == {"tipo": "senha", "local": "Guiche 01", "senha": "FA001"}
ok("Chamada por código emite {tipo: 'senha', local, senha} - contrato do payload intocado")

ws2 = socketio.test_client(app, namespace=NAMESPACE)
ws2.get_received(NAMESPACE)
ws2.emit("entrar_tv", {"tv_id": ids["tv"]}, namespace=NAMESPACE)
recebidos = ws2.get_received(NAMESPACE)
ev_entrou = next(ev for ev in recebidos if ev["name"] == "entrou")
assert ev_entrou["args"][0]["total_hoje"] == 1
assert ev_entrou["args"][0]["historico"] == [{"tipo": "senha", "local": "Guiche 01", "senha": "FA001"}]
ok("Reconectar reconstrói o histórico 'de hoje' a partir do banco (10.13 preservada)")

# ===========================================================================
# E) 'Tocar' reemite com repetir=True, sem alterar o histórico persistido.
# ===========================================================================
resp = c.post(f"/atendimento/recepcao/filas/{ids['fila_a']}/tocar", data={})
assert resp.status_code == 302
recebidos = ws.get_received(NAMESPACE)
evento_toque = next(ev for ev in recebidos if ev["name"] == "chamada")
assert evento_toque["args"][0]["repetir"] is True
ok("'Tocar' emite repetir=True (payload intocado)")

# 'Chamar' recusa uma segunda senha na MESMA fila+local enquanto o operador
# ainda tem uma em andamento (regra pré-existente, seção 10.2/10.17) -
# finaliza a anterior antes de seguir para o próximo cenário.
resp = c.post(f"/atendimento/recepcao/filas/{ids['fila_a']}/finalizar", data={}, follow_redirects=True)
assert resp.status_code == 200
ws.get_received(NAMESPACE)  # descarta o sinal 'fila_atualizada' do finalizar, irrelevante aqui

# ===========================================================================
# F) Chamada por nome (senha que já chega com nome_paciente gravado) ->
#    {tipo: 'nome', local, nome_paciente} - mesmo card "Chamando agora", só
#    troca o conteúdo (decisão da Sessão A).
# ===========================================================================
resp = c.post(f"/atendimento/recepcao/filas/{ids['fila_a']}/chamar", data={"codigo": "FA002"}, follow_redirects=True)
assert resp.status_code == 200
recebidos = ws.get_received(NAMESPACE)
evento_nome = next(ev for ev in recebidos if ev["name"] == "chamada")
assert evento_nome["args"][0] == {"tipo": "nome", "local": "Guiche 01", "nome_paciente": "Carlos Andrade"}
ok("Chamada por nome emite {tipo: 'nome', local, nome_paciente} - payload intocado")

corpo_valor_principal = corpo_da_funcao(html, "valorPrincipal")
assert 'dados.tipo === "nome"' in corpo_valor_principal and "nome_paciente" in corpo_valor_principal
ok("valorPrincipal() no HTML servido trata tipo='nome' usando nome_paciente, igual ao payload real")

resp = c.post(f"/atendimento/recepcao/filas/{ids['fila_a']}/finalizar", data={}, follow_redirects=True)
assert resp.status_code == 200
ws.get_received(NAMESPACE)  # descarta o sinal 'fila_atualizada' do finalizar, irrelevante aqui

# ===========================================================================
# F2) Correção do Problema 2: chamada por nome usa fonte menor/adaptativa
#     (tamanhoFonteNome), não o clamp() gigante da senha - e o texto chega ao
#     DOM sem transformação nenhuma (nem uppercase/lowercase forçado, nem
#     innerHTML) - testado com 1 palavra, nome composto de 4+ palavras com
#     acento, e a capitalização "como está gravada" (caso real do bug).
# ===========================================================================
corpo_tamanho_fonte = corpo_da_funcao(html, "tamanhoFonteNome")
assert "tamanho <= 10" in corpo_tamanho_fonte and ").length" in corpo_tamanho_fonte
# 4 faixas progressivas (curto -> nome composto longo) - nenhuma delas reusa
# o clamp() de 120-260px da senha.
assert corpo_tamanho_fonte.count("clamp(") == 4
assert "120px" not in corpo_tamanho_fonte and "260px" not in corpo_tamanho_fonte
ok("tamanhoFonteNome() no HTML servido tem faixas próprias, menores que o clamp() da senha")

corpo_render = corpo_da_funcao(html, "renderizarChamadaAtual")
assert 'dados.tipo === "nome"' in corpo_render
assert "chamada-atual-valor--nome" in corpo_render
assert "tamanhoFonteNome" in corpo_render
assert "elChamadaValor.textContent = valor;" in corpo_render  # nunca innerHTML
ok("renderizarChamadaAtual() aplica a classe/fonte de nome só quando tipo='nome', via textContent")

indice_regra_nome = html.index(".chamada-atual-valor--nome")
bloco_regra_nome = html[indice_regra_nome:html.index("}", indice_regra_nome) + 1]
assert "text-transform" not in bloco_regra_nome
ok("Regra CSS .chamada-atual-valor--nome não força text-transform (nome exibido como gravado)")

for codigo, nome_esperado in [
    ("FA005", "Ana"),
    ("FA006", "Maria Aparecida dos Santos Ção"),
    ("FA007", "FUlano teste"),
]:
    resp = c.post(f"/atendimento/recepcao/filas/{ids['fila_a']}/chamar", data={"codigo": codigo}, follow_redirects=True)
    assert resp.status_code == 200
    recebidos = ws.get_received(NAMESPACE)
    evento = next(ev for ev in recebidos if ev["name"] == "chamada")
    assert evento["args"][0]["nome_paciente"] == nome_esperado, evento["args"][0]
    resp = c.post(f"/atendimento/recepcao/filas/{ids['fila_a']}/finalizar", data={}, follow_redirects=True)
    assert resp.status_code == 200
    ws.get_received(NAMESPACE)
ok("Nomes de 1 palavra, compostos com acento, e com capitalização 'como gravada' chegam intactos no payload")

# ===========================================================================
# G) Senha de tipo prioritário com cor cadastrada (seção 10.4) -> payload
#    inclui 'cor'; o JS aplica a cor no card em destaque, e como indicador
#    discreto (borda) na lista "Últimas chamadas" - não mais preenchimento
#    inteiro do item, decisão tomada na Parte 2 do redesign.
# ===========================================================================
resp = c.post(f"/atendimento/recepcao/filas/{ids['fila_a']}/chamar", data={"codigo": "FA003"}, follow_redirects=True)
assert resp.status_code == 200
recebidos = ws.get_received(NAMESPACE)
evento_cor = next(ev for ev in recebidos if ev["name"] == "chamada")
assert evento_cor["args"][0]["cor"] == "#E67E22"
ok("Chamada de senha com tipo prioritário inclui 'cor' no payload (contrato da seção 10.4 intocado)")

corpo_render = corpo_da_funcao(html, "renderizarChamadaAtual")
assert "elChamadaAtual.style.backgroundColor = dados.cor" in corpo_render
corpo_item_historico = corpo_da_funcao(html, "construirItemHistorico")
assert "item.style.borderLeftColor = dados.cor" in corpo_item_historico
ok("HTML servido aplica dados.cor no card (fundo) e na lista (borda discreta), como desenhado na Parte 2")

resp = c.post(f"/atendimento/recepcao/filas/{ids['fila_a']}/finalizar", data={}, follow_redirects=True)
assert resp.status_code == 200
ws.get_received(NAMESPACE)  # descarta o sinal 'fila_atualizada' do finalizar, irrelevante aqui

# ===========================================================================
# G2) Correção do Problema 1 (bug real encontrado em produção local): um tipo
#     de senha NÃO prioritário ("Normal", peso_prioridade=0) com cor cadastrada
#     não deve pintar o card - só Senha.prioridade=True libera a cor no
#     payload (app/atendimento/helpers.py::_payload_chamada).
# ===========================================================================
resp = c.post(f"/atendimento/recepcao/filas/{ids['fila_a']}/chamar", data={"codigo": "FA004"}, follow_redirects=True)
assert resp.status_code == 200
recebidos = ws.get_received(NAMESPACE)
evento_normal_com_cor = next(ev for ev in recebidos if ev["name"] == "chamada")
assert "cor" not in evento_normal_com_cor["args"][0]
ok("Tipo de senha não-prioritário com cor cadastrada NÃO pinta o card (correção do Problema 1)")

resp = c.post(f"/atendimento/recepcao/filas/{ids['fila_a']}/finalizar", data={}, follow_redirects=True)
assert resp.status_code == 200
ws.get_received(NAMESPACE)

# ===========================================================================
# H) TV com múltiplas filas: duas chamadas em filas diferentes da mesma TV
#    continuam chegando como dois eventos 'chamada' independentes (decisão
#    aprovada na Sessão A: sem fila de exibição/áudio client-side).
# ===========================================================================
ws.get_received(NAMESPACE)  # limpa pendentes
resp = c.post(f"/atendimento/recepcao/filas/{ids['fila_b']}/chamar", data={"codigo": "FB001"}, follow_redirects=True)
assert resp.status_code == 200
recebidos = ws.get_received(NAMESPACE)
evento_fila_b = next(ev for ev in recebidos if ev["name"] == "chamada")
assert evento_fila_b["args"][0] == {"tipo": "senha", "local": "Guiche 01", "senha": "FB001"}
ok("Segunda fila da mesma TV emite seu próprio evento 'chamada', independente da primeira (sem fila de exibição)")

# ===========================================================================
# I) Animação respeita prefers-reduced-motion (CSS servido).
# ===========================================================================
resp_layout = app.test_client().get("/painel/tv/tv-redesign")
assert "@media (prefers-reduced-motion: reduce)" in resp_layout.data.decode("utf-8")
ok("CSS servido inclui @media (prefers-reduced-motion: reduce) desativando a animação")

# ===========================================================================
# J) TV inativa continua caindo em painel/indisponivel.html, sem nenhum
#    elemento do redesign (o redesign não tocou essa tela).
# ===========================================================================
resp = app.test_client().get("/painel/tv/tv-desligada")
assert resp.status_code == 200
texto_indisponivel = resp.data.decode("utf-8")
assert "desativada" in texto_indisponivel
# a checagem é pelos ELEMENTOS de fato (atributo id=), não pelo nome da classe
# CSS - _layout.html é compartilhado com tv.html, então os seletores
# .chamada-atual/#lista-ultimas aparecem no <style> mesmo aqui (CSS não usado
# nesta página, sem elemento nenhum correspondente no HTML/DOM).
assert 'id="chamada-atual"' not in texto_indisponivel
assert 'id="lista-ultimas"' not in texto_indisponivel
assert "function inserirChamada" not in texto_indisponivel
ok("TV desativada continua em painel/indisponivel.html, sem os elementos/JS do redesign (só CSS não usado)")

# ===========================================================================
# K) Sem innerHTML com dado dinâmico: a única ocorrência de '.innerHTML' no
#    arquivo é a limpeza da lista (string vazia), nunca inserção de dado do
#    servidor/WebSocket.
# ===========================================================================
ocorrencias_innerhtml = html.count(".innerHTML")
assert ocorrencias_innerhtml == 1
assert 'elListaUltimas.innerHTML = "";' in html
ok("Única ocorrência de innerHTML no arquivo é a limpeza da lista (string vazia) - nunca dado dinâmico")

# ===========================================================================
# L) Correção do Problema 3: com 1 chamada hoje, a lista fica sem itens "de
#    sobra" por design (a única chamada ocupa o card) - mas agora existe uma
#    mensagem própria pra esse estado, distinta de "nada aconteceu ainda
#    hoje". Com 3 chamadas sequenciais, o histórico do servidor cresce
#    corretamente (cronológico) - a mais recente vira o card, as outras 2
#    (dedup, sem a atual) alimentam a lista.
# ===========================================================================
corpo_msg_vazia = corpo_da_funcao(html, "atualizarMensagemListaVazia")
assert "Aguardando chamadas" in corpo_msg_vazia
assert "Nenhuma chamada anterior hoje" in corpo_msg_vazia
assert "atualizarMensagemListaVazia" in corpo_da_funcao(html, "inserirChamada")
assert "atualizarMensagemListaVazia" in corpo_da_funcao(html, "recarregarHistorico")
ok("atualizarMensagemListaVazia() distingue 'nada hoje' de 'só a chamada atual' e roda nos dois fluxos")

resp = c.post(f"/atendimento/recepcao/filas/{ids['fila_c']}/chamar", data={"codigo": "FC001"}, follow_redirects=True)
assert resp.status_code == 200
ws.get_received(NAMESPACE)  # 'chamada' ao vivo, irrelevante aqui - o foco é o reconnect

ws_p3 = socketio.test_client(app, namespace=NAMESPACE)
ws_p3.get_received(NAMESPACE)
ws_p3.emit("entrar_tv", {"tv_id": ids["tv_p3"]}, namespace=NAMESPACE)
recebidos = ws_p3.get_received(NAMESPACE)
ev_entrou = next(ev for ev in recebidos if ev["name"] == "entrou")
assert ev_entrou["args"][0]["total_hoje"] == 1
assert ev_entrou["args"][0]["historico"] == [{"tipo": "senha", "local": "Guiche 01", "senha": "FC001"}]
ok("Com 1 chamada hoje, o histórico tem exatamente 1 item - vira o card; lista sem itens 'de sobra' é o esperado, não bug")

resp = c.post(f"/atendimento/recepcao/filas/{ids['fila_c']}/finalizar", data={}, follow_redirects=True)
assert resp.status_code == 200
ws.get_received(NAMESPACE)
resp = c.post(f"/atendimento/recepcao/filas/{ids['fila_c']}/chamar", data={"codigo": "FC002"}, follow_redirects=True)
assert resp.status_code == 200
ws.get_received(NAMESPACE)
resp = c.post(f"/atendimento/recepcao/filas/{ids['fila_c']}/finalizar", data={}, follow_redirects=True)
assert resp.status_code == 200
ws.get_received(NAMESPACE)
resp = c.post(f"/atendimento/recepcao/filas/{ids['fila_c']}/chamar", data={"codigo": "FC003"}, follow_redirects=True)
assert resp.status_code == 200
ws.get_received(NAMESPACE)

ws_p3b = socketio.test_client(app, namespace=NAMESPACE)
ws_p3b.get_received(NAMESPACE)
ws_p3b.emit("entrar_tv", {"tv_id": ids["tv_p3"]}, namespace=NAMESPACE)
recebidos = ws_p3b.get_received(NAMESPACE)
ev_entrou2 = next(ev for ev in recebidos if ev["name"] == "entrou")
assert ev_entrou2["args"][0]["total_hoje"] == 3
codigos = [item.get("senha") for item in ev_entrou2["args"][0]["historico"]]
assert codigos == ["FC001", "FC002", "FC003"]  # cronológico - mais antiga primeiro
ok("3 chamadas sequenciais: histórico com as 3 em ordem cronológica - JS deixa a mais recente no card e as outras 2 na lista (mais recente primeiro, sem duplicar)")

# ===========================================================================
# M) Correção do Problema 4: watermark (.gf-watermark, gestfila.css) fica
#    fixo no canto inferior direito da viewport e sobrepunha o texto do
#    rodapé ("Última atualização", mesmo canto). A página do painel agora
#    sobrescreve `bottom` pra reposicionar o watermark acima da barra do
#    rodapé, sem tocar gestfila.css (compartilhado com o resto do sistema).
# ===========================================================================
indice_link_gestfila = html.index('css/gestfila.css')
indice_override_watermark = html.index(".gf-watermark { bottom: 3.5rem; }")
assert indice_override_watermark > indice_link_gestfila  # vem depois no cascade -> vence o empate de especificidade
ok("Painel sobrescreve .gf-watermark (bottom) DEPOIS do <link> de gestfila.css - a sobrescrita vence no cascade")

# a barra do rodapé (id="ultima-atualizacao") tem que existir e não pode ter
# nenhum estilo de posicionamento próprio que a jogue pra fora do fluxo
# normal do <footer> - a correção é só no watermark, o rodapé continua
# sendo texto normal dentro da barra.
assert 'id="ultima-atualizacao"' in html
corpo_footer_css = html[html.index("footer {"):html.index("}", html.index("footer {")) + 1]
assert "position:" not in corpo_footer_css.replace(" ", "")
ok("Rodapé continua em fluxo normal (sem position própria) - só o watermark foi reposicionado")

# ===========================================================================
# N) Correção do Bloco 0: 'Última atualização' ficava travado no placeholder
#    "—" indefinidamente quando a chamada em destaque chegava por
#    reconexão/reload em vez de evento ao vivo nesta sessão - NÃO é
#    regressão do ajuste do watermark (confirmado por diff contra o backup
#    pré-redesign: o comportamento antigo já não tocava esse campo na
#    reconstrução de histórico). recarregarHistorico() agora também
#    atualiza o campo, refletindo o momento da conexão/reconexão.
# ===========================================================================
html_bloco0 = app.test_client().get("/painel/tv/tv-redesign").data.decode("utf-8")
corpo_recarregar_atualizado = corpo_da_funcao(html_bloco0, "recarregarHistorico")
assert "elAtualizacao.textContent" in corpo_recarregar_atualizado
ok("recarregarHistorico() agora atualiza 'Última atualização' (não fica mais travado em '—' na reconexão)")

corpo_inserir_atualizado = corpo_da_funcao(html_bloco0, "inserirChamada")
assert corpo_inserir_atualizado.count("elAtualizacao.textContent") == 2  # repetir + chamada nova, sem mudança
ok("inserirChamada() continua com os 2 pontos originais intactos (repetir + chamada nova), sem duplicar lógica")

# ===========================================================================
# O) Correção do Bloco 1: espaço morto no card principal e na lista lateral.
#    1.1 - `grid-template-rows: 1fr` + `height: 100%` no card garantem que
#          ele ocupe a altura real disponível (não dependendo de
#          `align-content: normal` implícito), pra `justify-content: center`
#          centralizar o bloco selo+senha+local de verdade.
#    1.2 - `justify-content: center` na coluna "Últimas chamadas" evita a
#          lista ficar ancorada no topo com uma sobra grande embaixo.
# ===========================================================================
html_bloco1 = app.test_client().get("/painel/tv/tv-redesign").data.decode("utf-8")

corpo_conteudo_principal = html_bloco1[
    html_bloco1.index(".conteudo-principal {"):html_bloco1.index("}", html_bloco1.index(".conteudo-principal {")) + 1
]
assert "grid-template-rows: 1fr" in corpo_conteudo_principal

corpo_chamada_atual_css = html_bloco1[
    html_bloco1.index(".chamada-atual {"):html_bloco1.index("}", html_bloco1.index(".chamada-atual {")) + 1
]
assert "height: 100%" in corpo_chamada_atual_css
assert "justify-content: center" in corpo_chamada_atual_css
ok("Card 'Chamando agora' tem altura garantida (grid-template-rows: 1fr + height: 100%) e centraliza o conteúdo")

corpo_ultimas_chamadas_css = html_bloco1[
    html_bloco1.index(".ultimas-chamadas {"):html_bloco1.index("}", html_bloco1.index(".ultimas-chamadas {")) + 1
]
assert "justify-content: center" in corpo_ultimas_chamadas_css
assert "height: 100%" in corpo_ultimas_chamadas_css
ok("Coluna 'Últimas chamadas' centraliza título+lista como bloco, sem sobra ancorada só embaixo")

# ===========================================================================
# P) Bloco 2 (acessibilidade): prioridade não pode depender só da cor de
#    fundo do card - selo textual "Prioritário" (+ ícone SVG inline) some/
#    aparece ligado ao MESMO sinal que já controla a cor (`dados.cor`),
#    nunca um critério novo reimplementado no JS - cor e selo nunca saem
#    dessincronizados.
# ===========================================================================
html_bloco2 = app.test_client().get("/painel/tv/tv-redesign").data.decode("utf-8")

assert 'id="chamada-badge-prioritario"' in html_bloco2
assert 'hidden' in html_bloco2[html_bloco2.index('id="chamada-badge-prioritario"') - 40:html_bloco2.index('id="chamada-badge-prioritario"') + 40]
assert "<svg" in html_bloco2[html_bloco2.index('id="chamada-badge-prioritario"'):html_bloco2.index('id="chamada-badge-prioritario"') + 300]
ok("Selo 'Prioritário' existe no HTML, oculto por padrão (hidden), com ícone SVG inline")

corpo_render_bloco2 = corpo_da_funcao(html_bloco2, "renderizarChamadaAtual")
assert "elBadgePrioritario.hidden = false;" in corpo_render_bloco2
assert "elBadgePrioritario.hidden = true;" in corpo_render_bloco2
# o selo é decidido no MESMO bloco if/else que já decide a cor - não é um
# `if` novo e independente que poderia dessincronizar dos dois.
bloco_if_cor = corpo_render_bloco2[corpo_render_bloco2.index("if (dados.cor)"):]
assert "elBadgePrioritario.hidden = false;" in bloco_if_cor[:bloco_if_cor.index("elBadgePrioritario.hidden = true;")]
ok("Selo aparece/some dentro do MESMO if/else que decide dados.cor - nunca um critério de prioridade novo/duplicado")

corpo_badge_css = html_bloco2[html_bloco2.index(".chamada-badge-prioritario {"):html_bloco2.index("}", html_bloco2.index(".chamada-badge-prioritario {")) + 1]
assert "background: #fff" in corpo_badge_css
corpo_badge_hidden_css = html_bloco2[
    html_bloco2.index(".chamada-badge-prioritario[hidden]"):html_bloco2.index("}", html_bloco2.index(".chamada-badge-prioritario[hidden]")) + 1
]
assert "display: none" in corpo_badge_hidden_css
ok("Selo usa fundo fixo de alto contraste (não a cor dinâmica do card) e tem [hidden] explícito vencendo o display:inline-flex")

# Fluxo real já coberto nos blocos G/G2 acima: FA003 (tipo prioritário,
# Senha.prioridade=True) inclui 'cor' no payload; FA004 (tipo "Normal" com
# cor cadastrada por engano, Senha.prioridade=False - o próprio bug do
# Problema 1) NÃO inclui - é exatamente o mesmo campo que os asserts acima
# comprovam que decide o selo, então os dois ficam sincronizados por
# construção, sem duplicar o gate de prioridade em lugar nenhum do JS.
ok("Selo usa o mesmo campo 'cor' já validado nos blocos G/G2 (prioritária mostra, tipo não-prioritário com cor cadastrada não mostra)")

print()
print(f"TODOS OS {len(passos)} TESTES DO REDESIGN DO PAINEL DE TV PASSARAM")
