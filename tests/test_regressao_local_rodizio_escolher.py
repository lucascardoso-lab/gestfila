"""
Teste de regressão: escolher_local() redirecionando para a tela unificada por-Local
quando o Local tiver rodízio configurado (adendo 10.8, Fase 10.8-F - última fase).

Ponto central pedido para revisão nesta fase: um Local SEM rodízio configurado
continua se comportando EXATAMENTE como antes da Fase F - mesmo fallback
(`listar_filas`), independente de haver ou não um 'next' explícito. Só Locais COM
rodízio configurado E sem 'next' explícito passam a ser redirecionados
automaticamente para `painel_local` - qualquer 'next' explícito (ex.: 'Trocar
local' a partir de uma tela já aberta, ou o redirecionamento de
_exigir_local_ou_redirecionar de volta para uma fila específica) sempre tem
prioridade e nunca é sobrescrito, mesmo quando o Local escolhido tem rodízio.

Execução: python -m tests.test_regressao_local_rodizio_escolher (a partir da raiz
do projeto, com o virtualenv ativado).
"""
import os

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

from app import create_app
from app.extensions import db
from app.models import Empresa, Fila, Local, LocalRodizioFila, Perfil, Unidade, Usuario

app = create_app("testing")

passos = []


def ok(msg):
    passos.append(msg)
    print("OK:", msg)


with app.app_context():
    db.create_all()

    perfil_recepcao = Perfil(nome="atendente")
    perfil_coleta = Perfil(nome="executor")
    perfil_gerencial = Perfil(nome="gerencial")
    db.session.add_all([perfil_recepcao, perfil_coleta, perfil_gerencial])
    db.session.commit()

    empresa = Empresa(nome="Clinica A")
    db.session.add(empresa)
    db.session.commit()

    unidade = Unidade(empresa_id=empresa.id, nome="Unidade A1")
    db.session.add(unidade)
    db.session.commit()

    fila_x = Fila(empresa_id=empresa.id, unidade_id=unidade.id, nome="Fila X", sigla="FX")
    db.session.add(fila_x)
    db.session.commit()

    local_com_rodizio = Local(empresa_id=empresa.id, unidade_id=unidade.id, nome="Local Com Rodizio")
    local_sem_rodizio = Local(empresa_id=empresa.id, unidade_id=unidade.id, nome="Local Sem Rodizio")
    # local próprio pra coleta_f/gerencial_f (passos 5/6) - não pode ser o mesmo
    # local_com_rodizio que recepcao_f já ocupa nos passos 3/4, já que dois usuários
    # não podem mais ocupar o mesmo Local ao mesmo tempo (ver test_regressao_ocupacao_local.py).
    local_com_rodizio_2 = Local(empresa_id=empresa.id, unidade_id=unidade.id, nome="Local Com Rodizio 2")
    db.session.add_all([local_com_rodizio, local_sem_rodizio, local_com_rodizio_2])
    db.session.commit()

    db.session.add(LocalRodizioFila(local_id=local_com_rodizio.id, fila_id=fila_x.id, ordem=0, chamadas_seguidas=1))
    db.session.add(LocalRodizioFila(local_id=local_com_rodizio_2.id, fila_id=fila_x.id, ordem=0, chamadas_seguidas=1))
    db.session.commit()

    def novo_usuario(login, perfis):
        u = Usuario(empresa_id=empresa.id, nome=login, login=login)
        u.set_senha("senha123")
        u.perfis = perfis
        u.unidades = [unidade]
        db.session.add(u)
        db.session.commit()
        return u.id

    id_recepcao = novo_usuario("recepcao_f", [perfil_recepcao])
    id_coleta = novo_usuario("coleta_f", [perfil_coleta])
    id_gerencial = novo_usuario("gerencial_f", [perfil_gerencial])

    ids = dict(
        empresa=empresa.id, unidade=unidade.id, fila_x=fila_x.id,
        local_com_rodizio=local_com_rodizio.id, local_sem_rodizio=local_sem_rodizio.id,
        local_com_rodizio_2=local_com_rodizio_2.id,
        recepcao=id_recepcao, coleta=id_coleta, gerencial=id_gerencial,
    )

print("IDS", ids)


def login_como(login):
    c = app.test_client()
    resp = c.post("/login", data={"login": login, "senha": "senha123"}, follow_redirects=True)
    assert resp.status_code == 200, (login, resp.status_code)
    return c


c_recepcao = login_como("recepcao_f")
c_coleta = login_como("coleta_f")
c_gerencial = login_como("gerencial_f")

# ---------------------------------------------------------------------------
# 1) Local SEM rodízio, SEM 'next' explícito: comportamento idêntico ao de
#    antes da Fase F - cai no fallback `listar_filas`.
# ---------------------------------------------------------------------------
resp = c_recepcao.post(
    "/atendimento/local", data={"local_id": ids["local_sem_rodizio"]}, follow_redirects=False
)
assert resp.status_code == 302
assert resp.headers["Location"] == "/atendimento/filas"
ok("Local SEM rodízio, sem 'next': redireciona para /atendimento/filas (fallback antigo, sem mudança)")

# ---------------------------------------------------------------------------
# 2) Local SEM rodízio, COM 'next' explícito: continua respeitando o 'next',
#    exatamente como antes da Fase F.
# ---------------------------------------------------------------------------
resp = c_recepcao.post(
    "/atendimento/local",
    data={"local_id": ids["local_sem_rodizio"], "next": "/atendimento/filas"},
    follow_redirects=False,
)
assert resp.status_code == 302
assert resp.headers["Location"] == "/atendimento/filas"
ok("Local SEM rodízio, com 'next' explícito: respeita o 'next' informado, sem mudança")

# ---------------------------------------------------------------------------
# 3) Local COM rodízio configurado, SEM 'next' explícito: Fase 10.8-F em ação -
#    redireciona automaticamente para a tela unificada painel_local.
# ---------------------------------------------------------------------------
resp = c_recepcao.post(
    "/atendimento/local", data={"local_id": ids["local_com_rodizio"]}, follow_redirects=False
)
assert resp.status_code == 302
assert resp.headers["Location"] == f"/atendimento/recepcao/local/{ids['local_com_rodizio']}"
ok("Local COM rodízio, sem 'next': redireciona automaticamente para painel_local (Fase 10.8-F)")

# Confirma que a tela para a qual foi redirecionado carrega normalmente.
resp = c_recepcao.get(f"/atendimento/recepcao/local/{ids['local_com_rodizio']}")
assert resp.status_code == 200
ok("Redirecionamento automático aponta para uma tela painel_local válida (200)")

# ---------------------------------------------------------------------------
# 4) Local COM rodízio configurado, mas com 'next' EXPLÍCITO: o 'next' sempre
#    tem prioridade - não há bloqueio de acesso à tela por-fila (seção 3 do
#    adendo), o redirecionamento automático é só uma sugestão de fluxo quando
#    não há nenhum destino específico em jogo.
# ---------------------------------------------------------------------------
resp = c_recepcao.post(
    "/atendimento/local",
    data={"local_id": ids["local_com_rodizio"], "next": "/atendimento/filas"},
    follow_redirects=False,
)
assert resp.status_code == 302
assert resp.headers["Location"] == "/atendimento/filas"
ok("Local COM rodízio, mas com 'next' explícito: 'next' tem prioridade, NÃO redireciona para painel_local")

resp = c_recepcao.post(
    "/atendimento/local",
    data={
        "local_id": ids["local_com_rodizio"],
        "next": f"/atendimento/recepcao/filas/{ids['fila_x']}",
    },
    follow_redirects=False,
)
assert resp.status_code == 302
assert resp.headers["Location"] == f"/atendimento/recepcao/filas/{ids['fila_x']}"
ok("Local COM rodízio: 'next' apontando para a tela por-fila específica (ex.: 'Trocar local') é respeitado")

# ---------------------------------------------------------------------------
# 5) Setor padrão do redirecionamento automático: usuário só com perfil 'executor'
#    cai em painel_local com setor='coleta', não 'recepcao'.
# ---------------------------------------------------------------------------
resp = c_coleta.post(
    "/atendimento/local", data={"local_id": ids["local_com_rodizio_2"]}, follow_redirects=False
)
assert resp.status_code == 302
assert resp.headers["Location"] == f"/atendimento/coleta/local/{ids['local_com_rodizio_2']}"
ok("Usuário só 'coleta': redirecionamento automático usa setor='coleta', não 'recepcao'")

resp = c_coleta.get(f"/atendimento/coleta/local/{ids['local_com_rodizio_2']}")
assert resp.status_code == 200
ok("Redirecionamento automático (setor='coleta') aponta para uma tela painel_local válida (200)")

# libera local_com_rodizio_2 pra próxima persona (gerencial_f) poder ocupá-lo -
# dois usuários não podem mais ocupar o mesmo Local ao mesmo tempo.
c_coleta.get("/logout", follow_redirects=True)

# ---------------------------------------------------------------------------
# 6) Usuário sem acesso a nenhum dos dois setores (só 'gerencial') escolhendo um
#    Local COM rodízio configurado: cai em listar_filas, NÃO em painel_local -
#    redirecionar pra lá resultaria num 403 imediato em exigir_acesso_setor,
#    pior experiência do que o fallback antigo que ele já usaria normalmente.
# ---------------------------------------------------------------------------
resp = c_gerencial.post(
    "/atendimento/local", data={"local_id": ids["local_com_rodizio_2"]}, follow_redirects=False
)
assert resp.status_code == 302
assert resp.headers["Location"] == "/atendimento/filas"
ok("Usuário só 'gerencial' escolhendo Local COM rodízio: cai em listar_filas, não em painel_local/403")

# Confirma que painel_local realmente barraria esse usuário com 403 (prova de que
# o fallback acima não é só teórico - é o que de fato evita o erro).
resp = c_gerencial.get(f"/atendimento/coleta/local/{ids['local_com_rodizio_2']}")
assert resp.status_code == 403
ok("Confirmação: painel_local de fato retornaria 403 para 'gerencial' - o fallback evita esse erro")

print()
print(f"TODOS OS {len(passos)} TESTES DA FASE 10.8-F (ESCOLHER_LOCAL -> PAINEL_LOCAL) PASSARAM")
