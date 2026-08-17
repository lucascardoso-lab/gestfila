"""
Teste de regressão: single-session enforcement no login (app/auth/routes.py::
login) - logar de novo com o MESMO usuário deve invalidar automaticamente
qualquer sessão anterior dele, em qualquer dispositivo/aba, reaproveitando o
mesmo mecanismo de logout remoto que a seção 10.15 já construiu pro botão
"Desconectar" do admin (Usuario.sessao_versao comparado a cada request em
app/__init__.py::load_user).

Motivação: antes desta mudança, login() só LIA sessao_versao pra gravar no
cookie, nunca incrementava - duas sessões do mesmo usuário conviviam
normalmente (as duas batiam com o número do banco), o que permitia dois
logins simultâneos "brigarem" em silêncio pelo mesmo Local (local_atual_id,
seção 10.15): a sessão A escolhia um Local, a sessão B (outro
dispositivo/aba, mesmo usuário) continuava livre pra escolher/reconfirmar
outro sem que a A percebesse.

Cobre:
- Logar duas vezes como o mesmo usuário invalida a sessão antiga (a antiga
  vira 302 pro login na próxima requisição autenticada).
- A sessão NOVA continua válida - a ordem de operações (incrementa e
  persiste ANTES de gravar o cookie novo) não pode auto-invalidar quem
  acabou de logar.
- Cenário completo do problema original: sessão A escolhe um Local, sessão B
  loga com o mesmo usuário, sessão A tenta reconfirmar/agir no Local depois
  - é tratada como deslogada antes de conseguir fazer qualquer coisa (o
  double-booking silencioso não é mais possível).
- Usuario.sessao_versao realmente foi incrementado no banco (não só o
  comportamento observado via HTTP).
- admin/locais.py::desconectar_usuario_local continua funcionando do mesmo
  jeito (incrementa sessao_versao, libera o Local, não depende de nada desta
  mudança pra continuar correto).
- Dois usuários DIFERENTES logados ao mesmo tempo não se afetam (a
  invalidação é por usuário, não global).
- A sessão NOVA herda local_atual_id/TurnoAtivo normalmente (não fica
  "pendurada"/travada) - a sessão A inicia um turno completo (Local + Fila,
  TurnoAtivo criado) e é derrubada pela sessão B; a sessão B enxerga esse
  estado de cara (tela 'Iniciar Turno' pré-populada, tela de atendimento por-
  Local acessível) SEM precisar re-escolher nada, porque local_atual_id/
  TurnoAtivo vivem na linha do Usuario, não por sessão - decisão consciente
  de NÃO limpar esses campos no login (documentada no código e no adendo
  10.41).

Execução: python -m tests.test_regressao_single_session (a partir da raiz
do projeto, com o virtualenv ativado).
"""
import os

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

from app import create_app
from app.extensions import db
from app.models import Empresa, Fila, Local, Perfil, TurnoAtivo, Unidade, Usuario

app = create_app("testing")

passos = []


def ok(msg):
    passos.append(msg)
    print("OK:", msg)


with app.app_context():
    db.create_all()

    perfil_atendente = Perfil(nome="atendente")
    perfil_admin = Perfil(nome="admin")
    db.session.add_all([perfil_atendente, perfil_admin])
    db.session.commit()

    empresa = Empresa(nome="Clinica Single Session")
    db.session.add(empresa)
    db.session.commit()

    unidade = Unidade(empresa_id=empresa.id, nome="Unidade 1")
    db.session.add(unidade)
    db.session.commit()

    local_x = Local(
        empresa_id=empresa.id, unidade_id=unidade.id, nome="Guiche X",
        nome_exibicao="Guiche X", identificador_admin="[UNI] Guiche X",
    )
    local_y = Local(
        empresa_id=empresa.id, unidade_id=unidade.id, nome="Guiche Y",
        nome_exibicao="Guiche Y", identificador_admin="[UNI] Guiche Y",
    )
    db.session.add_all([local_x, local_y])
    db.session.commit()

    fila_y = Fila(empresa_id=empresa.id, unidade_id=unidade.id, nome="Fila Y", sigla="FY")
    db.session.add(fila_y)
    db.session.commit()

    def novo_usuario(login, perfis):
        u = Usuario(empresa_id=empresa.id, nome=login, login=login)
        u.set_senha("senha123")
        u.perfis = perfis
        u.unidades = [unidade]
        db.session.add(u)
        db.session.commit()
        return u.id

    id_operador = novo_usuario("operador_ss", [perfil_atendente])
    id_operador_b = novo_usuario("operador_ss_b", [perfil_atendente])
    id_operador_c = novo_usuario("operador_ss_c", [perfil_atendente])
    id_admin = novo_usuario("admin_ss", [perfil_admin])

    ids = dict(
        empresa=empresa.id, unidade=unidade.id, local_x=local_x.id, local_y=local_y.id,
        fila_y=fila_y.id, operador=id_operador, operador_b=id_operador_b,
        operador_c=id_operador_c, admin=id_admin,
    )


def login_como(login):
    c = app.test_client()
    resp = c.post("/login", data={"login": login, "senha": "senha123"}, follow_redirects=True)
    assert resp.status_code == 200, (login, resp.status_code)
    return c


def db_get_usuario(id_):
    with app.app_context():
        return db.session.get(Usuario, id_)


def csrf_de(c, url):
    resp = c.get(url)
    texto = resp.data.decode("utf-8")
    inicio = texto.index('name="csrf_token" value="') + len('name="csrf_token" value="')
    return texto[inicio:texto.index('"', inicio)]


# ===========================================================================
# A) Logar duas vezes como o mesmo usuário: a sessão ANTIGA fica inválida.
# ===========================================================================
sessao_a = login_como("operador_ss")
resp = sessao_a.get("/atendimento/local", follow_redirects=False)
assert resp.status_code == 200  # sessão A funciona normalmente antes do 2º login
ok("Sessão A (1º login) funciona normalmente antes de qualquer segundo login")

sessao_b = login_como("operador_ss")  # mesmo usuário, "segundo dispositivo"

resp_sessao_a_depois = sessao_a.get("/atendimento/local", follow_redirects=False)
assert resp_sessao_a_depois.status_code == 302
assert resp_sessao_a_depois.headers["Location"].startswith("/login") or "next=" in resp_sessao_a_depois.headers["Location"]
ok("Login novo (sessão B) invalida a sessão A automaticamente - próxima requisição de A vira 302 pro login")

# ===========================================================================
# B) A sessão NOVA (B) continua válida - a ordem de operações (incrementa
#    sessao_versao ANTES de gravar o cookie novo) não pode auto-invalidar
#    quem acabou de logar.
# ===========================================================================
resp_sessao_b = sessao_b.get("/atendimento/local", follow_redirects=False)
assert resp_sessao_b.status_code == 200
ok("Sessão B (a que acabou de logar) continua válida - não se auto-invalida")

with app.app_context():
    usuario_db = db.session.get(Usuario, ids["operador"])
    # cada POST /login bem-sucedido incrementa - começou em 0 (default), duas
    # chamadas a login_como() (sessão A + sessão B) = 0 -> 1 -> 2.
    assert usuario_db.sessao_versao == 2
ok("Usuario.sessao_versao foi realmente incrementado no banco a cada login (0 -> 2 após dois logins)")

# ===========================================================================
# C) Cenário completo do problema original: sessão A escolhe o Local X;
#    sessão B loga com o MESMO usuário; sessão A tenta reconfirmar o Local
#    depois - é tratada como deslogada ANTES de conseguir agir. O double-
#    booking silencioso de Local não é mais possível.
# ===========================================================================
sessao_c1 = login_como("operador_ss_b")
resp = sessao_c1.post("/atendimento/local", data={"local_id": ids["local_x"]}, follow_redirects=True)
assert resp.status_code == 200
u_antes = db_get_usuario(ids["operador_b"])
assert u_antes.local_atual_id == ids["local_x"]
ok("Sessão C1 escolhe o Local X normalmente")

sessao_c2 = login_como("operador_ss_b")  # mesmo usuário loga de novo, "outro dispositivo"

# sessão C1 tenta reconfirmar/agir no Local X - deveria ser bloqueada por
# estar deslogada, ANTES de qualquer lógica de negócio do Local rodar.
resp_c1_depois = sessao_c1.post(
    "/atendimento/local", data={"local_id": ids["local_x"]}, follow_redirects=False,
)
assert resp_c1_depois.status_code == 302
assert resp_c1_depois.headers["Location"].startswith("/login") or "next=" in resp_c1_depois.headers["Location"]
ok("Sessão C1 (antiga) é tratada como deslogada ao tentar reconfirmar o Local - nunca chega a agir nele")

# o registro no banco não mudou por causa da tentativa bloqueada da sessão C1.
u_depois = db_get_usuario(ids["operador_b"])
assert u_depois.local_atual_id == ids["local_x"]
ok("Estado do Local no banco não foi afetado pela tentativa da sessão antiga (nada de double-booking silencioso)")

# sessão C2 (a válida) continua livre pra operar o Local normalmente.
resp_c2 = sessao_c2.get("/atendimento/local", follow_redirects=False)
assert resp_c2.status_code == 200
ok("Sessão C2 (a que acabou de logar) continua operando normalmente")

# ===========================================================================
# D) Dois usuários DIFERENTES logados ao mesmo tempo não se afetam - a
#    invalidação é por usuário (sessao_versao é uma coluna do Usuario, não
#    algo global).
# ===========================================================================
sessao_admin = login_como("admin_ss")
resp = sessao_c2.get("/atendimento/local", follow_redirects=False)
assert resp.status_code == 200
ok("Login de um usuário DIFERENTE (admin) não invalida a sessão de outro usuário (operador_ss_b)")

# ===========================================================================
# F) A sessão NOVA herda local_atual_id/TurnoAtivo sem precisar re-escolher
#    nada - não fica "pendurada"/travada esperando ação. Sessão F1 completa
#    um turno de verdade (Fila + Local, TurnoAtivo criado de fato, não só
#    local_atual_id como nos blocos A-C); sessão F2 (mesmo usuário) loga por
#    cima, derrubando F1; a PRÓPRIA F2 já enxerga o Local ocupado e o turno
#    ativo de cara, sem re-escolher nada - prova que local_atual_id/
#    TurnoAtivo vivem na linha do Usuario, não por sessão, então não há
#    "estado pendurado" nem confusão de painel mostrando o operador em
#    atendimento num Local que ele nunca abriu NESTA sessão especificamente
#    (ele abriu no mesmo usuário, é o que importa pro sistema).
# ===========================================================================
URL_INICIAR_TURNO = "/atendimento/iniciar-turno"

sessao_f1 = login_como("operador_ss_c")
csrf = csrf_de(sessao_f1, URL_INICIAR_TURNO)
resp = sessao_f1.post(
    URL_INICIAR_TURNO,
    data={
        "csrf_token": csrf, "local_id": str(ids["local_y"]),
        "fila_id": [str(ids["fila_y"])], "chamadas_seguidas": ["1"],
    },
    follow_redirects=True,
)
assert resp.status_code == 200
with app.app_context():
    turno_f1 = TurnoAtivo.query.filter_by(usuario_id=ids["operador_c"], ativo=True).first()
    assert turno_f1 is not None
u_f1 = db_get_usuario(ids["operador_c"])
assert u_f1.local_atual_id == ids["local_y"]
ok("Sessão F1 inicia um turno completo (TurnoAtivo criado + Local Y ocupado)")

sessao_f2 = login_como("operador_ss_c")  # mesmo usuário, derruba F1

# a sessão nova NÃO precisa re-escolher nada: GET em 'Iniciar Turno' já chega
# pré-populado com o Local/Fila do turno aberto por F1 (mesmo comportamento
# de recuperação após reload já coberto em test_regressao_turno_ativo.py,
# aqui provado especificamente no contexto de single-session enforcement).
resp = sessao_f2.get(URL_INICIAR_TURNO)
assert resp.status_code == 200
texto_f2 = resp.data.decode("utf-8")
# Com o <select> substituído pelo widget de busca (10.10), a pré-seleção do
# Local vira o parâmetro `valorInicial` passado à fábrica criarBuscaAutocomplete()
# da instância de Local (atendimento/iniciar_turno.html).
assert f"valorInicial: {ids['local_y']}," in texto_f2
ok("Sessão F2 (nova) já enxerga o Local/Fila do turno aberto por F1 - 'Iniciar Turno' vem pré-populado, sem re-escolher")

# a tela de atendimento por-Local também abre direto, sem exigir nenhuma nova
# confirmação - mesmo estado, mesma linha do Usuario, nenhuma trava esperando.
resp = sessao_f2.get(f"/atendimento/recepcao/local/{ids['local_y']}")
assert resp.status_code == 200
ok("Sessão F2 acessa a tela de atendimento do Local direto, sem precisar reconfirmar o turno")

with app.app_context():
    turno_ainda_ativo = TurnoAtivo.query.filter_by(usuario_id=ids["operador_c"], ativo=True).first()
    assert turno_ainda_ativo is not None  # o novo login não encerrou o turno nem liberou o Local
u_f2 = db_get_usuario(ids["operador_c"])
assert u_f2.local_atual_id == ids["local_y"]
ok("O login novo NÃO limpa TurnoAtivo/local_atual_id - decisão consciente (10.41), estado continua íntegro pra sessão nova")

# ===========================================================================
# E) admin/locais.py::desconectar_usuario_local continua funcionando do
#    mesmo jeito - não depende de nada desta mudança, mas confirma que a
#    convivência dos dois mecanismos (login novo E botão do admin, ambos
#    incrementando sessao_versao) não quebra um ao outro.
# ===========================================================================
versao_antes_admin = db_get_usuario(ids["operador_b"]).sessao_versao

resp = sessao_admin.get("/admin/locais/ocupacao", follow_redirects=True)
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
csrf_token = texto[texto.index('name="csrf_token" value="') + len('name="csrf_token" value="'):]
csrf_token = csrf_token[:csrf_token.index('"')]

resp = sessao_admin.post(
    f"/admin/locais/ocupacao/{ids['operador_b']}/desconectar",
    data={"csrf_token": csrf_token}, follow_redirects=True,
)
assert resp.status_code == 200

u_desconectado = db_get_usuario(ids["operador_b"])
assert u_desconectado.local_atual_id is None
assert u_desconectado.sessao_versao == versao_antes_admin + 1
ok("desconectar_usuario_local continua incrementando sessao_versao e liberando o Local normalmente")

resp_sessao_c2_depois = sessao_c2.get("/atendimento/local", follow_redirects=False)
assert resp_sessao_c2_depois.status_code == 302
ok("Sessão C2 foi de fato derrubada pelo desconectar do admin (os dois mecanismos convivem sem conflito)")

# ===========================================================================
# G) Estrutural (mesmo espírito do "read the source" já usado noutros testes
#    deste projeto pra travar uma decisão de implementação, não só o
#    comportamento observável): login() precisa usar um UPDATE atômico no SQL
#    pra incrementar sessao_versao, NÃO `usuario.sessao_versao = usuario.
#    sessao_versao + 1` em Python (adendo 10.41, item 2.1) - esse padrão
#    ingênuo tem uma corrida real (read-then-write não atômico) sob dois
#    POST /login genuinamente simultâneos do mesmo usuário: os dois podem ler
#    o valor antigo antes de qualquer um commitar, calcular o mesmo próximo
#    valor, e as duas sessões nasceriam válidas ao mesmo tempo - exatamente a
#    fresta que este mecanismo existe pra fechar. O comportamento das seções
#    A-F acima passaria igual com o padrão ingênuo (a corrida só aparece sob
#    concorrência real, que este ambiente de teste não reproduz) - por isso
#    esta prova é estrutural, lendo o código-fonte diretamente.
# ===========================================================================
codigo_fonte_login = open("app/auth/routes.py", encoding="utf-8").read()
bloco_login = codigo_fonte_login[codigo_fonte_login.index("def login():"):codigo_fonte_login.index("def trocar_senha_obrigatoria")]
assert "update(Usuario)" in bloco_login
assert "sessao_versao=Usuario.sessao_versao + 1" in bloco_login
assert "usuario.sessao_versao = usuario.sessao_versao + 1" not in bloco_login
assert "usuario.sessao_versao = (usuario.sessao_versao or 0) + 1" not in bloco_login
ok("login() usa UPDATE atômico no SQL pra incrementar sessao_versao, não read-then-write em Python (evita lost update sob concorrência)")

print()
print(f"TODOS OS {len(passos)} TESTES DE SINGLE-SESSION ENFORCEMENT PASSARAM")
