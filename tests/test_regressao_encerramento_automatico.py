"""
Teste de regressão: encerramento automático das sessões de atendimento (seção
10.18 do BLUEPRINT) - aviso antecipado com contagem regressiva, redirecionamento
pro logout no horário configurado, cancelamento das senhas em andamento, e
confirmação de que totem/TV/admin não são afetados.

Limitação de infraestrutura, mesmo motivo já registrado em
test_regressao_som_configuravel.py/test_regressao_enter_submissao_implicita.py:
sem runtime de navegador nos testes (só Flask test client), não dá pra
verificar de fato a contagem regressiva rodando em JS ou o redirecionamento
disparado pelo `setInterval`. O que este teste verifica é que o HTML renderizado
carrega os dados corretos (horário configurado, minutos de aviso, elementos do
banner, URL de logout) que a lógica client-side usa - e, do lado do servidor
(o backup de segurança pedido), o comportamento real de cancelamento/logout
forçado, testável de ponta a ponta via requisições HTTP normais.

Execução: python -m tests.test_regressao_encerramento_automatico (a partir da
raiz do projeto, com o virtualenv ativado).
"""
import os

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app import create_app
from app.extensions import db
from app.models import (
    Empresa,
    Fila,
    Local,
    LogAtendimento,
    Perfil,
    Senha,
    Totem,
    Tv,
    Unidade,
    Usuario,
)

UTC = ZoneInfo("UTC")


def servidor_em_utc(ano, mes, dia, hora, minuto):
    """Mesmo padrão de tests/test_regressao_fuso_horario.py: finge que o relógio
    do servidor está neste instante UTC (patch de app.utils.tempo._instante_utc,
    que é o seam usado pelo middleware via agora_na_clinica())."""
    return patch("app.utils.tempo._instante_utc", return_value=datetime(ano, mes, dia, hora, minuto, tzinfo=UTC))

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

    totem = Totem(empresa_id=empresa.id, unidade_id=unidade.id, nome="Totem 1", slug="totem-encerramento")
    tv = Tv(empresa_id=empresa.id, unidade_id=unidade.id, nome="TV 1", slug="tv-encerramento")
    db.session.add_all([totem, tv])
    db.session.commit()

    def novo_usuario(login, perfis):
        u = Usuario(empresa_id=empresa.id, nome=login, login=login)
        u.set_senha("senha123")
        u.perfis = perfis
        u.unidades = [unidade]
        db.session.add(u)
        db.session.commit()
        return u.id

    id_recepcao = novo_usuario("recepcao_encerramento", [perfil_recepcao])
    id_admin = novo_usuario("admin_encerramento", [perfil_admin])

    ids = dict(
        empresa=empresa.id, unidade=unidade.id, fila=fila.id, local=local.id,
        totem_slug=totem.slug, tv_slug=tv.slug,
        recepcao=id_recepcao, admin=id_admin,
    )


def nova_senha(codigo, status, operador_id=None):
    with app.app_context():
        s = Senha(
            empresa_id=ids["empresa"], unidade_id=ids["unidade"], fila_id=ids["fila"],
            codigo=codigo, sequencial=1, prioridade=False, status=status,
            local_id=ids["local"] if status != "aguardando" else None,
            operador_id=operador_id, setor="recepcao" if status != "aguardando" else None,
        )
        db.session.add(s)
        db.session.commit()
        return s.id


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


URL_FILA = f"/atendimento/recepcao/filas/{ids['fila']}"

# ===========================================================================
# A) Antes do horário: HTML da tela de atendimento carrega os dados corretos
#    pro JS montar a contagem regressiva - horário/minutos configurados,
#    elementos do banner, URL de logout embutida.
# ===========================================================================
c = login_como("recepcao_encerramento")
resp = c.get(URL_FILA)
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
assert 'id="aviso-encerramento"' in texto and 'hidden' in texto
assert 'id="contagem-encerramento"' in texto
horario_embutido = f'"{app.config["HORARIO_ENCERRAMENTO_SESSOES"]}".split(":")'
assert horario_embutido in texto
ok("Tela de atendimento carrega o banner/elementos de encerramento, com o horário configurado embutido")

assert str(app.config["AVISO_ENCERRAMENTO_MINUTOS"]) in texto
assert '"/logout"' in texto
ok("Minutos de aviso e URL de logout corretos embutidos no script")

# Bug corrigido: '.aviso-encerramento { display: flex; ... }' tinha a MESMA
# especificidade do '[hidden] { display: none; }' nativo do navegador - como
# vinha depois no CSS, sempre vencia, e o card aparecia sempre visível (com o
# placeholder "--:--"), mesmo longe do horário de encerramento. Precisa de uma
# regra própria combinando classe + [hidden] pra vencer o `display: flex` de
# verdade, com especificidade maior.
assert ".aviso-encerramento[hidden]" in texto and "display: none" in texto
indice_regra = texto.index(".aviso-encerramento[hidden]")
assert "display: none" in texto[indice_regra:indice_regra + 60]
ok("CSS tem uma regra própria pra '[hidden]' vencer o 'display: flex' da classe (bug do card sempre visível corrigido)")

# nenhum redirecionamento/cancelamento ainda - horário de fechamento configurado
# bem no futuro do dia (qualquer coisa antes de 23:59 quase sempre é 'ainda não').
app.config["HORARIO_ENCERRAMENTO_SESSOES"] = "23:59"
resp = c.get(URL_FILA, follow_redirects=False)
assert resp.status_code == 200
ok("Antes do horário de encerramento: tela abre normalmente, sem redirecionar")

# ===========================================================================
# B) Senhas em andamento (a serem canceladas quando o horário passar).
# ===========================================================================
id_chamada = nova_senha("FA001", "chamada", operador_id=ids["recepcao"])
id_em_atendimento = nova_senha("FA002", "em_atendimento", operador_id=ids["recepcao"])
id_finalizada = nova_senha("FA003", "finalizada")
id_aguardando = nova_senha("FA004", "aguardando")

# ===========================================================================
# C) Horário de encerramento "passado" (00:00 - sempre já ultrapassado, já que
#    qualquer hora do dia é >= 00:00) - backend cancela as senhas abertas E
#    derruba a sessão de quem está numa tela de Atendimento.
# ===========================================================================
# Religa o gate (desligado por padrão em TestingConfig p/ hermeticidade da suíte
# inteira) - aqui é EXATAMENTE o ponto onde queremos o middleware ativo, para
# exercitar o encerramento de propósito.
app.config["ENCERRAMENTO_SESSOES_ATIVO"] = True
app.config["HORARIO_ENCERRAMENTO_SESSOES"] = "00:00"

resp = c.get(URL_FILA, follow_redirects=False)
assert resp.status_code == 302
assert resp.headers["Location"].startswith("/login")
ok("No/após o horário de encerramento: tela de atendimento redireciona pro login (302)")

# a sessão foi mesmo encerrada - uma nova requisição não está mais autenticada.
resp2 = c.get("/atendimento/local", follow_redirects=False)
assert resp2.status_code == 302
assert resp2.headers["Location"].startswith("/login") or "next=" in resp2.headers["Location"]
ok("Sessão do operador foi realmente encerrada (próxima requisição não está mais autenticada)")

with app.app_context():
    assert db.session.get(Senha, id_chamada).status == "cancelada"
    assert db.session.get(Senha, id_em_atendimento).status == "cancelada"
    assert db.session.get(Senha, id_finalizada).status == "finalizada"  # não mexeu
    assert db.session.get(Senha, id_aguardando).status == "aguardando"  # não mexeu
ok("Senhas 'chamada'/'em_atendimento' foram canceladas; 'finalizada'/'aguardando' não foram tocadas")

with app.app_context():
    logs_chamada = LogAtendimento.query.filter_by(
        senha_id=id_chamada, evento="encerramento_automatico_sistema"
    ).all()
    logs_em_atendimento = LogAtendimento.query.filter_by(
        senha_id=id_em_atendimento, evento="encerramento_automatico_sistema"
    ).all()
    assert len(logs_chamada) == 1
    assert len(logs_em_atendimento) == 1
    assert logs_chamada[0].usuario_id is None  # ação do sistema, não de um operador
ok("Log de auditoria 'encerramento_automatico_sistema' gravado para cada senha cancelada")

# ===========================================================================
# D) Idempotência: rodar de novo (nova requisição, ainda 'fechado') não duplica
#    logs nem quebra - não sobrou nenhuma senha aberta pra cancelar.
# ===========================================================================
c_admin_idem = login_como("admin_encerramento")
resp = c_admin_idem.get("/admin/unidades", follow_redirects=False)
assert resp.status_code == 200  # admin não é afetado (ver seção F) - só confirma que não quebra
with app.app_context():
    total_logs = LogAtendimento.query.filter_by(
        senha_id=id_chamada, evento="encerramento_automatico_sistema"
    ).count()
    assert total_logs == 1  # não duplicou
ok("Rodar o encerramento de novo (nada mais pra cancelar) é idempotente - não duplica log")

# ===========================================================================
# E) Totem e TV continuam funcionando normalmente, mesmo com o horário
#    "fechado" - são telas públicas, sem sessão de operador.
# ===========================================================================
resp = app.test_client().get(f"/totem/{ids['totem_slug']}", follow_redirects=False)
assert resp.status_code == 200
ok("Totem continua respondendo normalmente (200) mesmo depois do horário de encerramento")

resp = app.test_client().get(f"/painel/tv/{ids['tv_slug']}", follow_redirects=False)
assert resp.status_code == 200
ok("Painel de TV continua respondendo normalmente (200) mesmo depois do horário de encerramento")

# ===========================================================================
# F) Admin não é derrubado - encerramento automático é só das telas de
#    Atendimento (item 4 do pedido), configurar o sistema não é "atendimento".
# ===========================================================================
resp = c_admin_idem.get("/admin/unidades", follow_redirects=False)
assert resp.status_code == 200
ok("Admin continua logado e acessando o painel administrativo, mesmo após o horário de encerramento")

# ===========================================================================
# G) Multi-tenant: o encerramento roda para TODAS as empresas de uma vez numa
#    única passada - não é uma ação disparada por-tenant. Uma requisição
#    (de qualquer empresa) que chega depois do horário deve cancelar senhas
#    abertas de uma empresa B, mesmo sem ninguém de B ter feito requisição
#    nenhuma (cancelar_senhas_em_andamento() não filtra por empresa_id).
# ===========================================================================
with app.app_context():
    empresa_b = Empresa(nome="Clinica B")
    db.session.add(empresa_b)
    db.session.commit()

    unidade_b = Unidade(empresa_id=empresa_b.id, nome="Unidade B1")
    db.session.add(unidade_b)
    db.session.commit()

    fila_b = Fila(empresa_id=empresa_b.id, unidade_id=unidade_b.id, nome="Fila B", sigla="FB")
    db.session.add(fila_b)
    db.session.commit()

    senha_b = Senha(
        empresa_id=empresa_b.id, unidade_id=unidade_b.id, fila_id=fila_b.id,
        codigo="FB001", sequencial=1, prioridade=False, status="chamada",
    )
    db.session.add(senha_b)
    db.session.commit()
    id_senha_b = senha_b.id

# nenhum usuário de B fez login/requisição - o gatilho é a próxima requisição
# de QUALQUER tenant (aqui, o admin da empresa A, já logado) depois do horário.
resp = c_admin_idem.get("/admin/unidades", follow_redirects=False)
assert resp.status_code == 200
with app.app_context():
    assert db.session.get(Senha, id_senha_b).status == "cancelada"
ok("Encerramento roda para TODAS as empresas numa única passada - senha 'chamada' da empresa B é cancelada por uma requisição da empresa A")

# ===========================================================================
# H) Fuso da clínica (BLUEPRINT 10.37) DENTRO deste arquivo de encerramento -
#    servidor simulado em UTC, horário configurado no valor PADRÃO real
#    ("23:00", não o "00:00" artificial usado nos blocos C-G). 23:30 Brasília
#    (02:30 UTC) dispara; 22:30 Brasília (01:30 UTC) não. Mesmo cenário já
#    coberto em tests/test_regressao_fuso_horario.py - repetido aqui porque
#    este é o arquivo "dono" da funcionalidade de encerramento.
# ===========================================================================
app.config["HORARIO_ENCERRAMENTO_SESSOES"] = "23:00"

with app.app_context():
    perfil_fuso = Perfil.query.filter_by(nome="atendente").first()
    u_fuso = Usuario(empresa_id=empresa.id, nome="op_fuso_encerramento", login="op_fuso_encerramento")
    u_fuso.set_senha("senha123")
    u_fuso.perfis = [perfil_fuso]
    u_fuso.unidades = [unidade]
    db.session.add(u_fuso)
    db.session.commit()

    senha_h1 = Senha(
        empresa_id=ids["empresa"], unidade_id=ids["unidade"], fila_id=ids["fila"],
        codigo="FA010", sequencial=10, prioridade=False, status="chamada",
        local_id=ids["local"], operador_id=u_fuso.id, setor="recepcao",
    )
    db.session.add(senha_h1)
    db.session.commit()
    id_senha_h1 = senha_h1.id

c_fuso = login_como("op_fuso_encerramento")

with servidor_em_utc(2026, 6, 15, 1, 30):  # 22:30 Brasília - ANTES das 23:00
    resp = c_fuso.get(URL_FILA, follow_redirects=False)
assert resp.status_code == 200
with app.app_context():
    assert db.session.get(Senha, id_senha_h1).status == "chamada"
ok("Fuso (dentro deste arquivo): 22:30 Brasília (servidor 01:30 UTC) NÃO encerra - tela abre normal")

with servidor_em_utc(2026, 6, 15, 2, 30):  # 23:30 Brasília - APÓS as 23:00
    resp = c_fuso.get(URL_FILA, follow_redirects=False)
assert resp.status_code == 302 and resp.headers["Location"].startswith("/login")
with app.app_context():
    assert db.session.get(Senha, id_senha_h1).status == "cancelada"
ok("Fuso (dentro deste arquivo): 23:30 Brasília (servidor 02:30 UTC) ENCERRA - redireciona e cancela a senha, sem os 3h de erro do fuso do SO")

print()
print(f"TODOS OS {len(passos)} TESTES DE ENCERRAMENTO AUTOMATICO DE SESSOES PASSARAM")
