"""
Teste de regressão: controle de atendimento (bloqueio de chamada sem finalização
+ recuperação de estado após reload/crash), pedido explícito do cliente com base
nas seções 10.2 e 10.8 do BLUEPRINT.

Cobre, na ordem:
1. Bloqueio: com uma senha 'chamada'/'em_atendimento' em nome do operador atual
   naquele local, o botão "Chamar" (principal e o individual da linha) é
   recusado com "Finalize o atendimento atual antes de chamar o próximo." - nem
   a seleção automática nem a busca manual por código escapam do bloqueio.
   Tocar/Realocar/Finalizar/Aguardar continuam funcionando normalmente. Depois
   de "Aguardar" (senha volta pra 'aguardando'), "Chamar" é liberado de novo -
   sem nenhuma lógica de exceção extra, só o efeito natural do status mudar.
2. Recuperação de estado: um GET na tela (simulando reload/reconexão após
   crash) reabre já com a senha em andamento (Local/Senha chamada/Nome
   preenchidos) - a query usada é literalmente a do pedido (operador_id +
   local_id + status IN ('chamada','em_atendimento')). Prova, via um cliente
   WebSocket já conectado na sala da fila, que o GET em si NUNCA emite o evento
   'chamada' (que é o que dispara o áudio na TV) - só as rotas de ação
   (chamar/tocar/etc.) fazem isso.
3. Reload sem nenhuma senha em andamento: abre normalmente, senha_atual=None,
   Chamar liberado.
4. Indicador visual: badge "Em atendimento" e classe de destaque aparecem no
   HTML quando há senha em andamento.

Isolamento multi-tenant: a query de recuperação/bloqueio (senha_atual_no_local)
já filtra por operador_id E local_id - dois operadores diferentes no mesmo
Local (em momentos diferentes) nunca enxergam a senha um do outro.

Execução: python -m tests.test_regressao_bloqueio_chamada_recuperacao_estado
(a partir da raiz do projeto, com o virtualenv ativado).
"""
import os

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


NAMESPACE = "/painel"

with app.app_context():
    db.create_all()

    perfil_admin = Perfil(nome="admin")
    db.session.add(perfil_admin)
    db.session.commit()

    empresa = Empresa(nome="Clinica A")
    db.session.add(empresa)
    db.session.commit()

    unidade = Unidade(empresa_id=empresa.id, nome="Unidade A1")
    db.session.add(unidade)
    db.session.commit()

    fila = Fila(empresa_id=empresa.id, unidade_id=unidade.id, nome="Fila A", sigla="FA")
    db.session.add(fila)
    db.session.commit()

    local = Local(
        empresa_id=empresa.id, unidade_id=unidade.id, nome="Guiche 01",
        nome_exibicao="Guiche 01", identificador_admin="[UNI] Guiche 01",
    )
    db.session.add(local)
    db.session.commit()

    db.session.add(LocalRodizioFila(local_id=local.id, fila_id=fila.id, ordem=0, chamadas_seguidas=1))
    db.session.commit()

    def novo_usuario(login, perfis, unidades):
        u = Usuario(empresa_id=empresa.id, nome=login, login=login)
        u.set_senha("senha123")
        u.perfis = perfis
        u.unidades = unidades
        db.session.add(u)
        db.session.commit()
        return u.id

    # perfil admin: única forma hoje de ver o botão "Chamar" principal (perfis
    # operacionais recepcao/coleta/recepcao_coleta têm o botão principal
    # ocultado por decisão anterior - ver test_regressao_botao_chamar_oculto.py);
    # a proteção server-side, porém, vale pra TODOS, testada via POST direto.
    id_op1 = novo_usuario("operador1_bloqueio", [perfil_admin], [unidade])
    id_op2 = novo_usuario("operador2_bloqueio", [perfil_admin], [unidade])

    def nova_senha(codigo, seq):
        s = Senha(
            empresa_id=empresa.id, unidade_id=unidade.id, fila_id=fila.id,
            codigo=codigo, sequencial=seq, prioridade=False, status="aguardando",
        )
        db.session.add(s)
        db.session.commit()
        return s.id

    id_s1 = nova_senha("FA001", 1)
    id_s2 = nova_senha("FA002", 2)
    id_s3 = nova_senha("FA003", 3)

    ids = dict(
        fila=fila.id, local=local.id, op1=id_op1, op2=id_op2, s1=id_s1, s2=id_s2, s3=id_s3,
    )


def login_como(login):
    c = app.test_client()
    resp = c.post("/login", data={"login": login, "senha": "senha123"}, follow_redirects=True)
    assert resp.status_code == 200, (login, resp.status_code)
    with c.session_transaction() as sess:
        sess["atendimento_local_id"] = ids["local"]
    return c


def db_get(modelo, id_):
    with app.app_context():
        return db.session.get(modelo, id_)


def flash_bloqueio_presente(texto):
    """Diferente de bloqueio_ativo_no_html: aqui checa especificamente a <div
    class="flash error"> do bloqueio (só aparece quando a requisição É de fato
    recusada por ele), não o aviso estático sempre presente no card."""
    return '<div class="flash error">Finalize o atendimento atual antes de chamar o próximo.</div>' in texto


def bloqueio_ativo_no_html(texto):
    """O aviso <p id="aviso-bloqueio"> agora é sempre renderizado no HTML (pra
    poder ser alternado via JS sem esperar um reload) - alterna só o atributo
    `hidden`, não sua presença no DOM. Por isso não dá pra checar "bloqueado"
    olhando só se a frase aparece no texto (ela SEMPRE aparece, escondida ou
    não) - precisa checar especificamente o atributo `hidden` desse elemento."""
    inicio = texto.find('id="aviso-bloqueio"')
    if inicio == -1:
        return False
    return "hidden" not in texto[inicio:inicio + 150]


URL_FILA = f"/atendimento/recepcao/filas/{ids['fila']}"
URL_LOCAL = f"/atendimento/recepcao/local/{ids['local']}"

c1 = login_como("operador1_bloqueio")

# ===========================================================================
# A) BLOQUEIO: 'Chamar' recusado enquanto há senha em andamento (principal,
#    individual por código, e a variante por-Local) - status/senha não mudam.
# ===========================================================================
resp = c1.post(f"{URL_FILA}/chamar", data={}, follow_redirects=True)
assert resp.status_code == 200
assert db_get(Senha, ids["s1"]).status == "chamada"
ok("Primeira 'Chamar' funciona normalmente (nenhuma senha em andamento ainda)")

resp = c1.post(f"{URL_FILA}/chamar", data={}, follow_redirects=True)
assert resp.status_code == 200
assert "Finalize o atendimento atual antes de chamar o próximo.".encode("utf-8") in resp.data
assert db_get(Senha, ids["s2"]).status == "aguardando"  # nada foi chamado
ok("'Chamar' (seleção automática) é bloqueado com a senha atual ainda em andamento")

resp = c1.post(f"{URL_FILA}/chamar", data={"codigo": "FA002"}, follow_redirects=True)
assert resp.status_code == 200
assert "Finalize o atendimento atual antes de chamar o próximo.".encode("utf-8") in resp.data
assert db_get(Senha, ids["s2"]).status == "aguardando"
ok("'Chamar' individual por código (busca manual) também é bloqueado, não só a seleção automática")

resp = c1.post(f"{URL_LOCAL}/chamar", data={}, follow_redirects=True)
assert resp.status_code == 200
assert "Finalize o atendimento atual antes de chamar o próximo.".encode("utf-8") in resp.data
ok("'Chamar' na variante por-Local (chamar_local) também respeita o mesmo bloqueio")

# Tocar/Realocar/Finalizar/Aguardar continuam ativos normalmente (agem sobre a
# senha atual, não sobre uma nova) - Tocar primeiro, sem alterar nada.
resp = c1.post(f"{URL_FILA}/tocar", data={}, follow_redirects=True)
assert resp.status_code == 200
assert db_get(Senha, ids["s1"]).status == "chamada"
ok("'Tocar' continua funcionando normalmente enquanto 'Chamar' está bloqueado")

# ===========================================================================
# B) Exceção natural: 'Aguardar' devolve a senha à espera -> 'Chamar' libera
#    de novo, sem nenhuma lógica de exceção extra (só o status mudou).
# ===========================================================================
resp = c1.post(f"{URL_FILA}/aguardar", data={}, follow_redirects=True)
assert resp.status_code == 200
assert db_get(Senha, ids["s1"]).status == "aguardando"
ok("'Aguardar' devolve a senha à espera")

resp = c1.post(f"{URL_FILA}/chamar", data={}, follow_redirects=True)
assert resp.status_code == 200
# a prova de que NÃO foi bloqueada é o status ter mudado de verdade (se tivesse
# sido recusada, continuaria 'aguardando') - a página de destino, claro, volta a
# mostrar o aviso "em andamento" ativo, porque agora HÁ uma chamada em curso de
# novo (a que acabamos de fazer) - isso é o comportamento correto, não um bug.
assert db_get(Senha, ids["s1"]).status == "chamada"
assert "Senha FA001 chamada para".encode("utf-8") in resp.data  # flash de sucesso, não de bloqueio
ok("Depois de 'Aguardar', 'Chamar' é liberado de novo automaticamente (FA001 rechamada)")

# ===========================================================================
# C) status 'em_atendimento' (schema já previa, nunca usado até então) também
#    bloqueia, exatamente como 'chamada' - checagem explícita do pedido.
# ===========================================================================
with app.app_context():
    s1 = db.session.get(Senha, ids["s1"])
    s1.status = "em_atendimento"
    db.session.commit()

resp = c1.post(f"{URL_FILA}/chamar", data={}, follow_redirects=True)
assert resp.status_code == 200
assert "Finalize o atendimento atual antes de chamar o próximo.".encode("utf-8") in resp.data
ok("Status 'em_atendimento' bloqueia 'Chamar' exatamente como 'chamada'")

# libera pra próxima seção: finaliza de vez (Finalizar continua ativo com o
# status em 'em_atendimento', mesma senha_atual_no_local usada em todo lugar).
resp = c1.post(f"{URL_FILA}/finalizar", data={}, follow_redirects=True)
assert resp.status_code == 200
assert db_get(Senha, ids["s1"]).status == "finalizada"
ok("'Finalizar' também funciona normalmente com a senha em 'em_atendimento'")

# ===========================================================================
# D) RECUPERAÇÃO DE ESTADO: reload (GET) com senha em andamento reabre já
#    preenchido, e NUNCA re-dispara o evento 'chamada' (áudio da TV) - só a
#    ação de chamar em si faz isso, nunca um GET/reload.
# ===========================================================================
ws = socketio.test_client(app, namespace=NAMESPACE, flask_test_client=c1)
ws.get_received(NAMESPACE)
ws.emit("entrar_fila", {"fila_id": ids["fila"]}, namespace=NAMESPACE)
ws.get_received(NAMESPACE)

resp = c1.post(f"{URL_FILA}/chamar", data={"codigo": "FA002"}, follow_redirects=True)
assert resp.status_code == 200
recebidos_chamada_real = ws.get_received(NAMESPACE)
assert any(ev["name"] == "chamada" for ev in recebidos_chamada_real)
ok("Chamada real (ação do operador) emite o evento 'chamada' normalmente, como sempre")

# simula reload/reconexão: um GET simples na tela, MUITAS vezes, como um
# navegador recarregando repetidas vezes após um crash.
for _ in range(3):
    resp = c1.get(URL_FILA)
    assert resp.status_code == 200
    texto = resp.data.decode("utf-8")
    assert "FA002" in texto  # 'Senha chamada' já vem preenchida, sem precisar chamar de novo
    assert 'value="FA002"' not in texto  # não é o campo Código - é o display de "Senha chamada"

    recebidos_do_reload = ws.get_received(NAMESPACE)
    assert recebidos_do_reload == [], recebidos_do_reload
ok("3 reloads seguidos (GET) restauram o estado (senha em andamento) e NUNCA emitem 'chamada' de novo")

resp = c1.get(f"{URL_FILA}/dados")
dados = resp.get_json()
assert dados["senha_atual"]["codigo"] == "FA002"
recebidos_dados = ws.get_received(NAMESPACE)
assert recebidos_dados == []
ok("GET /dados (refresh via WebSocket 'fila_atualizada') também nunca dispara 'chamada'")

# mesma prova na tela por-Local.
resp = c1.get(URL_LOCAL)
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
assert "FA002" in texto
assert 'class="badge em-atendimento"' in texto
recebidos_local = ws.get_received(NAMESPACE)
assert recebidos_local == []
ok("Reload da tela por-Local também restaura o estado sem disparar 'chamada'")

# ===========================================================================
# E) Indicador visual: badge "Em atendimento" + classe de destaque aparecem
#    quando há senha em andamento (item 3 do pedido).
# ===========================================================================
resp = c1.get(URL_FILA)
texto = resp.data.decode("utf-8")
assert 'class="badge em-atendimento"' in texto and "Em atendimento" in texto
assert 'class="senha-chamada em-atendimento"' in texto
ok("Indicador visual (badge 'Em atendimento' + borda de destaque) presente quando há senha em andamento")

# libera FA002 pra próxima seção.
resp = c1.post(f"{URL_FILA}/finalizar", data={}, follow_redirects=True)
assert resp.status_code == 200
ws.get_received(NAMESPACE)

# ===========================================================================
# F) Reload SEM nenhuma senha em andamento: abre normalmente, sem indicador,
#    sem bloqueio.
# ===========================================================================
resp = c1.get(URL_FILA)
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
# "em-atendimento"/"Finalize o atendimento atual" aparecem sempre no HTML (CSS
# e o aviso oculto por `hidden`, pra poderem ser alternados via JS sem reload) -
# checa especificamente que a classe NÃO está aplicada no display, e que o
# aviso está com `hidden`, não a mera ausência da palavra no texto inteiro.
assert 'id="senha-chamada-display" class="senha-chamada"' in texto  # sem "em-atendimento" aplicada
assert not bloqueio_ativo_no_html(texto)
ok("Reload sem senha em andamento: abre normalmente, sem indicador nem bloqueio")

resp = c1.get(f"{URL_FILA}/dados")
dados = resp.get_json()
assert dados["senha_atual"] is None
ok("GET /dados sem senha em andamento retorna senha_atual=None")

# ===========================================================================
# G) Isolamento por operador: a senha em andamento de operador1 nunca aparece
#    como "a senha atual" de operador2 no MESMO local (mesmo já sem ocupação
#    simultânea de local - reforço direto na query, não só na tela de locais).
# ===========================================================================
resp = c1.post(f"{URL_FILA}/chamar", data={}, follow_redirects=True)  # operador1 chama FA003
assert resp.status_code == 200
assert db_get(Senha, ids["s3"]).status == "chamada"
assert db_get(Senha, ids["s3"]).operador_id == ids["op1"]

# operador2 assume o mesmo local depois (ex.: admin desconectou operador1 -
# ver tests/test_regressao_ocupacao_local.py) - sem finalizar a senha de
# operador1, que fica "órfã" (chamada, mas de outro operador).
c1.get("/logout", follow_redirects=True)
c2 = login_como("operador2_bloqueio")
resp = c2.get(URL_FILA)
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
# FA003 pode legitimamente aparecer em "Senhas atendidas" (histórico do POSTO,
# não do operador - propositalmente não filtrado por operador_id, útil numa
# troca de turno) - o que este teste garante é que ela NÃO aparece como "a
# senha atual" (o campo/display que decide bloqueio e reabertura de estado).
inicio_display = texto.index('id="senha-chamada-display"')
fim_display = texto.index("</div>", inicio_display)
assert "FA003" not in texto[inicio_display:fim_display]
assert not bloqueio_ativo_no_html(texto)  # operador2 não vê bloqueio por uma senha que não é dele
ok("Senha em andamento de um operador nunca aparece como 'senha atual' de outro operador no mesmo local")

resp = c2.post(f"{URL_FILA}/chamar", data={}, follow_redirects=True)
assert resp.status_code == 200
# não sobrou nenhuma senha 'aguardando' nesta fila (s1/s2 finalizadas, s3 está
# com operador1) - a resposta é "não há senhas aguardando", não o bloqueio de
# atendimento; o que este teste prova é que NÃO é o bloqueio quem barrou aqui.
assert not flash_bloqueio_presente(resp.data.decode("utf-8"))
assert "Não há senhas aguardando".encode("utf-8") in resp.data
ok("operador2 não é bloqueado por uma senha em andamento que pertence a operador1 (outro motivo barrou: fila vazia)")

print()
print(f"TODOS OS {len(passos)} TESTES DE BLOQUEIO DE CHAMADA E RECUPERACAO DE ESTADO PASSARAM")
