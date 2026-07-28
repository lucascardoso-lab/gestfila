"""
Teste de regressão: Enter nos campos "Código"/"Nome" da tela de Atendimento não
pode disparar submissão implícita do <form id="form-acoes"> (que sempre usaria o
formaction do PRIMEIRO botão submit no DOM - "Chamar" - mesmo que o operador
estivesse preenchendo o campo para usar Realocar/Finalizar/Aguardar em seguida).

Correção: onkeydown="if (event.key === 'Enter') event.preventDefault();" nos
inputs #codigo e #nome, em painel_fila.html e painel_local.html.

LIMITAÇÃO DE COBERTURA: a submissão implícita ao apertar Enter é um
comportamento do próprio motor de renderização do navegador (HTML living
standard, "implicit submission"), não algo que o servidor recebe ou que o
Flask test_client consiga disparar - não há uma requisição HTTP distinta para
"Enter" vs "clique no botão X" que se possa simular aqui. Este teste cobre o
que é possível no nível de servidor/HTML: confirma que o atributo onkeydown de
bloqueio está presente nos dois campos de texto, nas duas telas. Verificação
manual/E2E (Selenium/Playwright) seria necessária para exercitar o
comportamento real do teclado num navegador.

Execução: python -m tests.test_regressao_enter_submissao_implicita (a partir da
raiz do projeto, com o virtualenv ativado).
"""
import os
from datetime import datetime

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

from app import create_app
from app.extensions import db
from app.models import Empresa, Fila, Local, Perfil, Senha, Unidade, Usuario

app = create_app("testing")

passos = []


def ok(msg):
    passos.append(msg)
    print("OK:", msg)


with app.app_context():
    db.create_all()

    perfil_recepcao = Perfil(nome="atendente")
    db.session.add(perfil_recepcao)
    db.session.commit()

    empresa_a = Empresa(nome="Clinica A")
    db.session.add(empresa_a)
    db.session.commit()

    unidade_a1 = Unidade(empresa_id=empresa_a.id, nome="Unidade A1")
    db.session.add(unidade_a1)
    db.session.commit()

    fila_a = Fila(empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="Fila A", sigla="FA")
    db.session.add(fila_a)
    db.session.commit()

    local_guiche01 = Local(empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="Guiche 01")
    db.session.add(local_guiche01)
    db.session.commit()

    senha_chamada = Senha(
        empresa_id=empresa_a.id, unidade_id=unidade_a1.id, fila_id=fila_a.id,
        codigo="FA001", sequencial=1, prioridade=False, status="chamada",
        chamada_em=datetime.utcnow(), local_id=local_guiche01.id,
    )
    db.session.add(senha_chamada)
    db.session.commit()

    def novo_usuario(login, empresa_id, perfis, unidades):
        u = Usuario(empresa_id=empresa_id, nome=login, login=login)
        u.set_senha("senha123")
        u.perfis = perfis
        u.unidades = unidades
        db.session.add(u)
        db.session.commit()
        return u.id

    id_recepcao_a = novo_usuario("recepcao_a_enter", empresa_a.id, [perfil_recepcao], [unidade_a1])

    ids = dict(
        fila_a=fila_a.id, local_guiche01=local_guiche01.id,
    )

print("IDS", ids)


def login_como(login):
    c = app.test_client()
    resp = c.post("/login", data={"login": login, "senha": "senha123"}, follow_redirects=True)
    assert resp.status_code == 200, (login, resp.status_code)
    return c


c_recepcao = login_como("recepcao_a_enter")
with c_recepcao.session_transaction() as sess:
    sess["atendimento_local_id"] = ids["local_guiche01"]

GUARDA_ENTER = "onkeydown=\"if (event.key === 'Enter') event.preventDefault();\""

# ---------------------------------------------------------------------------
# 1) painel_fila.html: #codigo e #nome bloqueiam Enter (evita submissão
#    implícita usando o formaction de "Chamar", o primeiro submit do form).
# ---------------------------------------------------------------------------
resp = c_recepcao.get(f"/atendimento/recepcao/filas/{ids['fila_a']}")
assert resp.status_code == 200
texto = resp.data.decode("utf-8")

trecho_codigo = texto[texto.index('id="codigo"'):texto.index('id="codigo"') + 200]
trecho_nome = texto[texto.index('id="nome"'):texto.index('id="nome"') + 300]
assert GUARDA_ENTER in trecho_codigo, "campo #codigo sem guarda de Enter em painel_fila.html"
assert GUARDA_ENTER in trecho_nome, "campo #nome sem guarda de Enter em painel_fila.html"
ok("painel_fila: #codigo e #nome bloqueiam submissão implícita via Enter")

# ---------------------------------------------------------------------------
# 2) painel_local.html: mesma correção.
# ---------------------------------------------------------------------------
resp = c_recepcao.get(f"/atendimento/recepcao/local/{ids['local_guiche01']}")
assert resp.status_code == 200
texto = resp.data.decode("utf-8")

trecho_codigo = texto[texto.index('id="codigo"'):texto.index('id="codigo"') + 200]
trecho_nome = texto[texto.index('id="nome"'):texto.index('id="nome"') + 300]
assert GUARDA_ENTER in trecho_codigo, "campo #codigo sem guarda de Enter em painel_local.html"
assert GUARDA_ENTER in trecho_nome, "campo #nome sem guarda de Enter em painel_local.html"
ok("painel_local: #codigo e #nome bloqueiam submissão implícita via Enter")

print()
print(f"TODOS OS {len(passos)} TESTES DA GUARDA DE ENTER (SUBMISSAO IMPLICITA) PASSARAM")
