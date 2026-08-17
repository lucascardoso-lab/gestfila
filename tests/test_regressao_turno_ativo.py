"""
Teste de regressão: tela "Iniciar Turno" (seção 10.19 do BLUEPRINT) - configuração
de turno (filas + peso de rodízio + local) preenchida pelo próprio operador após o
login e antes de acessar a tela de atendimento, persistida em `TurnoAtivo` para
sobreviver a reload/crash (adendo 10.11).

Execução: python -m tests.test_regressao_turno_ativo (a partir da raiz do projeto,
com o virtualenv ativado).
"""
import json
import os

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

from app import create_app
from app.extensions import db
from app.models import Empresa, Fila, Local, Perfil, Senha, TurnoAtivo, Unidade, Usuario

app = create_app("testing")

passos = []


def ok(msg):
    passos.append(msg)
    print("OK:", msg)


with app.app_context():
    db.create_all()

    perfil_recepcao = Perfil(nome="atendente")
    perfil_gerencial = Perfil(nome="gerencial")
    db.session.add_all([perfil_recepcao, perfil_gerencial])
    db.session.commit()

    empresa = Empresa(nome="Clinica A")
    db.session.add(empresa)
    db.session.commit()

    unidade = Unidade(empresa_id=empresa.id, nome="Unidade A1")
    db.session.add(unidade)
    db.session.commit()

    fila_a = Fila(empresa_id=empresa.id, unidade_id=unidade.id, nome="Recepção Geral", sigla="RG")
    fila_b = Fila(empresa_id=empresa.id, unidade_id=unidade.id, nome="Preferencial", sigla="PR")
    db.session.add_all([fila_a, fila_b])
    db.session.commit()

    local_1 = Local(
        empresa_id=empresa.id, unidade_id=unidade.id, nome="Guiche 01", tipo="recepcao",
        nome_exibicao="Guiche 01", identificador_admin="[UNI] Guiche 01",
    )
    local_2 = Local(
        empresa_id=empresa.id, unidade_id=unidade.id, nome="Guiche 02", tipo="recepcao",
        nome_exibicao="Guiche 02", identificador_admin="[UNI] Guiche 02",
    )
    db.session.add_all([local_1, local_2])
    db.session.commit()

    def novo_usuario(login, perfis):
        u = Usuario(empresa_id=empresa.id, nome=login, login=login)
        u.set_senha("senha123")
        u.perfis = perfis
        u.unidades = [unidade]
        db.session.add(u)
        db.session.commit()
        return u.id

    id_recepcao_1 = novo_usuario("recepcao_turno_1", [perfil_recepcao])
    id_recepcao_2 = novo_usuario("recepcao_turno_2", [perfil_recepcao])
    id_gerencial = novo_usuario("gerencial_turno", [perfil_gerencial])

    ids = dict(
        empresa=empresa.id, unidade=unidade.id,
        fila_a=fila_a.id, fila_b=fila_b.id,
        local_1=local_1.id, local_2=local_2.id,
        recepcao_1=id_recepcao_1, recepcao_2=id_recepcao_2, gerencial=id_gerencial,
    )


def login_como(login):
    c = app.test_client()
    resp = c.post("/login", data={"login": login, "senha": "senha123"}, follow_redirects=False)
    assert resp.status_code == 302, (login, resp.status_code)
    return c, resp


def csrf_de(c, url):
    resp = c.get(url)
    texto = resp.data.decode("utf-8")
    inicio = texto.index('name="csrf_token" value="') + len('name="csrf_token" value="')
    return texto[inicio:texto.index('"', inicio)]


def db_get(modelo, id_):
    with app.app_context():
        return db.session.get(modelo, id_)


URL_INICIAR_TURNO = "/atendimento/iniciar-turno"
URL_ENCERRAR_TURNO = "/atendimento/turno/encerrar"

# ===========================================================================
# A) Login de um perfil puramente operacional (recepção) redireciona direto pra
#    'Iniciar Turno' - fluxo Login -> Iniciar Turno -> Atendimento pedido.
# ===========================================================================
c1, resp_login = login_como("recepcao_turno_1")
assert resp_login.headers["Location"].endswith(URL_INICIAR_TURNO)
ok("Login de perfil recepção redireciona direto para a tela 'Iniciar Turno'")

resp = c1.get(URL_INICIAR_TURNO)
assert resp.status_code == 200
texto_tela = resp.data.decode("utf-8")
assert "Iniciar turno" in texto_tela
# Busca com autocomplete (mesmo padrão da seção 10.10) substituiu os <select>
# simples de Fila/Local - a lista completa vem embutida como JSON no JS da
# página (FILAS_DISPONIVEIS/LOCAIS_DISPONIVEIS), não mais como <option>.
assert 'id="busca-fila"' in texto_tela and 'id="busca-local"' in texto_tela
inicio = texto_tela.index("FILAS_DISPONIVEIS = ") + len("FILAS_DISPONIVEIS = ")
fim = texto_tela.index(";", inicio)
filas_disponiveis = json.loads(texto_tela[inicio:fim])
por_id = {item["id"]: item for item in filas_disponiveis}
assert por_id[ids["fila_a"]]["nome"] == "Recepção Geral"
ok("Tela 'Iniciar Turno' lista as filas e locais acessíveis ao operador")

# ===========================================================================
# B) Tentar iniciar turno sem selecionar nenhuma fila é bloqueado.
# ===========================================================================
csrf = csrf_de(c1, URL_INICIAR_TURNO)
resp = c1.post(URL_INICIAR_TURNO, data={"csrf_token": csrf, "local_id": str(ids["local_1"])}, follow_redirects=True)
assert resp.status_code == 200
assert "Adicione ao menos uma fila ao turno." in resp.data.decode("utf-8")
with app.app_context():
    assert TurnoAtivo.query.filter_by(usuario_id=ids["recepcao_1"]).first() is None
ok("Iniciar turno sem nenhuma fila selecionada é recusado, nenhum TurnoAtivo criado")

# ===========================================================================
# C) Tentar iniciar turno com fila(s) mas SEM local é bloqueado (item explícito
#    do pedido: "tentativa de iniciar sem local deve bloquear").
# ===========================================================================
csrf = csrf_de(c1, URL_INICIAR_TURNO)
resp = c1.post(
    URL_INICIAR_TURNO,
    data={"csrf_token": csrf, "fila_id": [str(ids["fila_a"])], "chamadas_seguidas": ["2"]},
    follow_redirects=True,
)
assert resp.status_code == 200
assert "Selecione um local válido para o turno." in resp.data.decode("utf-8")
with app.app_context():
    assert TurnoAtivo.query.filter_by(usuario_id=ids["recepcao_1"]).first() is None
ok("Iniciar turno com fila(s) mas sem local válido é recusado, nenhum TurnoAtivo criado")

# ===========================================================================
# D) Configuração de turno com MÚLTIPLAS filas (a mesma fila adicionada duas
#    vezes, para ajustar o peso) + um local -> cria TurnoAtivo e redireciona pra
#    tela de atendimento por-Local.
# ===========================================================================
csrf = csrf_de(c1, URL_INICIAR_TURNO)
resp = c1.post(
    URL_INICIAR_TURNO,
    data={
        "csrf_token": csrf,
        "fila_id": [str(ids["fila_a"]), str(ids["fila_b"]), str(ids["fila_a"])],
        "chamadas_seguidas": ["2", "1", "3"],
        "local_id": str(ids["local_1"]),
    },
    follow_redirects=False,
)
assert resp.status_code == 302
assert f"/local/{ids['local_1']}" in resp.headers["Location"]
ok("Turno com múltiplas filas (incl. a mesma fila duas vezes) + local válido cria o turno e redireciona pro atendimento")

with app.app_context():
    turno = TurnoAtivo.query.filter_by(usuario_id=ids["recepcao_1"]).first()
    assert turno is not None
    assert turno.ativo is True
    assert turno.local_id == ids["local_1"]
    # fila_a foi adicionada duas vezes (peso 2 + peso 3) - mesclada em UMA entrada
    # de peso 5, sem duplicar fila_id (ambiguidade no motor de rodízio).
    assert len(turno.filas_config) == 2
    por_fila = {item["fila_id"]: item["chamadas_seguidas"] for item in turno.filas_config}
    assert por_fila[ids["fila_a"]] == 5
    assert por_fila[ids["fila_b"]] == 1
ok("Filas repetidas na configuração do turno são mescladas (soma de chamadas_seguidas), sem fila_id duplicado")

with app.app_context():
    usuario = db.session.get(Usuario, ids["recepcao_1"])
    assert usuario.local_atual_id == ids["local_1"]
ok("Iniciar turno também ocupa o Local (current_user.local_atual_id), igual à tela 'Escolher local' já existente")

# ===========================================================================
# E) Motor de rodízio realmente usa a configuração do turno (não só decorativo):
#    Local sem NENHUM rodízio admin-configurado, mas com turno ativo -> 'Chamar'
#    passa a considerar as filas do turno.
# ===========================================================================
with app.app_context():
    s1 = Senha(
        empresa_id=ids["empresa"], unidade_id=ids["unidade"], fila_id=ids["fila_a"],
        codigo="RG001", sequencial=1, prioridade=False, status="aguardando",
    )
    s2 = Senha(
        empresa_id=ids["empresa"], unidade_id=ids["unidade"], fila_id=ids["fila_b"],
        codigo="PR001", sequencial=1, prioridade=False, status="aguardando",
    )
    db.session.add_all([s1, s2])
    db.session.commit()

csrf = csrf_de(c1, f"/atendimento/recepcao/local/{ids['local_1']}")
resp = c1.post(
    f"/atendimento/recepcao/local/{ids['local_1']}/chamar",
    data={"csrf_token": csrf},
    follow_redirects=True,
)
assert resp.status_code == 200
with app.app_context():
    chamadas = Senha.query.filter(Senha.status.in_(("chamada", "em_atendimento"))).all()
    assert len(chamadas) == 1
    assert chamadas[0].fila_id in (ids["fila_a"], ids["fila_b"])
    assert chamadas[0].local_id == ids["local_1"]
ok("'Chamar' no Local usa a configuração de filas do turno ativo (Local sem rodízio admin-configurado)")

# ===========================================================================
# F) GET em 'Iniciar Turno' SEMPRE mostra o formulário (padronizado como tela
#    de entrada em todo login - seção 10.23, reverte o pulo automático da seção
#    10.19) - com um turno já ativo, vem pré-populado com a configuração atual.
# ===========================================================================
resp = c1.get(URL_INICIAR_TURNO)
assert resp.status_code == 200
texto_form = resp.data.decode("utf-8")
assert "Iniciar turno" in texto_form
assert str(ids["fila_a"]) in texto_form  # configuração atual pré-populada, não pulada
ok("GET em 'Iniciar Turno' sempre mostra o formulário, pré-populado quando já há turno ativo (nunca pula mais)")

# ===========================================================================
# G) Recuperação de turno ativo após reload: uma sessão NOVA do MESMO usuário
#    (simulando fechar/reabrir o navegador) - sem nada na sessão do navegador
#    (session_transaction limpa), o turno persistido no banco ainda é encontrado
#    e o formulário chega pré-populado com a configuração salva.
# ===========================================================================
c1_novo = app.test_client()
resp = c1_novo.post("/login", data={"login": "recepcao_turno_1", "senha": "senha123"}, follow_redirects=False)
assert resp.status_code == 302
assert resp.headers["Location"].endswith(URL_INICIAR_TURNO)
ok("Reload/nova sessão do mesmo operador: login redireciona pra 'Iniciar Turno' (perfil operacional puro)")

# Single-session enforcement (login() incrementa Usuario.sessao_versao a cada
# autenticação bem-sucedida): a sessão ANTIGA (c1) deixa de ser válida assim
# que a nova (c1_novo) loga - próxima requisição de c1 é tratada como anônima,
# mesmo sem nenhum logout explícito ter acontecido. 'Iniciar Turno' usa
# @roles_required (app/utils/decorators.py), não @login_required do Flask-
# Login - current_user não autenticado vira 401 direto, não um redirect 302
# pro login (isso é comportamento normal desta rota, não algo novo desta
# mudança). Daqui em diante o teste usa c1_novo pra continuar a sessão do
# operador - reusar c1 abaixo seria testar um cenário que não é mais
# suportado de propósito (duas sessões simultâneas do mesmo usuário).
resp_sessao_antiga = c1.get(URL_INICIAR_TURNO, follow_redirects=False)
assert resp_sessao_antiga.status_code == 401
ok("Sessão antiga (c1) foi invalidada pelo novo login (c1_novo) - próxima requisição não está mais autenticada (401)")

resp = c1_novo.get(URL_INICIAR_TURNO)
assert resp.status_code == 200
texto_recuperado = resp.data.decode("utf-8")
assert str(ids["fila_a"]) in texto_recuperado
# Com o <select> substituído pelo widget de busca (10.10), a pré-seleção do
# Local recuperado do banco vira o parâmetro `valorInicial` passado à fábrica
# criarBuscaAutocomplete() - a instância de Local (não a de Fila, que nunca
# pré-seleciona nada) - único jeito de confirmar QUAL local foi pré-selecionado,
# já que IDs de Fila e Local são sequências independentes e podem colidir.
assert f"valorInicial: {ids['local_1']}," in texto_recuperado
ok("'Iniciar Turno' encontra o turno salvo no banco e pré-popula filas/Local - recuperação de estado após reload")

resp = c1_novo.get(f"/atendimento/recepcao/local/{ids['local_1']}")
assert resp.status_code == 200
ok("Tela de atendimento por-Local abre normalmente, mesmo sem confirmar o formulário de novo")

# ===========================================================================
# H) Encerrar turno: marca ativo=False, libera o Local, mas NÃO desloga - o
#    operador pode iniciar outro turno sem precisar logar de novo. Continua
#    com c1_novo (a sessão válida) - c1 já foi invalidada no bloco G acima.
# ===========================================================================
csrf = csrf_de(c1_novo, f"/atendimento/recepcao/local/{ids['local_1']}")
resp = c1_novo.post(URL_ENCERRAR_TURNO, data={"csrf_token": csrf}, follow_redirects=True)
assert resp.status_code == 200
assert "Iniciar turno" in resp.data.decode("utf-8")
ok("'Encerrar turno' volta para a tela 'Iniciar Turno'")

with app.app_context():
    turno = TurnoAtivo.query.filter_by(usuario_id=ids["recepcao_1"]).first()
    assert turno is not None
    assert turno.ativo is False
    usuario = db.session.get(Usuario, ids["recepcao_1"])
    assert usuario.local_atual_id is None
ok("'Encerrar turno' marca TurnoAtivo.ativo=False e libera o Local (local_atual_id=None)")

# a sessão continua autenticada - não foi um logout.
resp = c1_novo.get("/atendimento/filas")
assert resp.status_code == 200
ok("Após encerrar o turno, a sessão continua autenticada (não é um logout) - operador acessa outras telas normalmente")

# GET em 'Iniciar Turno' de novo não pula mais (turno inativo) - pode começar
# outro turno, em outro Local, sem logar de novo.
resp = c1_novo.get(URL_INICIAR_TURNO)
assert resp.status_code == 200
assert "Iniciar turno" in resp.data.decode("utf-8")
ok("Após encerrar, GET em 'Iniciar Turno' mostra o formulário normalmente (não pula mais, turno está inativo)")

# ===========================================================================
# I) Um Local já ocupado por outro operador não pode ser escolhido ao iniciar
#    um turno (mesma regra de 'Escolher local' já existente).
# ===========================================================================
c2, _ = login_como("recepcao_turno_2")
csrf = csrf_de(c2, URL_INICIAR_TURNO)
resp = c2.post(
    URL_INICIAR_TURNO,
    data={
        "csrf_token": csrf,
        "fila_id": [str(ids["fila_a"])],
        "chamadas_seguidas": ["1"],
        "local_id": str(ids["local_2"]),
    },
    follow_redirects=False,
)
assert resp.status_code == 302  # local_2 está livre
ok("Local livre: outro operador consegue iniciar turno nele normalmente")

# recepcao_2 reinicia o turno de recepcao_1 no MESMO local_1 (que recepcao_1
# encerrou no passo H) - deveria funcionar (ninguém mais o ocupa).
csrf = csrf_de(c2, URL_INICIAR_TURNO)
resp = c2.post(
    URL_INICIAR_TURNO,
    data={
        "csrf_token": csrf,
        "fila_id": [str(ids["fila_b"])],
        "chamadas_seguidas": ["1"],
        "local_id": str(ids["local_1"]),
    },
    follow_redirects=False,
)
assert resp.status_code == 302  # troca de local_2 pra local_1, ambos livres pra ele
ok("Operador troca de Local ao reconfigurar o turno (local anterior liberado automaticamente)")

# agora local_2 está livre de novo (recepcao_2 saiu de lá) - um TERCEIRO cenário:
# recepcao_2 está em local_1; simula outro operador tentando entrar em local_1.
c1_de_novo, _ = login_como("recepcao_turno_1")
csrf = csrf_de(c1_de_novo, URL_INICIAR_TURNO)
resp = c1_de_novo.post(
    URL_INICIAR_TURNO,
    data={
        "csrf_token": csrf,
        "fila_id": [str(ids["fila_a"])],
        "chamadas_seguidas": ["1"],
        "local_id": str(ids["local_1"]),
    },
    follow_redirects=True,
)
assert resp.status_code == 200
assert "já está em uso por" in resp.data.decode("utf-8")
with app.app_context():
    turno = TurnoAtivo.query.filter_by(usuario_id=ids["recepcao_1"]).first()
    assert turno.ativo is False  # continua encerrado - não foi sobrescrito
ok("Local já ocupado por outro operador bloqueia o início do turno, com mensagem clara")

# ===========================================================================
# J) RBAC: perfil gerencial não acessa a tela 'Iniciar Turno' (só
#    recepcao/coleta/recepcao_coleta/admin, mesma lista de PERFIS_OPERACIONAIS).
# ===========================================================================
c_gerencial, resp_login_gerencial = login_como("gerencial_turno")
assert not resp_login_gerencial.headers["Location"].endswith(URL_INICIAR_TURNO)
ok("Login de perfil gerencial NÃO redireciona para 'Iniciar Turno' (continua no dashboard)")

resp = c_gerencial.get(URL_INICIAR_TURNO)
assert resp.status_code == 403
ok("Perfil gerencial recebe 403 ao tentar acessar 'Iniciar Turno' diretamente (RBAC)")

# ===========================================================================
# K) Bug corrigido: deslogar ('Sair') não encerrava o turno - TurnoAtivo.ativo
#    continuava True, então o próximo login pré-populava 'Iniciar Turno' com as
#    filas/Local do turno ANTERIOR. Um logout explícito agora encerra o turno
#    (mesmo efeito do botão 'Encerrar turno') - o próximo login sempre chega com
#    o formulário zerado.
# ===========================================================================
with app.app_context():
    turno_antes_logout = TurnoAtivo.query.filter_by(usuario_id=ids["recepcao_2"]).first()
    assert turno_antes_logout is not None and turno_antes_logout.ativo is True
    assert turno_antes_logout.filas_config  # tem fila(s) configurada(s) (seção I/H)

resp = c2.get("/logout", follow_redirects=True)
assert resp.status_code == 200

with app.app_context():
    turno_apos_logout = TurnoAtivo.query.filter_by(usuario_id=ids["recepcao_2"]).first()
    assert turno_apos_logout.ativo is False
ok("Logout ('Sair') marca o TurnoAtivo como inativo, igual ao botão 'Encerrar turno'")

c2_relogado, resp_login_2 = login_como("recepcao_turno_2")
assert resp_login_2.headers["Location"].endswith(URL_INICIAR_TURNO)
resp = c2_relogado.get(URL_INICIAR_TURNO)
assert resp.status_code == 200
texto_pos_logout = resp.data.decode("utf-8")
assert "let filasSelecionadas = ([]).filter" in texto_pos_logout
ok("Após deslogar e logar de novo, 'Iniciar Turno' chega com a lista de filas ZERADA (não repete o turno anterior)")

print()
print(f"TODOS OS {len(passos)} TESTES DE TURNO ATIVO PASSARAM")
