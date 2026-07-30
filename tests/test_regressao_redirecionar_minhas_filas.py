"""
Teste de regressão: remoção da seção "Minhas filas" (acesso rápido) do bloco
Redirecionar - adendo 10.20 do BLUEPRINT, removido a pedido do cliente (não é
correção de bug). Ver BLUEPRINT.md pra decisão completa.

app/atendimento/helpers.py::filas_rapidas_redirecionar() continua existindo no
repositório (código legado, não apagado - mesma convenção adotada para
app/totem/impressao.py), mas nenhum dos dois route handlers (painel_local,
painel_fila) chama mais nada dela.

Cobre:
- Mesmo com um operador tendo turno ativo configurado pra este Local (cenário
  que ANTES fazia a seção "Minhas filas" aparecer), ela não aparece mais em
  nenhuma das duas telas (painel_fila.html - seção 10.2 - e painel_local.html
  - adendo 10.8), já que as duas compartilham o mesmo partial
  _redirecionar_busca.html.
- A busca livre (adendo 10.10) continua funcionando normalmente nas duas
  telas - é o único modo de seleção de destino que resta.
- O campo oculto fila_destino_id (mesmo usado antes pelos botões rápidos)
  continua sendo o campo consumido pelo Realocar - o backend não muda.
- filas_rapidas_redirecionar() continua importável de app/atendimento/helpers.py
  (função preservada, só não é mais chamada).

Execução: python -m tests.test_regressao_redirecionar_minhas_filas (a partir
da raiz do projeto, com o virtualenv ativado).
"""
import os

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

from app import create_app
from app.extensions import db
from app.models import Empresa, Fila, Local, Perfil, Senha, TurnoAtivo, Unidade, Usuario
from app.models.fila import DIAS_SEMANA

app = create_app("testing")

passos = []


def ok(msg):
    passos.append(msg)
    print("OK:", msg)


SEMPRE_ABERTA = {dia: {"manha": True, "tarde": True} for dia in DIAS_SEMANA}

with app.app_context():
    db.create_all()

    perfil_recepcao = Perfil(nome="atendente")
    db.session.add(perfil_recepcao)
    db.session.commit()

    empresa = Empresa(nome="Clinica A")
    db.session.add(empresa)
    db.session.commit()

    unidade = Unidade(empresa_id=empresa.id, nome="Unidade A1")
    db.session.add(unidade)
    db.session.commit()

    fila_origem = Fila(empresa_id=empresa.id, unidade_id=unidade.id, nome="Recepção Geral", sigla="RG")
    fila_turno_1 = Fila(
        empresa_id=empresa.id, unidade_id=unidade.id, nome="Dr. Aberto", sigla="DA",
        horario_funcionamento=dict(SEMPRE_ABERTA),
    )
    db.session.add_all([fila_origem, fila_turno_1])
    db.session.commit()

    local = Local(
        empresa_id=empresa.id, unidade_id=unidade.id, nome="Guiche 01", tipo="recepcao",
        nome_exibicao="Guiche 01", identificador_admin="[UNI] Guiche 01",
    )
    db.session.add(local)
    db.session.commit()

    def novo_usuario(login, perfis):
        u = Usuario(empresa_id=empresa.id, nome=login, login=login)
        u.set_senha("senha123")
        u.perfis = perfis
        u.unidades = [unidade]
        db.session.add(u)
        db.session.commit()
        return u.id

    id_com_turno = novo_usuario("recepcao_mf_com_turno", [perfil_recepcao])

    # turno ativo do operador, no `local` em questão - cenário que ANTES da
    # remoção fazia a seção "Minhas filas" aparecer com fila_turno_1 dentro.
    db.session.add(TurnoAtivo(
        usuario_id=id_com_turno, local_id=local.id, ativo=True,
        filas_config=[{"fila_id": fila_turno_1.id, "ordem": 0, "chamadas_seguidas": 1}],
    ))
    db.session.commit()

    def nova_senha_chamada(operador_id, codigo="RG001"):
        s = Senha(
            empresa_id=empresa.id, unidade_id=unidade.id, fila_id=fila_origem.id,
            codigo=codigo, sequencial=1, prioridade=False, status="chamada",
            local_id=local.id, operador_id=operador_id, setor="recepcao",
        )
        db.session.add(s)
        db.session.commit()
        return s.id

    id_senha_com_turno = nova_senha_chamada(id_com_turno, "RG001")

    ids = dict(
        fila_origem=fila_origem.id, fila_turno_1=fila_turno_1.id, local=local.id,
        senha_com_turno=id_senha_com_turno,
    )


def login_como(login, local_id):
    c = app.test_client()
    resp = c.post("/login", data={"login": login, "senha": "senha123"}, follow_redirects=True)
    assert resp.status_code == 200, (login, resp.status_code)
    with c.session_transaction() as sess:
        sess["atendimento_local_id"] = local_id
    return c


URL_FILA_ORIGEM = f"/atendimento/recepcao/filas/{ids['fila_origem']}"
URL_LOCAL = f"/atendimento/recepcao/local/{ids['local']}"

c_com_turno = login_como("recepcao_mf_com_turno", ids["local"])

# ===========================================================================
# A) painel_fila.html (seção 10.2): mesmo com turno ativo configurado pra
#    este Local (cenário que antes exibia "Minhas filas" com fila_turno_1
#    dentro), a seção não aparece mais - só a busca livre.
# ===========================================================================
resp = c_com_turno.get(URL_FILA_ORIGEM)
assert resp.status_code == 200
texto_fila = resp.data.decode("utf-8")

assert "Minhas filas" not in texto_fila
assert 'id="grade-filas-rapidas"' not in texto_fila
assert "botao-fila-rapida" not in texto_fila
assert "divisor-secoes-redirecionar" not in texto_fila
ok("painel_fila.html: seção 'Minhas filas' não aparece mais, mesmo com turno ativo configurado")

assert 'id="busca-redirecionar"' in texto_fila
assert 'id="fila-destino-id"' in texto_fila
assert str(ids["fila_turno_1"]) in texto_fila  # continua disponível via busca livre
ok("painel_fila.html: busca livre continua funcionando, com a fila do turno disponível nela")

# ===========================================================================
# B) painel_local.html (adendo 10.8): mesma verificação - o mesmo partial é
#    incluído pelas duas telas, então a remoção precisa valer nas duas.
# ===========================================================================
resp = c_com_turno.get(URL_LOCAL)
assert resp.status_code == 200
texto_local = resp.data.decode("utf-8")

assert "Minhas filas" not in texto_local
assert 'id="grade-filas-rapidas"' not in texto_local
assert "botao-fila-rapida" not in texto_local
assert "divisor-secoes-redirecionar" not in texto_local
ok("painel_local.html: seção 'Minhas filas' não aparece mais, mesmo com turno ativo configurado")

assert 'id="busca-redirecionar"' in texto_local
assert 'id="fila-destino-id"' in texto_local
ok("painel_local.html: busca livre continua funcionando")

# ===========================================================================
# C) Realocar continua funcionando de ponta a ponta usando fila_destino_id -
#    único modo de seleção de destino que resta (busca livre).
# ===========================================================================
resp = c_com_turno.post(
    f"{URL_FILA_ORIGEM}/realocar",
    data={"fila_destino_id": str(ids["fila_turno_1"])},
    follow_redirects=True,
)
assert resp.status_code == 200
assert "realocada para" in resp.data.decode("utf-8")

with app.app_context():
    atualizada = db.session.get(Senha, ids["senha_com_turno"])
    assert atualizada.fila_id == ids["fila_turno_1"]
    assert atualizada.status == "aguardando"
ok("Realocar continua funcionando de ponta a ponta via busca livre, backend inalterado")

# ===========================================================================
# D) filas_rapidas_redirecionar() continua existindo em helpers.py (código
#    legado preservado, só não é mais chamada pelas rotas).
# ===========================================================================
from app.atendimento.helpers import filas_rapidas_redirecionar

with app.app_context():
    local_obj = db.session.get(Local, ids["local"])
    usuario_obj = db.session.get(Usuario, id_com_turno)
    resultado = filas_rapidas_redirecionar(local_obj, usuario_obj, [])
    assert isinstance(resultado, list)
ok("filas_rapidas_redirecionar() continua existindo e funcional em helpers.py (código legado, não apagado)")

import inspect

import app.atendimento.chamada as chamada_module

codigo_fonte = inspect.getsource(chamada_module)
assert "filas_rapidas_redirecionar" not in codigo_fonte
ok("app/atendimento/chamada.py não referencia mais filas_rapidas_redirecionar (import e chamadas removidos)")

print()
print(f"TODOS OS {len(passos)} TESTES DE REMOÇÃO DE 'MINHAS FILAS' NO BLOCO REDIRECIONAR PASSARAM")
