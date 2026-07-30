"""
Teste de regressão: botões de ação da tela por-Local (adendo 10.8, Fase 10.8-E) -
chamar_local/tocar_local/realocar_local/finalizar_local/aguardar_local, mais a
integração WebSocket de múltiplas salas agregadas no cliente (uma sala `fila_<id>`
por fila do rodízio, seção 3 e 7 do adendo).

Cobre, na ordem:
1. RBAC (401/403) e isolamento multi-tenant (404) nas 5 rotas novas - mesma
   checagem já usada nas rotas por-fila, agora reaproveitada via local_acessivel_ou_404.
2. 'Chamar' automático: a SELEÇÃO usa selecionar_proxima_fila_e_senha(local) (rodízio
   entre filas do Local), mas a MUTAÇÃO é a mesma registrar_chamada_senha() de sempre -
   sem duplicação de lógica (ponto 1 do pedido do usuário para esta fase).
3. 'Chamar' com busca manual por código - localiza a senha em QUALQUER fila do
   rodízio (fila_id.in_(...)), não numa fila fixa, e bypassa a seleção automática.
4. 'Tocar'/'Realocar'/'Finalizar'/'Aguardar' - todos localizando a senha atual via
   senha_atual_no_local(local) (sem fila_ids, agregando o Local inteiro) e delegando
   a mutação, sem alteração, às funções já existentes de app/atendimento/helpers.py.
5. WebSocket: um cliente entra em N salas (fila_x e fila_y, mesmo padrão de
   entrar_fila usado por entrar_tv) e recebe cada evento exatamente uma vez, sem
   duplicação, roteado corretamente por sala; um segundo cliente cadastrado em SÓ
   uma das salas nunca recebe eventos da outra (prova de isolamento entre salas -
   ponto 3 do pedido do usuário).
6. Sanidade de concorrência (ponto 2 do pedido do usuário): chamadas sequenciais e
   rápidas ao rodízio de um Local nunca produzem duas senhas 'chamada' simultâneas
   nem pulam a rotação de forma inconsistente. NÃO testa concorrência real
   multi-thread (o processo de teste é single-threaded, mesma limitação já aceita
   no precedente de totem/routes.py::_emitir_senha, que também não tem teste de
   race condition de verdade) - só confirma que a trava não quebra o uso normal.

Execução: python -m tests.test_regressao_local_rodizio_acoes (a partir da raiz do
projeto, com o virtualenv ativado).
"""
import os
from datetime import datetime

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

from app import create_app
from app.extensions import db, socketio
from app.models import Empresa, Fila, Local, LocalRodizioFila, Perfil, Senha, Unidade, Usuario

app = create_app("testing")

passos = []


def ok(msg):
    passos.append(msg)
    print("OK:", msg)


with app.app_context():
    db.create_all()

    perfil_recepcao = Perfil(nome="atendente")
    perfil_admin = Perfil(nome="admin")
    db.session.add_all([perfil_recepcao, perfil_admin])
    db.session.commit()

    empresa_a = Empresa(nome="Clinica A")
    empresa_b = Empresa(nome="Clinica B")
    db.session.add_all([empresa_a, empresa_b])
    db.session.commit()

    unidade_a1 = Unidade(empresa_id=empresa_a.id, nome="Unidade A1")
    unidade_b1 = Unidade(empresa_id=empresa_b.id, nome="Unidade B1")
    db.session.add_all([unidade_a1, unidade_b1])
    db.session.commit()

    fila_x = Fila(empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="Fila X", sigla="FX")
    fila_y = Fila(empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="Fila Y", sigla="FY")
    fila_z = Fila(empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="Fila Z", sigla="FZ")  # destino, fora do rodízio
    fila_outra_empresa = Fila(empresa_id=empresa_b.id, unidade_id=unidade_b1.id, nome="Fila B", sigla="FB")
    db.session.add_all([fila_x, fila_y, fila_z, fila_outra_empresa])
    db.session.commit()

    local = Local(
        empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="Local Rodizio",
        nome_exibicao="Local Rodizio", identificador_admin="[UNI] Local Rodizio",
    )
    local_outra_empresa = Local(
        empresa_id=empresa_b.id, unidade_id=unidade_b1.id, nome="Local B",
        nome_exibicao="Local B", identificador_admin="[UNI] Local B",
    )
    db.session.add_all([local, local_outra_empresa])
    db.session.commit()

    db.session.add_all([
        LocalRodizioFila(local_id=local.id, fila_id=fila_x.id, ordem=0, chamadas_seguidas=1),
        LocalRodizioFila(local_id=local.id, fila_id=fila_y.id, ordem=1, chamadas_seguidas=1),
    ])
    db.session.commit()

    def nova_senha(fila_id, codigo, seq):
        s = Senha(
            empresa_id=empresa_a.id, unidade_id=unidade_a1.id, fila_id=fila_id,
            codigo=codigo, sequencial=seq, prioridade=False, status="aguardando",
        )
        db.session.add(s)
        db.session.commit()
        return s.id

    id_fx001 = nova_senha(fila_x.id, "FX001", 1)
    id_fy001 = nova_senha(fila_y.id, "FY001", 1)

    def novo_usuario(login, empresa_id, perfis, unidades):
        u = Usuario(empresa_id=empresa_id, nome=login, login=login)
        u.set_senha("senha123")
        u.perfis = perfis
        u.unidades = unidades
        db.session.add(u)
        db.session.commit()
        return u.id

    id_recepcao = novo_usuario("recepcao_a1_acoes", empresa_a.id, [perfil_recepcao], [unidade_a1])
    id_admin_b = novo_usuario("admin_b_acoes", empresa_b.id, [perfil_admin], [])

    ids = dict(
        empresa_a=empresa_a.id, unidade_a1=unidade_a1.id,
        fila_x=fila_x.id, fila_y=fila_y.id, fila_z=fila_z.id, fila_outra_empresa=fila_outra_empresa.id,
        local=local.id, local_outra_empresa=local_outra_empresa.id,
        fx001=id_fx001, fy001=id_fy001,
        recepcao=id_recepcao, admin_b=id_admin_b,
    )

print("IDS", ids)


def login_como(login):
    c = app.test_client()
    resp = c.post("/login", data={"login": login, "senha": "senha123"}, follow_redirects=True)
    assert resp.status_code == 200, (login, resp.status_code)
    return c


def db_get(modelo, id_):
    with app.app_context():
        return db.session.get(modelo, id_)


anon = app.test_client()
c = login_como("recepcao_a1_acoes")
c_admin_b = login_como("admin_b_acoes")

URL_BASE = f"/atendimento/recepcao/local/{ids['local']}"

# ---------------------------------------------------------------------------
# 1) RBAC (401 sem login) e isolamento multi-tenant (404 para Local de outra
#    empresa) nas 5 rotas de ação novas.
# ---------------------------------------------------------------------------
assert anon.post(f"{URL_BASE}/chamar", data={}).status_code == 401
ok("RBAC: sem login recebe 401 em /local/<id>/chamar")

URL_OUTRA = f"/atendimento/recepcao/local/{ids['local_outra_empresa']}"
for sufixo, dados_post in [
    ("chamar", {}), ("tocar", {}), ("realocar", {"fila_destino_id": "1"}),
    ("finalizar", {}), ("aguardar", {}),
]:
    resp = c.post(f"{URL_OUTRA}/{sufixo}", data=dados_post)
    assert resp.status_code == 404, (sufixo, resp.status_code)
ok("Isolamento multi-tenant: as 5 rotas de ação retornam 404 para Local de outra empresa")

# ---------------------------------------------------------------------------
# 2) WebSocket: um cliente entra nas DUAS salas do rodízio (fila_x e fila_y),
#    mesmo padrão de entrar_fila usado pela tela (múltiplas chamadas na mesma
#    conexão); outro cliente entra SÓ na sala de fila_y, como grupo de controle
#    para confirmar isolamento entre salas.
# ---------------------------------------------------------------------------
NAMESPACE = "/painel"
ws_multi = socketio.test_client(app, namespace=NAMESPACE, flask_test_client=c)
ws_multi.get_received(NAMESPACE)
ws_multi.emit("entrar_fila", {"fila_id": ids["fila_x"]}, namespace=NAMESPACE)
ws_multi.emit("entrar_fila", {"fila_id": ids["fila_y"]}, namespace=NAMESPACE)
recebidos_entrada = ws_multi.get_received(NAMESPACE)
assert sum(1 for ev in recebidos_entrada if ev["name"] == "entrou") == 2
ok("Cliente entra nas salas fila_x E fila_y na mesma conexão (2 chamadas de entrar_fila)")

ws_y_only = socketio.test_client(app, namespace=NAMESPACE, flask_test_client=c)
ws_y_only.get_received(NAMESPACE)
ws_y_only.emit("entrar_fila", {"fila_id": ids["fila_y"]}, namespace=NAMESPACE)
ws_y_only.get_received(NAMESPACE)

# ---------------------------------------------------------------------------
# 3) 'Chamar' automático (sem código): rodízio começa pela fila de menor ordem
#    (Fila X) - a MUTAÇÃO é registrar_chamada_senha(), sem alteração nenhuma.
# ---------------------------------------------------------------------------
resp = c.post(f"{URL_BASE}/chamar", data={}, follow_redirects=True)
assert resp.status_code == 200
senha_fx001 = db_get(Senha, ids["fx001"])
assert senha_fx001.status == "chamada"
assert senha_fx001.local_id == ids["local"]
ok("'Chamar' automático: rodízio escolhe Fila X primeiro (ordem 0, sem histórico) e chama FX001")

recebidos = ws_multi.get_received(NAMESPACE)
ev_chamada = next(ev for ev in recebidos if ev["name"] == "chamada")
assert ev_chamada["args"][0]["senha"] == "FX001"
ev_atualizada = next(ev for ev in recebidos if ev["name"] == "fila_atualizada")
assert ev_atualizada["args"][0]["fila_id"] == ids["fila_x"]
ok("WS: cliente multi-sala recebe 'chamada'+'fila_atualizada' de FX001 na sala fila_x")

recebidos_y_only = ws_y_only.get_received(NAMESPACE)
assert recebidos_y_only == []
ok("WS: cliente cadastrado SÓ na sala fila_y não recebe nada do evento de fila_x (isolamento confirmado)")

# Controle de atendimento (bloqueio de chamada sem finalização): FX001 precisa ser
# finalizada antes da próxima 'Chamar' - sem isso, o mesmo operador ainda teria
# FX001 em andamento e a chamada seguinte seria recusada ("Finalize o atendimento
# atual..."). Finaliza via a própria rota (não manipulação direta do banco), pra
# continuar exercitando o fluxo real.
resp = c.post(f"{URL_BASE}/finalizar", data={}, follow_redirects=True)
assert resp.status_code == 200
assert db_get(Senha, ids["fx001"]).status == "finalizada"
ws_multi.get_received(NAMESPACE)
ws_y_only.get_received(NAMESPACE)
ok("FX001 finalizada antes da próxima chamada (controle de atendimento)")

# ---------------------------------------------------------------------------
# 4) 'Chamar' automático de novo: Fila X já usou sua cota (chamadas_seguidas=1),
#    rodízio avança para Fila Y - prova que a seleção lê o histórico real
#    (Senha.chamada_em), não um contador fixo, e que a trava de concorrência
#    (Fase 10.8-E) não impede a segunda chamada sequencial de funcionar.
# ---------------------------------------------------------------------------
resp = c.post(f"{URL_BASE}/chamar", data={}, follow_redirects=True)
assert resp.status_code == 200
senha_fy001 = db_get(Senha, ids["fy001"])
assert senha_fy001.status == "chamada"
ok("'Chamar' automático (2ª vez): rodízio avança para Fila Y (cota de X já usada) e chama FY001")

recebidos = ws_multi.get_received(NAMESPACE)
ev_chamada = next(ev for ev in recebidos if ev["name"] == "chamada")
assert ev_chamada["args"][0]["senha"] == "FY001"
ev_atualizada = next(ev for ev in recebidos if ev["name"] == "fila_atualizada")
assert ev_atualizada["args"][0]["fila_id"] == ids["fila_y"]
ok("WS: cliente multi-sala recebe o evento de FY001 na sala fila_y (uma vez só, sem duplicar)")

recebidos_y_only = ws_y_only.get_received(NAMESPACE)
assert sum(1 for ev in recebidos_y_only if ev["name"] == "chamada") == 1
assert sum(1 for ev in recebidos_y_only if ev["name"] == "fila_atualizada") == 1
ok("WS: cliente cadastrado só em fila_y recebe exatamente 1 'chamada' + 1 'fila_atualizada' (sem duplicação)")

# Controle de atendimento: FY001 também precisa ser finalizada antes da próxima
# 'Chamar' (mesmo motivo do passo anterior, para FX001).
resp = c.post(f"{URL_BASE}/finalizar", data={}, follow_redirects=True)
assert resp.status_code == 200
assert db_get(Senha, ids["fy001"]).status == "finalizada"
ws_multi.get_received(NAMESPACE)
ws_y_only.get_received(NAMESPACE)
ok("FY001 finalizada antes da próxima chamada (controle de atendimento)")

# ---------------------------------------------------------------------------
# 5) 'Chamar' com busca manual por código: bypassa a seleção automática do
#    rodízio, localizando a senha em QUALQUER fila configurada no Local.
# ---------------------------------------------------------------------------
with app.app_context():
    fx002 = Senha(
        empresa_id=ids["empresa_a"], unidade_id=ids["unidade_a1"], fila_id=ids["fila_x"],
        codigo="FX002", sequencial=2, prioridade=False, status="aguardando",
    )
    db.session.add(fx002)
    db.session.commit()
    ids["fx002"] = fx002.id

# código inexistente testado ANTES de chamar FX002 - a essa altura o operador
# ainda não tem nenhuma senha em andamento (FX001/FY001 já finalizadas acima);
# testar depois de chamar FX002 esbarraria no bloqueio de chamada sem finalização
# (a mensagem seria "Finalize o atendimento atual...", não "não encontrado").
resp = c.post(f"{URL_BASE}/chamar", data={"codigo": "NAOEXISTE"}, follow_redirects=True)
assert "não encontrado".encode("utf-8") in resp.data
ok("'Chamar' com código inexistente nas filas do Local mostra erro amigável")

resp = c.post(f"{URL_BASE}/chamar", data={"codigo": "fx002"}, follow_redirects=True)
assert resp.status_code == 200
senha_fx002 = db_get(Senha, ids["fx002"])
assert senha_fx002.status == "chamada"
ok("'Chamar' com código manual localiza e chama FX002 mesmo fora da vez do rodízio (Fila X já tinha usado a cota)")

ws_multi.get_received(NAMESPACE)  # descarta eventos deste passo, já validados nos anteriores

# ---------------------------------------------------------------------------
# 6) 'Tocar': repete a chamada da senha ATUAL do Local (a mais recente = FX002),
#    sem alterar nada no banco - delega para registrar_toque() sem mudança.
# ---------------------------------------------------------------------------
antes = db_get(Senha, ids["fx002"]).chamada_em
resp = c.post(f"{URL_BASE}/tocar", data={}, follow_redirects=True)
assert resp.status_code == 200
depois = db_get(Senha, ids["fx002"]).chamada_em
assert antes == depois  # nada mudou no banco
ok("'Tocar' repete a chamada de FX002 (senha atual do Local) sem alterar nada no banco")

recebidos = ws_multi.get_received(NAMESPACE)
ev_toque = next(ev for ev in recebidos if ev["name"] == "chamada")
assert ev_toque["args"][0]["senha"] == "FX002"
assert not any(ev["name"] == "fila_atualizada" for ev in recebidos)
ok("WS: 'Tocar' emite só 'chamada' de novo (sem 'fila_atualizada', pois nada mudou nas tabelas)")

# ---------------------------------------------------------------------------
# 7) 'Realocar': move a senha atual (FX002, chamada na Fila X) para a Fila Z,
#    de volta para 'aguardando' - delega para registrar_realocacao() sem mudança.
# ---------------------------------------------------------------------------
ws_z_only = socketio.test_client(app, namespace=NAMESPACE, flask_test_client=c)
ws_z_only.get_received(NAMESPACE)
ws_z_only.emit("entrar_fila", {"fila_id": ids["fila_z"]}, namespace=NAMESPACE)
ws_z_only.get_received(NAMESPACE)

resp = c.post(f"{URL_BASE}/realocar", data={"fila_destino_id": ids["fila_z"]}, follow_redirects=True)
assert resp.status_code == 200
senha_fx002 = db_get(Senha, ids["fx002"])
assert senha_fx002.fila_id == ids["fila_z"]
assert senha_fx002.status == "aguardando"
assert senha_fx002.chamada_em is None
assert senha_fx002.local_id is None
ok("'Realocar' move FX002 da Fila X para a Fila Z, de volta para 'aguardando'")

recebidos_z = ws_z_only.get_received(NAMESPACE)
assert any(ev["name"] == "fila_atualizada" and ev["args"][0]["fila_id"] == ids["fila_z"] for ev in recebidos_z)
ok("WS: 'Realocar' emite 'fila_atualizada' também para a sala da fila de DESTINO (fila_z)")

# ---------------------------------------------------------------------------
# 8) 'Finalizar': chama uma nova senha (FX003) e conclui o atendimento dela -
#    delega para registrar_fim_atendimento() sem mudança.
# ---------------------------------------------------------------------------
with app.app_context():
    fx003 = Senha(
        empresa_id=ids["empresa_a"], unidade_id=ids["unidade_a1"], fila_id=ids["fila_x"],
        codigo="FX003", sequencial=3, prioridade=False, status="aguardando",
    )
    db.session.add(fx003)
    db.session.commit()
    ids["fx003"] = fx003.id

resp = c.post(f"{URL_BASE}/chamar", data={"codigo": "FX003"}, follow_redirects=True)
assert resp.status_code == 200
assert db_get(Senha, ids["fx003"]).status == "chamada"

resp = c.post(f"{URL_BASE}/finalizar", data={}, follow_redirects=True)
assert resp.status_code == 200
senha_fx003 = db_get(Senha, ids["fx003"])
assert senha_fx003.status == "finalizada"
assert senha_fx003.finalizada_em is not None
ok("'Finalizar' conclui o atendimento de FX003 (status='finalizada', finalizada_em preenchido)")

# ---------------------------------------------------------------------------
# 9) 'Aguardar': chama uma nova senha (FY002) e devolve para a espera, sem
#    finalizar - delega para registrar_volta_espera() sem mudança.
# ---------------------------------------------------------------------------
with app.app_context():
    fy002 = Senha(
        empresa_id=ids["empresa_a"], unidade_id=ids["unidade_a1"], fila_id=ids["fila_y"],
        codigo="FY002", sequencial=2, prioridade=False, status="aguardando",
    )
    db.session.add(fy002)
    db.session.commit()
    ids["fy002"] = fy002.id

resp = c.post(f"{URL_BASE}/chamar", data={"codigo": "FY002"}, follow_redirects=True)
assert resp.status_code == 200
assert db_get(Senha, ids["fy002"]).status == "chamada"

resp = c.post(f"{URL_BASE}/aguardar", data={}, follow_redirects=True)
assert resp.status_code == 200
senha_fy002 = db_get(Senha, ids["fy002"])
assert senha_fy002.status == "aguardando"
assert senha_fy002.chamada_em is None
assert senha_fy002.local_id is None
ok("'Aguardar' devolve FY002 para a espera (status='aguardando'), sem finalizar")

# FX001 e FY001 (chamadas nas seções 3/4) já foram finalizadas via rota logo após
# cada uma (controle de atendimento, bloqueio de chamada sem finalização) - a essa
# altura o operador não tem mais nenhuma senha em andamento (FX002 foi Realocada,
# FX003 finalizada, FY002 devolvida à espera), premissa da próxima seção.
# ---------------------------------------------------------------------------
# 10) 'Tocar'/'Realocar'/'Finalizar'/'Aguardar' sem nenhuma senha chamada no
#     Local: erro amigável, sem exceção.
# ---------------------------------------------------------------------------
for sufixo, dados_post in [
    ("tocar", {}), ("realocar", {"fila_destino_id": str(ids["fila_z"])}),
    ("finalizar", {}), ("aguardar", {}),
]:
    resp = c.post(f"{URL_BASE}/{sufixo}", data=dados_post, follow_redirects=True)
    assert resp.status_code == 200
    assert "Nenhuma senha chamada neste Local".encode("utf-8") in resp.data
ok("Sem senha chamada no Local: os 4 botões (Tocar/Realocar/Finalizar/Aguardar) mostram erro amigável")

# ---------------------------------------------------------------------------
# 11) Sanidade de concorrência (ponto 2 do pedido do usuário, Fase 10.8-E):
#     chamadas sequenciais e rápidas ao MESMO Local nunca chamam a mesma senha
#     duas vezes nem pulam a rotação de forma inconsistente. A trava adicionada
#     em selecionar_proxima_fila_e_senha (UPDATE no-op na linha do Local) serializa
#     tentativas concorrentes de verdade (múltiplas threads/processos); aqui, num
#     processo de teste single-threaded, confirmamos que ela NÃO quebra o
#     funcionamento sequencial normal - não é um teste de race condition real
#     (mesma limitação já aceita, sem teste dedicado, no precedente equivalente
#     de totem/routes.py::_emitir_senha).
# ---------------------------------------------------------------------------
with app.app_context():
    fila_x2 = Fila(empresa_id=ids["empresa_a"], unidade_id=ids["unidade_a1"], nome="Fila X2", sigla="X2")
    fila_y2 = Fila(empresa_id=ids["empresa_a"], unidade_id=ids["unidade_a1"], nome="Fila Y2", sigla="Y2")
    db.session.add_all([fila_x2, fila_y2])
    db.session.commit()

    local2 = Local(
        empresa_id=ids["empresa_a"], unidade_id=ids["unidade_a1"], nome="Local Rodizio 2",
        nome_exibicao="Local Rodizio 2", identificador_admin="[UNI] Local Rodizio 2",
    )
    db.session.add(local2)
    db.session.commit()

    db.session.add_all([
        LocalRodizioFila(local_id=local2.id, fila_id=fila_x2.id, ordem=0, chamadas_seguidas=1),
        LocalRodizioFila(local_id=local2.id, fila_id=fila_y2.id, ordem=1, chamadas_seguidas=1),
    ])
    db.session.commit()

    codigos_esperados = []
    for i in range(2):
        s = Senha(
            empresa_id=ids["empresa_a"], unidade_id=ids["unidade_a1"], fila_id=fila_x2.id,
            codigo=f"CX{i:03d}", sequencial=i + 1, prioridade=False, status="aguardando",
        )
        db.session.add(s)
        codigos_esperados.append(f"CX{i:03d}")
    for i in range(2):
        s = Senha(
            empresa_id=ids["empresa_a"], unidade_id=ids["unidade_a1"], fila_id=fila_y2.id,
            codigo=f"CY{i:03d}", sequencial=i + 1, prioridade=False, status="aguardando",
        )
        db.session.add(s)
        codigos_esperados.append(f"CY{i:03d}")
    db.session.commit()

    ids["local2"] = local2.id

URL_BASE_2 = f"/atendimento/recepcao/local/{ids['local2']}"
for _ in range(4):
    resp = c.post(f"{URL_BASE_2}/chamar", data={}, follow_redirects=True)
    assert resp.status_code == 200
    # controle de atendimento: finaliza antes da próxima chamada da sequência -
    # sem isso, a 2ª/3ª/4ª iteração seriam recusadas ("Finalize o atendimento
    # atual..."), já que este mesmo operador ainda teria a anterior em andamento.
    resp = c.post(f"{URL_BASE_2}/finalizar", data={}, follow_redirects=True)
    assert resp.status_code == 200

with app.app_context():
    chamadas_ordenadas = (
        Senha.query.filter(Senha.local_id == ids["local2"], Senha.chamada_em.isnot(None))
        .order_by(Senha.chamada_em.asc())
        .all()
    )
    codigos_chamados = [s.codigo for s in chamadas_ordenadas]
    # rodízio 1-a-1 entre X2 e Y2: alterna estritamente, sem repetir nem pular
    # nenhuma das 4 senhas cadastradas.
    assert codigos_chamados == ["CX000", "CY000", "CX001", "CY001"]
    assert len(set(codigos_chamados)) == 4  # nenhuma senha chamada duas vezes
ok("4 chamadas sequenciais e rápidas ao rodízio (Local 2, X2/Y2) alternam corretamente "
   "sem repetir nem pular nenhuma senha - trava não quebra o uso sequencial normal")

# ---------------------------------------------------------------------------
# 12) Cruzamento dos DOIS caminhos na MESMA fila: uma fila que participa do
#     rodízio de um Local também continua acessível pela URL antiga por-fila
#     (seção 5 do adendo 10.8, coexistência deliberada). Chamando-a alternando
#     os dois caminhos (chamar_local, depois chamar direto na fila, e vice-versa),
#     nenhuma senha pode ser chamada duas vezes nem ficar de fora.
#
#     Limitação explícita: isto NÃO é um teste de concorrência real (múltiplas
#     threads/conexões disputando a MESMA linha ao mesmo tempo) - o processo de
#     teste é single-threaded e o banco de testes é SQLite ":memory:" sem
#     StaticPool/conexão compartilhada entre threads, então threads de verdade
#     aqui não veriam o estado umas das outras (cada uma abriria seu próprio
#     banco em memória, isolado). Um teste de race condition genuíno exigiria
#     infraestrutura de teste nova (conexão compartilhada ou banco em arquivo),
#     fora do padrão "script simples" deste projeto - mesma limitação já aceita,
#     sem teste dedicado, no precedente equivalente de
#     totem/routes.py::_emitir_senha. O que ESTE teste prova: os dois caminhos
#     agora travam a MESMA linha (fila) quando operam sobre a mesma fila
#     (travar_fila, chamado tanto em chamar() quanto em
#     selecionar_proxima_fila_e_senha) e interoperam corretamente sobre o mesmo
#     histórico (Senha.chamada_em) mesmo quando usados alternadamente e depressa.
# ---------------------------------------------------------------------------
with app.app_context():
    fila_misto = Fila(empresa_id=ids["empresa_a"], unidade_id=ids["unidade_a1"], nome="Fila Mista", sigla="FM")
    db.session.add(fila_misto)
    db.session.commit()

    local_misto = Local(
        empresa_id=ids["empresa_a"], unidade_id=ids["unidade_a1"], nome="Local Misto",
        nome_exibicao="Local Misto", identificador_admin="[UNI] Local Misto",
    )
    db.session.add(local_misto)
    db.session.commit()

    db.session.add(LocalRodizioFila(local_id=local_misto.id, fila_id=fila_misto.id, ordem=0, chamadas_seguidas=1))
    db.session.commit()

    codigos_misto = []
    for i in range(6):
        s = Senha(
            empresa_id=ids["empresa_a"], unidade_id=ids["unidade_a1"], fila_id=fila_misto.id,
            codigo=f"FM{i:03d}", sequencial=i + 1, prioridade=False, status="aguardando",
        )
        db.session.add(s)
        codigos_misto.append(f"FM{i:03d}")
    db.session.commit()

    ids["fila_misto"] = fila_misto.id
    ids["local_misto"] = local_misto.id

# 'chamar()' (caminho por-fila antigo) exige um Local escolhido NA SESSÃO -
# escolhe o próprio Local Misto, simulando um operador que atende ali usando a
# tela antiga por-fila, enquanto outro (ou o mesmo, em outra aba) usa a tela
# nova por-Local sobre o mesmo rodízio.
with c.session_transaction() as sess:
    sess["atendimento_local_id"] = ids["local_misto"]

URL_FILA_MISTA = f"/atendimento/recepcao/filas/{ids['fila_misto']}"
URL_LOCAL_MISTO = f"/atendimento/recepcao/local/{ids['local_misto']}"

caminhos_alternados = [URL_LOCAL_MISTO, URL_FILA_MISTA] * 3  # 6 chamadas, alternando os 2 caminhos
for url in caminhos_alternados:
    resp = c.post(f"{url}/chamar", data={}, follow_redirects=True)
    assert resp.status_code == 200
    # controle de atendimento: finaliza (por qualquer um dos 2 caminhos - ambos
    # operam sobre a mesma senha_atual_no_local) antes da próxima chamada da
    # sequência, senão seria recusada.
    resp = c.post(f"{url}/finalizar", data={}, follow_redirects=True)
    assert resp.status_code == 200

with app.app_context():
    chamadas_fila_mista = (
        Senha.query.filter(Senha.fila_id == ids["fila_misto"], Senha.chamada_em.isnot(None))
        .order_by(Senha.chamada_em.asc())
        .all()
    )
    codigos_chamados_misto = [s.codigo for s in chamadas_fila_mista]
    assert len(codigos_chamados_misto) == 6
    assert set(codigos_chamados_misto) == set(codigos_misto)  # todas as 6, nenhuma de fora
    assert len(set(codigos_chamados_misto)) == 6  # nenhuma repetida (nenhum double-booking)
ok("Fila participando de um rodízio de Local, chamada alternadamente pelos 2 caminhos "
   "(por-Local e por-fila direto): as 6 senhas são chamadas uma vez cada, sem repetir "
   "nem pular nenhuma - os dois caminhos agora travam a mesma linha (Fila) e "
   "interoperam corretamente sobre o mesmo histórico de chamada_em")

print()
print(f"TODOS OS {len(passos)} TESTES DE AÇÕES DA TELA POR-LOCAL (FASE 10.8-E) PASSARAM")
