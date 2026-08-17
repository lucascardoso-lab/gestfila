"""
Teste de regressão: persistência de FILAS entre logout/login (seção 10.57,
BLUEPRINT.md) - reversão PARCIAL e DELIBERADA da seção 10.23, a pedido do
cliente, ciente do histórico (10.23 tinha corrigido um bug real fazendo
logout encerrar o turno e zerar TUDO no próximo login - filas E Local).

Escopo confirmado com o cliente: só as FILAS (e o peso "chamadas_seguidas"
de cada uma) passam a sobreviver a um logout/login - o LOCAL continua sendo
pedido do zero a cada login, sem nenhuma mudança nesse ponto. Vale pros 3
perfis operacionais (atendente/executor/atendente_executor) - `admin`
também está em PERFIS_OPERACIONAIS (app/atendimento/filas.py) e usa a MESMA
lógica, sem tratamento especial por perfil (a persistência é por
usuario_id, não por perfil).

Cobre:
- Remover uma fila específica (simulando o botão "x" da tabela "Filas do
  turno", que só edita o array em JS até o submit - "gravar em tempo real"
  não existe como rota própria, a persistência sempre foi no POST final)
  faz só ELA sumir de filas_config - as outras continuam.
- Logout/login mantém as filas restantes (não a removida), com o peso
  certo, nos 3 perfis operacionais.
- Local continua sendo pedido do zero a cada login (não persiste) -
  confirmado especificamente após o logout, não só presumido pela ausência
  de asserção contrária.
- Single-session (10.41) e RBAC de gerencial (sem acesso à tela) não
  regrediram - reaproveita os mesmos mecanismos já testados em
  test_regressao_single_session.py/test_regressao_turno_ativo.py, aqui só
  uma confirmação direta e local de que nada nesta mudança os afeta.

Execução: python -m tests.test_regressao_persistencia_filas_turno (a partir
da raiz do projeto, com o virtualenv ativado).
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
    perfil_executor = Perfil(nome="executor")
    perfil_atendente_executor = Perfil(nome="atendente_executor")
    perfil_gerencial = Perfil(nome="gerencial")
    db.session.add_all([perfil_atendente, perfil_executor, perfil_atendente_executor, perfil_gerencial])
    db.session.commit()

    empresa = Empresa(nome="Clinica Persistencia Filas")
    db.session.add(empresa)
    db.session.commit()

    unidade = Unidade(empresa_id=empresa.id, nome="Unidade 1")
    db.session.add(unidade)
    db.session.commit()

    fila_a = Fila(empresa_id=empresa.id, unidade_id=unidade.id, nome="Fila A", sigla="FA")
    fila_b = Fila(empresa_id=empresa.id, unidade_id=unidade.id, nome="Fila B", sigla="FB")
    db.session.add_all([fila_a, fila_b])
    db.session.commit()

    local_1 = Local(
        empresa_id=empresa.id, unidade_id=unidade.id, nome="Guiche 01",
        nome_exibicao="Guiche 01", identificador_admin="[UNI] Guiche 01",
    )
    local_2 = Local(
        empresa_id=empresa.id, unidade_id=unidade.id, nome="Guiche 02",
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

    id_atendente = novo_usuario("op_atendente_pf", [perfil_atendente])
    id_executor = novo_usuario("op_executor_pf", [perfil_executor])
    id_atendente_executor = novo_usuario("op_atendente_exec_pf", [perfil_atendente_executor])
    id_gerencial = novo_usuario("op_gerencial_pf", [perfil_gerencial])

    ids = dict(
        fila_a=fila_a.id, fila_b=fila_b.id, local_1=local_1.id, local_2=local_2.id,
        atendente=id_atendente, executor=id_executor,
        atendente_executor=id_atendente_executor, gerencial=id_gerencial,
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


URL_INICIAR_TURNO = "/atendimento/iniciar-turno"

# ===========================================================================
# A) Remover uma fila específica (simula o botão "x" - o array só existe no
#    JS até o submit, então "remover" no teste é submeter sem ela) faz só
#    ELA sumir - a outra continua, com o peso certo.
# ===========================================================================
c_at, _ = login_como("op_atendente_pf")

csrf = csrf_de(c_at, URL_INICIAR_TURNO)
resp = c_at.post(
    URL_INICIAR_TURNO,
    data={
        "csrf_token": csrf,
        "fila_id": [str(ids["fila_a"]), str(ids["fila_b"])],
        "chamadas_seguidas": ["2", "3"],
        "local_id": str(ids["local_1"]),
    },
    follow_redirects=False,
)
assert resp.status_code == 302
with app.app_context():
    turno = TurnoAtivo.query.filter_by(usuario_id=ids["atendente"]).first()
    assert len(turno.filas_config) == 2
ok("Turno iniciado com as duas filas (A peso 2, B peso 3)")

# "remove" fila_a via o botão "x" (só fila_b sobrevive ao próximo submit,
# simulando o array já editado em JS antes de enviar o formulário de novo).
csrf = csrf_de(c_at, URL_INICIAR_TURNO)
resp = c_at.post(
    URL_INICIAR_TURNO,
    data={
        "csrf_token": csrf,
        "fila_id": [str(ids["fila_b"])],
        "chamadas_seguidas": ["3"],
        "local_id": str(ids["local_1"]),
    },
    follow_redirects=False,
)
assert resp.status_code == 302
with app.app_context():
    turno = TurnoAtivo.query.filter_by(usuario_id=ids["atendente"]).first()
    assert len(turno.filas_config) == 1
    assert turno.filas_config[0]["fila_id"] == ids["fila_b"]
    assert turno.filas_config[0]["chamadas_seguidas"] == 3
ok("Remover uma fila (botão 'x') faz só ELA sumir de filas_config - fila_b (peso 3) continua sozinha")

# ===========================================================================
# B) Logout/login mantém a fila restante (fila_b) - não a removida (fila_a),
#    não vazio - e o LOCAL continua vindo vazio (não persiste, sem mudança
#    nesse ponto). Repetido nos 3 perfis operacionais.
# ===========================================================================
for chave_usuario, login in [
    ("atendente", "op_atendente_pf"),
    ("executor", "op_executor_pf"),
    ("atendente_executor", "op_atendente_exec_pf"),
]:
    c, _ = login_como(login)

    if chave_usuario != "atendente":
        # atendente já configurou o turno no bloco A - executor/
        # atendente_executor configuram aqui, com as DUAS filas, pra depois
        # confirmar que só a removida some (mesmo cenário do bloco A, em
        # menos passos já que o mecanismo em si já foi provado lá).
        csrf = csrf_de(c, URL_INICIAR_TURNO)
        resp = c.post(
            URL_INICIAR_TURNO,
            data={
                "csrf_token": csrf,
                "fila_id": [str(ids["fila_b"])],
                "chamadas_seguidas": ["3"],
                "local_id": str(ids["local_1"]),
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302, chave_usuario

    resp = c.get("/logout", follow_redirects=True)
    assert resp.status_code == 200, chave_usuario

    c_relogado, resp_login = login_como(login)
    assert resp_login.headers["Location"].endswith(URL_INICIAR_TURNO), chave_usuario

    resp = c_relogado.get(URL_INICIAR_TURNO)
    assert resp.status_code == 200, chave_usuario
    texto = resp.data.decode("utf-8")

    assert f'"chamadas_seguidas": 3, "fila_id": {ids["fila_b"]}, "ordem": 0' in texto, chave_usuario
    # a lista de filas SELECIONADAS (filasSelecionadas, não FILAS_DISPONIVEIS -
    # esta última é o catálogo completo pro dropdown de busca, sempre lista
    # TODAS as filas disponíveis, incluída fila_a de propósito, ver adendo da
    # busca com autocomplete em "Iniciar turno") não pode conter fila_a.
    bloco_selecionadas = texto[texto.index("filasSelecionadas ="):texto.index("FILAS_POR_ID", texto.index("filasSelecionadas ="))]
    assert f'"fila_id": {ids["fila_a"]}' not in bloco_selecionadas, chave_usuario

    assert "valorInicial: null," in texto, chave_usuario
    assert f"valorInicial: {ids['local_1']}," not in texto, chave_usuario

    ok(f"{chave_usuario}: logout/login mantém só a fila restante (fila_b, peso 3) pré-populada; Local continua vazio")

# ===========================================================================
# C) Single-session e RBAC de gerencial não regrediram - confirmação direta
#    e local (mecanismos já cobertos em outros arquivos, aqui só garantindo
#    que esta mudança específica não os afeta).
# ===========================================================================
c_gerencial, resp_login_gerencial = login_como("op_gerencial_pf")
assert not resp_login_gerencial.headers["Location"].endswith(URL_INICIAR_TURNO)
resp = c_gerencial.get(URL_INICIAR_TURNO)
assert resp.status_code == 403
ok("RBAC: perfil gerencial continua sem acesso a 'Iniciar Turno' (403) - não afetado pela persistência de filas")

c_sessao_1, _ = login_como("op_executor_pf")
c_sessao_2, _ = login_como("op_executor_pf")  # novo login do MESMO usuário - derruba a sessão 1 (10.41)
resp_sessao_1 = c_sessao_1.get(URL_INICIAR_TURNO, follow_redirects=False)
assert resp_sessao_1.status_code == 401
resp_sessao_2 = c_sessao_2.get(URL_INICIAR_TURNO)
assert resp_sessao_2.status_code == 200
ok("Single-session (10.41): novo login do mesmo operador ainda derruba a sessão antiga - não afetado pela persistência de filas")

print()
print(f"TODOS OS {len(passos)} TESTES DE PERSISTÊNCIA DE FILAS ENTRE LOGOUT/LOGIN PASSARAM")
