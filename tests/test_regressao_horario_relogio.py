"""
Teste de regressão: horário de relógio exibido junto da duração nas telas de
Atendimento (painel_fila.html e painel_local.html) - complementa a duração já
existente (T. Espera ao vivo / Tempo de espera congelado) com o horário em que
a senha foi gerada ("Senhas em espera") ou chamada ("Senhas atendidas").

Cobre:
- O HTML renderizado carrega o timestamp bruto (`data-hora-de`, ISO sem "Z")
  tanto em "Senhas em espera" (criada_em) quanto em "Senhas atendidas"
  (chamada_em) - a formatação em si (`toLocaleTimeString`) é sempre feita no
  navegador (função `formatarHoraLocal`), nunca no servidor, porque só o
  navegador sabe o fuso horário local da clínica (mesmo princípio já usado no
  cálculo de T. Espera - `criada_em`/`chamada_em` são UTC "naive" no banco).
- O endpoint JSON usado pelo refresh via WebSocket (`/dados`, `/local/.../dados`)
  agora inclui `chamada_em` em cada item de "atendidas" (antes só existia em
  "espera"), para o JS reconstruir o mesmo horário depois de um refresh.
- Isso vale nas duas telas (por-fila e por-local, adendo 10.8).

Execução: python -m tests.test_regressao_horario_relogio (a partir da raiz do
projeto, com o virtualenv ativado).
"""
import os
from datetime import datetime

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

from app import create_app
from app.extensions import db
from app.models import Empresa, Fila, Local, LocalRodizioFila, Perfil, Senha, Unidade, Usuario

app = create_app("testing")

passos = []


def ok(msg):
    passos.append(msg)
    print("OK:", msg)


CRIADA_EM = datetime(2026, 7, 23, 11, 5, 0)
CHAMADA_EM = datetime(2026, 7, 23, 14, 32, 0)

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

    local_guiche01 = Local(
        empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="Guichê 01",
        nome_exibicao="Guichê 01", identificador_admin="[UNI] Guichê 01",
    )
    db.session.add(local_guiche01)
    db.session.commit()

    db.session.add(LocalRodizioFila(local_id=local_guiche01.id, fila_id=fila_a.id, ordem=0, chamadas_seguidas=1))
    db.session.commit()

    senha_esperando = Senha(
        empresa_id=empresa_a.id, unidade_id=unidade_a1.id, fila_id=fila_a.id,
        codigo="FA001", sequencial=1, prioridade=False, status="aguardando",
        criada_em=CRIADA_EM,
    )
    senha_atendida = Senha(
        empresa_id=empresa_a.id, unidade_id=unidade_a1.id, fila_id=fila_a.id,
        codigo="FA002", sequencial=2, prioridade=False, status="finalizada",
        criada_em=CRIADA_EM, chamada_em=CHAMADA_EM, local_id=local_guiche01.id,
    )
    db.session.add_all([senha_esperando, senha_atendida])
    db.session.commit()

    def novo_usuario(login, empresa_id, perfis, unidades):
        u = Usuario(empresa_id=empresa_id, nome=login, login=login)
        u.set_senha("senha123")
        u.perfis = perfis
        u.unidades = unidades
        db.session.add(u)
        db.session.commit()
        return u.id

    id_recepcao_a = novo_usuario("recepcao_a_horario", empresa_a.id, [perfil_recepcao], [unidade_a1])

    ids = dict(fila_a=fila_a.id, local_guiche01=local_guiche01.id)


def login_como(login):
    c = app.test_client()
    resp = c.post("/login", data={"login": login, "senha": "senha123"}, follow_redirects=True)
    assert resp.status_code == 200, (login, resp.status_code)
    return c


c_recepcao = login_como("recepcao_a_horario")
with c_recepcao.session_transaction() as sess:
    sess["atendimento_local_id"] = ids["local_guiche01"]

ISO_CRIADA = CRIADA_EM.isoformat()
ISO_CHAMADA = CHAMADA_EM.isoformat()

# ===========================================================================
# A) painel_fila.html: horário embutido em "Senhas em espera" (criada_em) e em
#    "Senhas atendidas" (chamada_em), como atributo bruto pro JS formatar.
# ===========================================================================
resp = c_recepcao.get(f"/atendimento/recepcao/filas/{ids['fila_a']}")
assert resp.status_code == 200
texto = resp.data.decode("utf-8")

assert f'data-hora-de="{ISO_CRIADA}"' in texto
assert 'title="Horário em que a senha foi gerada"' in texto
ok("painel_fila: 'Senhas em espera' carrega data-hora-de com criada_em (horário de geração)")

assert f'data-hora-de="{ISO_CHAMADA}"' in texto
assert 'title="Horário em que a senha foi chamada"' in texto
ok("painel_fila: 'Senhas atendidas' carrega data-hora-de com chamada_em (horário da chamada)")

assert "function formatarHoraLocal" in texto
ok("painel_fila: formatação do horário (toLocaleTimeString) acontece no navegador, não no servidor")

# ===========================================================================
# B) /dados (JSON usado pelo refresh via WebSocket) inclui chamada_em em cada
#    item de "atendidas" - antes só existia em "espera".
# ===========================================================================
resp = c_recepcao.get(f"/atendimento/recepcao/filas/{ids['fila_a']}/dados")
assert resp.status_code == 200
corpo = resp.get_json()

item_espera = next(i for i in corpo["espera"] if i["codigo"] == "FA001")
assert item_espera["criada_em"] == ISO_CRIADA
ok("dados_fila: item de 'espera' traz criada_em (já existia, sem mudança)")

item_atendida = next(i for i in corpo["atendidas"] if i["codigo"] == "FA002")
assert item_atendida["chamada_em"] == ISO_CHAMADA
ok("dados_fila: item de 'atendidas' agora traz chamada_em (novo campo)")

# ===========================================================================
# C) Mesma correção na tela por-Local (adendo 10.8) - espera e atendidas.
# ===========================================================================
resp = c_recepcao.get(f"/atendimento/recepcao/local/{ids['local_guiche01']}")
assert resp.status_code == 200
texto_local = resp.data.decode("utf-8")

assert f'data-hora-de="{ISO_CRIADA}"' in texto_local
assert f'data-hora-de="{ISO_CHAMADA}"' in texto_local
ok("painel_local: mesmos data-hora-de (criada_em/chamada_em) presentes na tela por-Local")

resp = c_recepcao.get(f"/atendimento/recepcao/local/{ids['local_guiche01']}/dados")
assert resp.status_code == 200
corpo_local = resp.get_json()
item_atendida_local = next(i for i in corpo_local["atendidas"] if i["codigo"] == "FA002")
assert item_atendida_local["chamada_em"] == ISO_CHAMADA
ok("dados_local: item de 'atendidas' também traz chamada_em (mesmo serializador compartilhado)")

print()
print(f"TODOS OS {len(passos)} TESTES DO HORARIO DE RELOGIO JUNTO DA DURACAO PASSARAM")
