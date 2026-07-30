"""
Teste de regressão da seção 10.2 do BLUEPRINT (tela unificada de Atendimento):
cobre os 5 botões de ação (Chamar, Tocar, Realocar, Finalizar, Aguardar), o botão
"Chamar" individual da tabela "Senhas em espera", a coluna Observações, o endpoint
JSON /dados (usado pelo front-end para re-sincronizar as tabelas) e a emissão
correta de WebSocket ('chamada' para a TV, 'fila_atualizada' para a própria tela)
em cada ação relevante - além do RBAC e isolamento multi-tenant já estabelecidos.

Inclui também (seções 11/12, adicionadas na auditoria de IDOR): isolamento por
UNIDADE dentro da MESMA empresa (fila_acessivel_ou_404/senha_acessivel_ou_404
com empresa_id batendo mas unidade_id não) e o cruzamento de SETOR (perfil
'atendente' tentando rotas do setor 'coleta' e vice-versa, via exigir_acesso_setor).

Execução: python -m tests.test_regressao_fase10_2 (a partir da raiz do projeto, com
o virtualenv ativado).
"""
import os

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

from app import create_app
from app.extensions import db, socketio
from app.models import Empresa, Fila, Local, LogAtendimento, Perfil, Senha, Tv, Unidade, Usuario

app = create_app("testing")

passos = []


def ok(msg):
    passos.append(msg)
    print("OK:", msg)


def _sem_slug_audio(payload):
    """Ignora local_audio_slug (adendo do áudio pré-gravado) nos asserts de payload
    deste teste, que são sobre tipo/local/senha - o valor do slug depende de quais
    .wav estão no repo e é coberto por test_regressao_audio_pregravado.py."""
    return {k: v for k, v in payload.items() if k != "local_audio_slug"}


with app.app_context():
    db.create_all()

    perfil_recepcao = Perfil(nome="atendente")
    perfil_coleta = Perfil(nome="executor")
    perfil_gerencial = Perfil(nome="gerencial")
    perfil_admin = Perfil(nome="admin")
    db.session.add_all([perfil_recepcao, perfil_coleta, perfil_gerencial, perfil_admin])
    db.session.commit()

    empresa_a = Empresa(nome="Clinica A")
    empresa_b = Empresa(nome="Clinica B")
    db.session.add_all([empresa_a, empresa_b])
    db.session.commit()

    unidade_a1 = Unidade(empresa_id=empresa_a.id, nome="Unidade A1")
    # mesma empresa A, mas outra unidade - usada para testar isolamento por unidade
    # (empresa_id bate, unidade_id não deveria bater) via fila_acessivel_ou_404/
    # senha_acessivel_ou_404 (gap identificado na auditoria de IDOR).
    unidade_a2 = Unidade(empresa_id=empresa_a.id, nome="Unidade A2")
    unidade_b1 = Unidade(empresa_id=empresa_b.id, nome="Unidade B1")
    db.session.add_all([unidade_a1, unidade_a2, unidade_b1])
    db.session.commit()

    fila_x = Fila(empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="Fila X", sigla="X")
    fila_y = Fila(empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="Fila Y", sigla="Y")
    fila_z = Fila(empresa_id=empresa_a.id, unidade_id=unidade_a2.id, nome="Fila Z", sigla="Z")
    fila_outra_empresa = Fila(empresa_id=empresa_b.id, unidade_id=unidade_b1.id, nome="Fila B", sigla="B")
    db.session.add_all([fila_x, fila_y, fila_z, fila_outra_empresa])
    db.session.commit()

    tv_a = Tv(empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="TV A1", slug="tv-a1")
    tv_a.filas = [fila_x, fila_y]
    db.session.add(tv_a)
    db.session.commit()

    def novo_usuario(login, empresa_id, perfis, unidades):
        u = Usuario(empresa_id=empresa_id, nome=login, login=login)
        u.set_senha("senha123")
        u.perfis = perfis
        u.unidades = unidades
        db.session.add(u)
        db.session.commit()
        return u.id

    id_recepcao_a = novo_usuario("recepcao_a2", empresa_a.id, [perfil_recepcao], [unidade_a1])
    # perfil 'executor' puro (sem 'atendente'), restrito à unidade A1 - usado só para o
    # teste de cruzamento de setor (seção 12), isolando a variável RBAC da variável
    # de unidade (ambos têm acesso à mesma unidade A1).
    id_coleta_a = novo_usuario("coleta_a2", empresa_a.id, [perfil_coleta], [unidade_a1])
    id_gerencial_a = novo_usuario("gerencial_a2", empresa_a.id, [perfil_gerencial], [unidade_a1])
    id_admin_b = novo_usuario("admin_b2", empresa_b.id, [perfil_admin], [])

    from datetime import datetime, timedelta

    base_t = datetime.utcnow() - timedelta(minutes=10)

    def nova_senha(codigo, seq, criada_em):
        s = Senha(
            empresa_id=empresa_a.id, unidade_id=unidade_a1.id, fila_id=fila_x.id,
            codigo=codigo, sequencial=seq, prioridade=False, status="aguardando", criada_em=criada_em,
        )
        db.session.add(s)
        db.session.commit()
        return s.id

    id_x1 = nova_senha("X001", 1, base_t + timedelta(seconds=1))
    id_x2 = nova_senha("X002", 2, base_t + timedelta(seconds=2))
    id_x3 = nova_senha("X003", 3, base_t + timedelta(seconds=3))

    # senha na Fila Z (unidade A2, mesma empresa A) - usada para testar que
    # senha_acessivel_ou_404 isola por unidade, não só por empresa (seção 11).
    senha_z1 = Senha(
        empresa_id=empresa_a.id, unidade_id=unidade_a2.id, fila_id=fila_z.id,
        codigo="Z001", sequencial=1, prioridade=False, status="aguardando", criada_em=base_t,
    )
    db.session.add(senha_z1)
    db.session.commit()
    id_z1 = senha_z1.id

    # locais (seção 10.3) - cada posto físico cadastrado, escolhido na sessão do
    # operador (não mais texto livre por POST); local_vazio nunca recebe chamada,
    # usado para os testes de "nenhuma senha chamada neste local".
    local1 = Local(
        empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="Guiche 01",
        nome_exibicao="Guiche 01", identificador_admin="[UNI] Guiche 01",
    )
    local2 = Local(
        empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="Guiche 02",
        nome_exibicao="Guiche 02", identificador_admin="[UNI] Guiche 02",
    )
    local3 = Local(
        empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="Guiche 03",
        nome_exibicao="Guiche 03", identificador_admin="[UNI] Guiche 03",
    )
    local_vazio = Local(
        empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="Guiche Vazio",
        nome_exibicao="Guiche Vazio", identificador_admin="[UNI] Guiche Vazio",
    )
    db.session.add_all([local1, local2, local3, local_vazio])
    db.session.commit()

    ids = dict(
        empresa_a=empresa_a.id, empresa_b=empresa_b.id, unidade_a1=unidade_a1.id, unidade_a2=unidade_a2.id,
        fila_x=fila_x.id, fila_y=fila_y.id, fila_z=fila_z.id, fila_outra_empresa=fila_outra_empresa.id, tv_a=tv_a.id,
        recepcao_a=id_recepcao_a, coleta_a=id_coleta_a, gerencial_a=id_gerencial_a, admin_b=id_admin_b,
        x1=id_x1, x2=id_x2, x3=id_x3, z1=id_z1,
        local1=local1.id, local2=local2.id, local3=local3.id, local_vazio=local_vazio.id,
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


def _escolher_local(client, local_id):
    """Simula a troca de Local (seção 10.3) sem passar pela tela HTTP - equivalente a
    escolher um local novo no início do turno ou usar o link 'Trocar local'."""
    with client.session_transaction() as sess:
        sess["atendimento_local_id"] = local_id


NAMESPACE = "/painel"
ws = socketio.test_client(app, namespace=NAMESPACE)
ws.get_received(NAMESPACE)
ws.emit("entrar_tv", {"tv_id": ids["tv_a"]}, namespace=NAMESPACE)
ws.get_received(NAMESPACE)

c = login_como("recepcao_a2")
c_coleta = login_como("coleta_a2")
c_gerencial = login_como("gerencial_a2")
c_admin_b = login_como("admin_b2")

URL_FILA_X = f"/atendimento/recepcao/filas/{ids['fila_x']}"

# ---------------------------------------------------------------------------
# 0) RBAC: gerencial (só relatórios) recebe 403; sem login recebe 401.
# ---------------------------------------------------------------------------
assert c_gerencial.get(URL_FILA_X).status_code == 403
ok("RBAC: perfil 'gerencial' recebe 403 na tela unificada de Atendimento")

assert app.test_client().get(URL_FILA_X).status_code == 401
ok("RBAC: sem login recebe 401")

# ---------------------------------------------------------------------------
# 1) Sem Local escolhido na sessão ainda (1ª interação deste usuário, seção 10.3: o
#    Local não é mais texto livre, precisa ser escolhido antes) -> GET e POST
#    redirecionam para a tela de escolha de Local, sem nenhuma mutação.
# ---------------------------------------------------------------------------
resp = c.get(URL_FILA_X)
assert resp.status_code == 302
assert "/atendimento/local" in resp.headers["Location"]
ok("Sem Local escolhido na sessão, GET na tela de Atendimento redireciona para a escolha de Local")

resp = c.post(f"{URL_FILA_X}/chamar", data={"codigo": "X002"}, follow_redirects=True)
assert resp.status_code == 200
assert "Escolha o seu local de atendimento".encode("utf-8") in resp.data
senha2_intacta_inicio = db_get(Senha, ids["x2"])
assert senha2_intacta_inicio.status == "aguardando"
ok("Sem Local escolhido na sessão, 'Chamar' redireciona para a tela de escolha de Local, sem mutação")

# ---------------------------------------------------------------------------
# 2) Escolhe o Local ("Guiche 01") - válido para o resto do teste, exceto onde
#    explicitamente trocado (cada posto tem sua própria 'senha atual').
# ---------------------------------------------------------------------------
_escolher_local(c, ids["local1"])

resp = c.get(URL_FILA_X)
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
assert "X001" in texto and "X002" in texto and "X003" in texto
ok("Com Local escolhido, GET mostra as 3 senhas em espera, nenhuma 'Senha chamada'")

resp = c.get(f"{URL_FILA_X}/dados")
assert resp.status_code == 200
dados = resp.get_json()
assert dados["senha_atual"] is None
assert len(dados["espera"]) == 3
assert dados["espera"][0]["codigo"] == "X001"  # ordenado por criada_em asc
assert dados["atendidas"] == []
ok("Endpoint JSON /dados retorna senha_atual=None e as 3 senhas em espera, ordenadas")

# ---------------------------------------------------------------------------
# 2) Botão 'Chamar' (sem código = próxima da fila, FIFO).
# ---------------------------------------------------------------------------
_escolher_local(c, ids["local1"])
resp = c.post(f"{URL_FILA_X}/chamar", data={}, follow_redirects=True)
assert resp.status_code == 200
senha1 = db_get(Senha, ids["x1"])
assert senha1.status == "chamada" and senha1.local_id == ids["local1"]
ok("'Chamar' sem código chama a próxima da fila (FIFO) - X001")

recebidos = ws.get_received(NAMESPACE)
ev_chamada = next(ev for ev in recebidos if ev["name"] == "chamada")
assert _sem_slug_audio(ev_chamada["args"][0]) == {"tipo": "senha", "local": "Guiche 01", "senha": "X001"}
ev_atualizada = next(ev for ev in recebidos if ev["name"] == "fila_atualizada")
assert ev_atualizada["args"][0] == {"fila_id": ids["fila_x"]}
ok("'Chamar' emite 'chamada' (TV) e 'fila_atualizada' (tela) na sala da fila")

resp = c.get(f"{URL_FILA_X}/dados")
dados = resp.get_json()
assert dados["senha_atual"]["codigo"] == "X001"
assert len(dados["espera"]) == 2
assert len(dados["atendidas"]) == 1 and dados["atendidas"][0]["codigo"] == "X001"
ok("/dados reflete X001 fora de 'espera' e dentro de 'atendidas' após o Chamar")

# ---------------------------------------------------------------------------
# 3) Botão 'Chamar' individual (por código) na tabela de espera - chama X003
#    fora de ordem, sem alterar X002, num posto diferente (Guiche 02).
# ---------------------------------------------------------------------------
_escolher_local(c, ids["local2"])
resp = c.post(f"{URL_FILA_X}/chamar", data={"codigo": "X003"}, follow_redirects=True)
assert resp.status_code == 200
senha3 = db_get(Senha, ids["x3"])
assert senha3.status == "chamada" and senha3.local_id == ids["local2"]
senha2_intacta = db_get(Senha, ids["x2"])
assert senha2_intacta.status == "aguardando"
ok("'Chamar' individual (botão da linha, via código) chama X003 fora de ordem, sem afetar X002")

# código inexistente / já atendido -> erro amigável, nada muda. Precisa ser num
# local onde ESTE operador não tenha senha em andamento (controle de atendimento,
# bloqueio de chamada sem finalização) - senão o bloqueio "Finalize o atendimento
# atual..." dispara antes mesmo de olhar o código, mascarando o teste. local2 tem
# X003 em andamento (passo anterior) - troca pra local_vazio só pra este passo.
_escolher_local(c, ids["local_vazio"])
resp = c.post(f"{URL_FILA_X}/chamar", data={"codigo": "X999"}, follow_redirects=True)
assert "não encontrado".encode("utf-8") in resp.data
ok("'Chamar' com código inexistente mostra erro amigável, sem efeito colateral")

# ---------------------------------------------------------------------------
# 4) Botão 'Tocar' - repete a chamada do local "Guiche 01" (X001), sem mudar nada.
# ---------------------------------------------------------------------------
_escolher_local(c, ids["local1"])
ws.get_received(NAMESPACE)  # descarta eventos pendentes
resp = c.post(f"{URL_FILA_X}/tocar", data={}, follow_redirects=True)
assert resp.status_code == 200
senha1_apos_tocar = db_get(Senha, ids["x1"])
assert senha1_apos_tocar.chamada_em == senha1.chamada_em
assert senha1_apos_tocar.status == "chamada"
ok("'Tocar' não altera status nem chamada_em da senha atual")

recebidos = ws.get_received(NAMESPACE)
ev_toque = next(ev for ev in recebidos if ev["name"] == "chamada")
# `repetir: True` (bug corrigido no painel/tv.html - antes ausente): sinaliza que isto
# NÃO é uma chamada nova, é a mesma sendo reanunciada - sem isso, a TV duplicava a linha
# na lista e inflava o contador de chamadas a cada "Tocar".
assert _sem_slug_audio(ev_toque["args"][0]) == {"tipo": "senha", "local": "Guiche 01", "senha": "X001", "repetir": True}
assert not any(ev["name"] == "fila_atualizada" for ev in recebidos)
ok("'Tocar' emite 'chamada' de novo para a TV com repetir=True, e NÃO emite 'fila_atualizada'")

_escolher_local(c, ids["local_vazio"])
resp = c.post(f"{URL_FILA_X}/tocar", data={}, follow_redirects=True)
assert "Nenhuma senha chamada neste local".encode("utf-8") in resp.data
ok("'Tocar' em local sem senha chamada mostra erro amigável")

# ---------------------------------------------------------------------------
# 5) Botão 'Realocar' - move X003 (chamada em "Guiche 02") para a Fila Y.
# ---------------------------------------------------------------------------
ws2 = socketio.test_client(app, namespace=NAMESPACE, flask_test_client=c)
ws2.get_received(NAMESPACE)
ws2.emit("entrar_fila", {"fila_id": ids["fila_y"]}, namespace=NAMESPACE)
recebido_entrou = ws2.get_received(NAMESPACE)
assert any(ev["name"] == "entrou" for ev in recebido_entrou)
ok("Socket 'entrar_fila' entra na sala da fila destino com sucesso (usuário autenticado e com acesso)")

_escolher_local(c, ids["local2"])
resp = c.post(
    f"{URL_FILA_X}/realocar",
    data={"fila_destino_id": ids["fila_y"], "nome": "João Pereira"},
    follow_redirects=True,
)
assert resp.status_code == 200
senha3_realocada = db_get(Senha, ids["x3"])
assert senha3_realocada.fila_id == ids["fila_y"]
assert senha3_realocada.status == "aguardando"
assert senha3_realocada.local_id is None
assert senha3_realocada.nome_paciente == "João Pereira"
ok("'Realocar' move X003 para a Fila Y, devolve para 'aguardando' e grava o Nome")

# Mudança deliberada (não regressão): 'Realocar' passou a ser SILENCIOSO na TV de
# origem - antes emitia 'chamada' tipo 'nome' ali antes de mover a senha (o que
# fazia a TV da Recepção anunciar um paciente que estava de saída para a Coleta,
# sem sentido). Agora só sinaliza 'fila_atualizada' (para as telas de operador se
# atualizarem); o Nome continua sendo gravado (ver 'grava o Nome' acima), só não
# é mais anunciado na TV daqui - quem chama por nome é o destino, depois.
recebidos_origem = ws.get_received(NAMESPACE)
assert not any(ev["name"] == "chamada" for ev in recebidos_origem)
assert any(ev["name"] == "fila_atualizada" and ev["args"][0]["fila_id"] == ids["fila_x"] for ev in recebidos_origem)
ok("'Realocar' NÃO anuncia mais na TV de origem (silencioso, mudança deliberada) - só emite 'fila_atualizada'")

recebidos_destino = ws2.get_received(NAMESPACE)
assert any(ev["name"] == "fila_atualizada" and ev["args"][0]["fila_id"] == ids["fila_y"] for ev in recebidos_destino)
ok("'Realocar' emite 'fila_atualizada' tanto para a fila de origem quanto para a de destino")

with app.app_context():
    log_realocado = LogAtendimento.query.filter_by(senha_id=ids["x3"], evento="realocado").first()
    assert log_realocado is not None
ok("'Realocar' grava o evento de auditoria 'realocado'")

_escolher_local(c, ids["local3"])
resp = c.post(
    f"{URL_FILA_X}/realocar", data={"fila_destino_id": ids["fila_y"]}, follow_redirects=True
)
assert "Nenhuma senha chamada neste local".encode("utf-8") in resp.data
ok("'Realocar' em local sem senha chamada mostra erro amigável")

# ---------------------------------------------------------------------------
# 6) Botão 'Aguardar' - chama X002, depois devolve para a espera. (sessão já está
#    em local3, "Guiche 03", desde o passo anterior)
# ---------------------------------------------------------------------------
resp = c.post(f"{URL_FILA_X}/chamar", data={"codigo": "X002"}, follow_redirects=True)
assert resp.status_code == 200

resp = c.post(f"{URL_FILA_X}/aguardar", data={}, follow_redirects=True)
assert resp.status_code == 200
senha2_aguardando = db_get(Senha, ids["x2"])
assert senha2_aguardando.status == "aguardando"
assert senha2_aguardando.local_id is None
assert senha2_aguardando.chamada_em is None
ok("'Aguardar' devolve X002 para a espera, limpando local/chamada_em")

with app.app_context():
    log_voltou = LogAtendimento.query.filter_by(senha_id=ids["x2"], evento="voltou_aguardar").first()
    assert log_voltou is not None
ok("'Aguardar' grava o evento de auditoria 'voltou_aguardar'")

resp = c.get(f"{URL_FILA_X}/dados")
dados = resp.get_json()
assert dados["senha_atual"] is None
assert any(s["codigo"] == "X002" for s in dados["espera"])
ok("/dados confirma X002 de volta em 'espera' e sem 'senha_atual' no Guiche 03")

# ---------------------------------------------------------------------------
# 7) Botão 'Finalizar' - chama X002 de novo e finaliza.
#    local2 (não local1): X001 continua 'chamada' em local1 desde o passo 2/4
#    (nunca foi finalizada/aguardada nesta sequência de testes) - com o bloqueio
#    de chamada sem finalização, chamar em local1 aqui seria recusado. local2
#    está livre (X003 foi Realocada pra fora dela no passo 5).
# ---------------------------------------------------------------------------
_escolher_local(c, ids["local2"])
resp = c.post(f"{URL_FILA_X}/chamar", data={"codigo": "X002"}, follow_redirects=True)
assert resp.status_code == 200

resp = c.post(f"{URL_FILA_X}/finalizar", data={}, follow_redirects=True)
assert resp.status_code == 200
senha2_final = db_get(Senha, ids["x2"])
assert senha2_final.status == "finalizada" and senha2_final.finalizada_em is not None
ok("'Finalizar' conclui o atendimento de X002 (status='finalizada')")

with app.app_context():
    log_fim = LogAtendimento.query.filter_by(senha_id=ids["x2"], evento="fim_atendimento").first()
    assert log_fim is not None
ok("'Finalizar' grava o evento de auditoria 'fim_atendimento'")

_escolher_local(c, ids["local_vazio"])
resp = c.post(f"{URL_FILA_X}/finalizar", data={}, follow_redirects=True)
assert "Nenhuma senha chamada neste local".encode("utf-8") in resp.data
ok("'Finalizar' em local sem nenhuma senha chamada mostra erro amigável")

# ---------------------------------------------------------------------------
# 8) Observações: edição inline na tabela de espera.
# ---------------------------------------------------------------------------
resp = c.post(
    f"/atendimento/recepcao/senhas/{ids['x1']}/observacoes",
    data={"observacoes": "Paciente prefere ser chamado por apelido"},
    follow_redirects=True,
)
assert resp.status_code == 200
senha1_obs = db_get(Senha, ids["x1"])
assert senha1_obs.observacoes == "Paciente prefere ser chamado por apelido"
ok("Observações da senha são salvas via edição inline")

recebidos = ws.get_received(NAMESPACE)
assert any(ev["name"] == "fila_atualizada" for ev in recebidos)
ok("Salvar Observações emite 'fila_atualizada' para re-sincronizar a tabela")

# isolamento: admin de outra empresa não acessa/edita observações de senha da empresa A
resp = c_admin_b.post(
    f"/atendimento/recepcao/senhas/{ids['x1']}/observacoes", data={"observacoes": "hack"}
)
assert resp.status_code == 404
senha1_obs_intacta = db_get(Senha, ids["x1"])
assert senha1_obs_intacta.observacoes == "Paciente prefere ser chamado por apelido"
ok("Isolamento: admin de outra empresa recebe 404 ao tentar editar observações de senha da empresa A")

# ---------------------------------------------------------------------------
# 9) Isolamento multi-tenant nos 5 botões e no /dados, todos via fila de outra empresa.
# ---------------------------------------------------------------------------
URL_FILA_OUTRA = f"/atendimento/recepcao/filas/{ids['fila_outra_empresa']}"
for sufixo, dados_post in [
    ("chamar", {}), ("tocar", {}), ("aguardar", {}),
    ("finalizar", {}), ("realocar", {"fila_destino_id": "1"}),
]:
    resp = c.post(f"{URL_FILA_OUTRA}/{sufixo}", data=dados_post)
    assert resp.status_code == 404, (sufixo, resp.status_code)
assert c.get(f"{URL_FILA_OUTRA}/dados").status_code == 404
assert c.get(URL_FILA_OUTRA).status_code == 404
ok("Isolamento: todos os botões + GET + /dados retornam 404 para fila de outra empresa")

# ---------------------------------------------------------------------------
# 10) Socket 'entrar_fila': usuário sem acesso à fila recebe 'erro', não entra na sala.
# ---------------------------------------------------------------------------
ws3 = socketio.test_client(app, namespace=NAMESPACE)
ws3.get_received(NAMESPACE)
ws3.emit("entrar_fila", {"fila_id": ids["fila_x"]}, namespace=NAMESPACE)
recebido_sem_login = ws3.get_received(NAMESPACE)
assert any(ev["name"] == "erro" for ev in recebido_sem_login)
ok("Socket 'entrar_fila' sem usuário autenticado recebe 'erro' (cliente de teste não loga via HTTP)")

# ---------------------------------------------------------------------------
# 11) Isolamento por UNIDADE dentro da MESMA empresa (gap identificado na auditoria
#     de IDOR): recepcao_a só tem acesso à unidade A1, mas a Fila Z pertence à
#     unidade A2 - MESMA empresa A. empresa_id bate, unidade_id não deveria bater.
#     Cobre fila_acessivel_ou_404 e senha_acessivel_ou_404 (atendimento/helpers.py)
#     para o caso que test_regressao_fase10_3.py só cobria para Locais.
# ---------------------------------------------------------------------------
URL_FILA_Z = f"/atendimento/recepcao/filas/{ids['fila_z']}"

assert c.get(URL_FILA_Z).status_code == 404
ok("Isolamento por unidade: GET na Fila Z (unidade A2) por usuário restrito à unidade A1 -> 404")

assert c.get(f"{URL_FILA_Z}/dados").status_code == 404
ok("Isolamento por unidade: GET /dados na Fila Z (unidade A2) -> 404")

for sufixo, dados_post in [
    ("chamar", {}), ("tocar", {}), ("aguardar", {}),
    ("finalizar", {}), ("realocar", {"fila_destino_id": str(ids["fila_x"])}),
]:
    resp = c.post(f"{URL_FILA_Z}/{sufixo}", data=dados_post)
    assert resp.status_code == 404, (sufixo, resp.status_code)
ok("Isolamento por unidade: todos os botões (chamar/tocar/aguardar/finalizar/realocar) -> 404 na Fila Z")

with app.app_context():
    senha_z_intacta = db.session.get(Senha, ids["z1"])
    assert senha_z_intacta.status == "aguardando"
ok("Isolamento por unidade: nenhuma das tentativas acima mudou o estado da senha Z001")

resp = c.post(f"/atendimento/recepcao/senhas/{ids['z1']}/observacoes", data={"observacoes": "hack"})
assert resp.status_code == 404
with app.app_context():
    assert db.session.get(Senha, ids["z1"]).observacoes is None
ok("Isolamento por unidade: senha_acessivel_ou_404 bloqueia edição de Observações da senha Z001 (404)")

# ---------------------------------------------------------------------------
# 12) Cruzamento de SETOR (gap identificado na auditoria de IDOR): perfil 'atendente'
#     não acessa rotas do setor 'coleta' e vice-versa, mesmo com acesso à mesma
#     unidade/fila - cobre exigir_acesso_setor (atendimento/helpers.py).
# ---------------------------------------------------------------------------
assert c.get(f"/atendimento/coleta/filas/{ids['fila_x']}").status_code == 403
ok("RBAC de setor: usuário só 'atendente' recebe 403 em GET /atendimento/coleta/filas/<id>")

resp = c.post(f"/atendimento/coleta/filas/{ids['fila_x']}/chamar", data={})
assert resp.status_code == 403
ok("RBAC de setor: usuário só 'atendente' recebe 403 ao tentar POST 'chamar' em rota do setor 'coleta'")

assert c_coleta.get(f"/atendimento/recepcao/filas/{ids['fila_x']}").status_code == 403
ok("RBAC de setor: usuário só 'executor' recebe 403 em GET /atendimento/recepcao/filas/<id>")

resp = c_coleta.post(f"/atendimento/recepcao/filas/{ids['fila_x']}/chamar", data={})
assert resp.status_code == 403
ok("RBAC de setor: usuário só 'executor' recebe 403 ao tentar POST 'chamar' em rota do setor 'recepcao'")

with app.app_context():
    senha_x1_intacta = db.session.get(Senha, ids["x1"])
    assert senha_x1_intacta.status == "chamada"  # já chamada no passo 2, cruzamento não alterou nada
ok("RBAC de setor: nenhuma tentativa de cruzamento alterou o estado das senhas da Fila X")

print()
print(f"TODOS OS {len(passos)} TESTES DA SEÇÃO 10.2 (TELA UNIFICADA DE ATENDIMENTO) PASSARAM")
