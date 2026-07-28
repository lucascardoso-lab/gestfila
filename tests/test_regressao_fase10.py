"""
Teste de regressão end-to-end (Fase 10, adaptado para a tela unificada de
Atendimento da seção 10.2): geração de senha (totem) -> chamada -> realocação
(com nome) -> nova chamada na fila destino -> finalização, com verificação de
isolamento multi-tenant em CADA etapa (tentativa de atravessar para a empresa B
deve sempre falhar com 404, nunca vazar dado nem permitir mutação).

Execução: python -m tests.test_regressao_fase10 (a partir da raiz do projeto, com o
virtualenv do requirements.txt ativado - o "-m" garante que `import app` resolva sem
precisar setar PYTHONPATH manualmente). Não usa pytest - segue o mesmo estilo de
script com asserts usado nos testes manuais de todas as fases anteriores deste
projeto, para manter consistência.
"""
import os

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

from app import create_app
from app.extensions import db, socketio
from app.models import (
    Empresa,
    Fila,
    Local,
    LogAtendimento,
    Perfil,
    Senha,
    Totem,
    TotemBotao,
    Tv,
    Unidade,
    Usuario,
)

app = create_app("testing")

passos = []


def ok(msg):
    passos.append(msg)
    print("OK:", msg)


with app.app_context():
    db.create_all()

    perfil_recepcao = Perfil(nome="atendente")
    perfil_coleta = Perfil(nome="executor")
    perfil_admin = Perfil(nome="admin")
    db.session.add_all([perfil_recepcao, perfil_coleta, perfil_admin])
    db.session.commit()

    empresa_a = Empresa(nome="Clinica A")
    empresa_b = Empresa(nome="Clinica B")
    db.session.add_all([empresa_a, empresa_b])
    db.session.commit()

    unidade_a1 = Unidade(empresa_id=empresa_a.id, nome="Unidade A1")
    unidade_b1 = Unidade(empresa_id=empresa_b.id, nome="Unidade B1")
    db.session.add_all([unidade_a1, unidade_b1])
    db.session.commit()

    fila_a = Fila(empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="Fila Lab A", sigla="LB")
    fila_a2 = Fila(empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="Fila Coleta A", sigla="CL")
    fila_b = Fila(empresa_id=empresa_b.id, unidade_id=unidade_b1.id, nome="Fila Lab B", sigla="LB")
    db.session.add_all([fila_a, fila_a2, fila_b])
    db.session.commit()

    botao_a = TotemBotao(empresa_id=empresa_a.id, unidade_id=unidade_a1.id, fila_id=fila_a.id, label="Laboratorio")
    botao_b = TotemBotao(empresa_id=empresa_b.id, unidade_id=unidade_b1.id, fila_id=fila_b.id, label="Laboratorio B")
    db.session.add_all([botao_a, botao_b])
    db.session.commit()

    # seção 10.6: o totem físico agora é identificado na própria URL pública
    totem_a = Totem(empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="Totem A1", slug="totem-a1")
    db.session.add(totem_a)
    db.session.commit()

    tv_a = Tv(empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="TV A1", slug="tv-a1")
    tv_a.filas = [fila_a, fila_a2]
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

    id_recepcao_a = novo_usuario("recepcao_a", empresa_a.id, [perfil_recepcao], [unidade_a1])
    id_coleta_a = novo_usuario("coleta_a", empresa_a.id, [perfil_coleta], [unidade_a1])
    id_admin_b = novo_usuario("admin_b", empresa_b.id, [perfil_admin], [])

    # senha "aguardando" na empresa B, usada nas tentativas de violação de tenant
    senha_b = Senha(
        empresa_id=empresa_b.id, unidade_id=unidade_b1.id, fila_id=fila_b.id,
        codigo="LB901", sequencial=901, prioridade=False, status="aguardando",
    )
    db.session.add(senha_b)
    db.session.commit()

    # locais (seção 10.3) - cada posto físico é cadastrado, não digitado livremente
    local_guiche01 = Local(empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="Guiche 01")
    local_coleta01 = Local(empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="Coleta 01")
    db.session.add_all([local_guiche01, local_coleta01])
    db.session.commit()

    ids = dict(
        empresa_a=empresa_a.id, empresa_b=empresa_b.id, unidade_a1=unidade_a1.id, unidade_b1=unidade_b1.id,
        fila_a=fila_a.id, fila_a2=fila_a2.id, fila_b=fila_b.id, botao_a=botao_a.id, botao_b=botao_b.id,
        tv_a=tv_a.id, recepcao_a=id_recepcao_a, coleta_a=id_coleta_a, admin_b=id_admin_b, senha_b=senha_b.id,
        totem_a=totem_a.id,
        local_guiche01=local_guiche01.id, local_coleta01=local_coleta01.id,
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


NAMESPACE = "/painel"
ws = socketio.test_client(app, namespace=NAMESPACE)
ws.get_received(NAMESPACE)
ws.emit("entrar_tv", {"tv_id": ids["tv_a"]}, namespace=NAMESPACE)
ws.get_received(NAMESPACE)

c_recepcao = login_como("recepcao_a")
c_coleta = login_como("coleta_a")
c_admin_b = login_como("admin_b")

# seção 10.3: cada operador escolhe o Local do seu posto uma vez por turno (sessão)
with c_recepcao.session_transaction() as sess:
    sess["atendimento_local_id"] = ids["local_guiche01"]
with c_coleta.session_transaction() as sess:
    sess["atendimento_local_id"] = ids["local_coleta01"]

# ---------------------------------------------------------------------------
# Etapa 1: geração de senha via totem (pública, sem login).
# ---------------------------------------------------------------------------
c_totem = app.test_client()
resp = c_totem.post(f"/totem/{ids['unidade_a1']}/{ids['totem_a']}/gerar/{ids['botao_a']}", follow_redirects=True)
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
assert "LB001" in texto
ok("Etapa 1: totem gera a senha LB001 (status aguardando, log 'gerada')")

with app.app_context():
    senha_gerada = Senha.query.filter_by(codigo="LB001", empresa_id=ids["empresa_a"]).first()
    assert senha_gerada is not None
    assert senha_gerada.status == "aguardando"
    log_gerada = LogAtendimento.query.filter_by(senha_id=senha_gerada.id, evento="gerada").first()
    assert log_gerada is not None
    id_senha = senha_gerada.id

# isolamento: botão da empresa B não pode ser usado para gerar senha na unidade A1
# (a URL antiga por ID redireciona - 307 - antes de chegar na validação real do
# botão; seguir o redirect chega no 404 correto, igual a qualquer cliente real faria)
resp = c_totem.post(f"/totem/{ids['unidade_a1']}/{ids['totem_a']}/gerar/{ids['botao_b']}", follow_redirects=True)
assert resp.status_code == 404
ok("Isolamento (etapa 1): botão de outra empresa não gera senha via unidade A1 (404)")

# ---------------------------------------------------------------------------
# Etapa 2: tela unificada de Atendimento (seção 10.2) - botão Chamar.
# ---------------------------------------------------------------------------
resp = c_recepcao.get(f"/atendimento/recepcao/filas/{ids['fila_a']}")
assert resp.status_code == 200
assert "LB001" in resp.data.decode("utf-8")
ok("Etapa 2: recepção vê LB001 em 'Senhas em espera' na tela unificada")

# isolamento: recepcao_a (empresa A) não acessa fila da empresa B
resp = c_recepcao.get(f"/atendimento/recepcao/filas/{ids['fila_b']}")
assert resp.status_code == 404
ok("Isolamento (etapa 2): recepção da empresa A recebe 404 ao tentar acessar fila da empresa B")

resp = c_recepcao.post(f"/atendimento/recepcao/filas/{ids['fila_b']}/chamar", data={})
assert resp.status_code == 404
ok("Isolamento (etapa 2): POST 'chamar' em fila de outra empresa retorna 404")

resp = c_recepcao.post(
    f"/atendimento/recepcao/filas/{ids['fila_a']}/chamar",
    data={},
    follow_redirects=True,
)
assert resp.status_code == 200

senha_chamada = db_get(Senha, id_senha)
assert senha_chamada.status == "chamada"
assert senha_chamada.chamada_em is not None
assert senha_chamada.local_id == ids["local_guiche01"]
assert senha_chamada.operador_id == ids["recepcao_a"]
assert senha_chamada.setor == "recepcao"
ok("Etapa 2: 'Chamar' (próxima da fila) muda status para 'chamada', grava guichê/operador/setor")

with app.app_context():
    log_chamada = LogAtendimento.query.filter_by(senha_id=id_senha, evento="chamada_senha").first()
    assert log_chamada is not None and log_chamada.usuario_id == ids["recepcao_a"]
ok("Etapa 2: log 'chamada_senha' gravado corretamente")

recebidos = ws.get_received(NAMESPACE)
evento_chamada = next(ev for ev in recebidos if ev["name"] == "chamada")
assert evento_chamada["args"][0] == {"tipo": "senha", "local": "Guiche 01", "senha": "LB001"}
ok("Etapa 2: WebSocket emite 'chamada' na sala da fila com o payload correto")

# ---------------------------------------------------------------------------
# Etapa 3: Realocar (com Nome preenchido) - consolida a antiga "chamada por nome".
# ---------------------------------------------------------------------------
resp = c_admin_b.post(
    f"/atendimento/coleta/filas/{ids['fila_a']}/realocar",
    data={"fila_destino_id": ids["fila_a2"], "nome": "x"},
)
assert resp.status_code == 404
ok("Isolamento (etapa 3): admin de outra empresa recebe 404 ao tentar realocar em fila da empresa A")

resp = c_recepcao.post(
    f"/atendimento/recepcao/filas/{ids['fila_a']}/realocar",
    data={"fila_destino_id": ids["fila_a2"], "nome": "Maria Souza"},
    follow_redirects=True,
)
assert resp.status_code == 200

senha_realocada = db_get(Senha, id_senha)
assert senha_realocada.fila_id == ids["fila_a2"]
assert senha_realocada.status == "aguardando"
assert senha_realocada.chamada_em is None
assert senha_realocada.local_id is None
assert senha_realocada.nome_paciente == "Maria Souza"
ok("Etapa 3: 'Realocar' move a senha para a fila destino, volta para 'aguardando' e grava o Nome")

with app.app_context():
    log_realocado = LogAtendimento.query.filter_by(senha_id=id_senha, evento="realocado").first()
    assert log_realocado is not None and log_realocado.usuario_id == ids["recepcao_a"]
    # Mudança deliberada (não regressão): 'Realocar' passou a NÃO gravar mais o log
    # 'chamada_nome' quando o campo Nome é editado no próprio Realocar (nome_paciente
    # continua sendo gravado na senha, ver assert acima) - esse log ficava colado ao
    # 'realocado' (mesma request) e era escolhido por engano como "início" do segmento
    # de atendimento da Recepção em _pares_inicio_realocado (app/relatorios/queries.py),
    # medindo o tempo de atendimento da Recepção como ~0s em vez do valor real. Ver
    # BLUEPRINT.md, seção 7, para o efeito colateral (também deliberado) em
    # ranking_operadores()/ultimas_chamadas().
    log_nome = LogAtendimento.query.filter_by(senha_id=id_senha, evento="chamada_nome").first()
    assert log_nome is None
ok("Etapa 3: log 'realocado' gravado; 'chamada_nome' NÃO é mais gravado (mudança deliberada)")

# Mudança deliberada (não regressão): 'Realocar' passou a ser SILENCIOSO na TV de
# origem - antes emitia 'chamada' tipo 'nome' aqui (a TV da Recepção anunciava um
# paciente que estava de saída para a Coleta, antes mesmo de chegar lá). O nome
# continua sendo gravado na senha (nome_paciente, já conferido acima), só não
# dispara mais esse evento de TV - quem chama por nome agora é quem de fato
# atender o paciente na fila de destino.
recebidos = ws.get_received(NAMESPACE)
assert not any(ev["name"] == "chamada" for ev in recebidos)
assert any(ev["name"] == "fila_atualizada" and ev["args"][0]["fila_id"] == ids["fila_a"] for ev in recebidos)
ok("Etapa 3: 'Realocar' NÃO anuncia mais na TV de origem (silencioso, mudança deliberada) - só 'fila_atualizada'")

# ---------------------------------------------------------------------------
# Etapa 4: nova chamada na fila destino (coleta) + Tocar + Aguardar + Finalizar.
# ---------------------------------------------------------------------------
resp = c_coleta.post(
    f"/atendimento/coleta/filas/{ids['fila_a2']}/chamar",
    data={"codigo": senha_realocada.codigo},
    follow_redirects=True,
)
assert resp.status_code == 200
senha_rechamada = db_get(Senha, id_senha)
assert senha_rechamada.status == "chamada" and senha_rechamada.fila_id == ids["fila_a2"]
ok("Etapa 4: 'Chamar' por Código encontra a senha realocada na nova fila e a chama")

recebidos = ws.get_received(NAMESPACE)
evento_nome_ws = next(ev for ev in recebidos if ev["name"] == "chamada")
assert evento_nome_ws["args"][0] == {"tipo": "nome", "local": "Coleta 01", "nome_paciente": "Maria Souza"}
ok("Etapa 4: como a senha já tem Nome gravado, o WebSocket anuncia pelo nome, não pelo código")

resp = c_coleta.post(f"/atendimento/coleta/filas/{ids['fila_a2']}/tocar", data={})
assert resp.status_code == 302
ws.get_received(NAMESPACE)  # descarta o reforço do 'tocar'
senha_apos_tocar = db_get(Senha, id_senha)
assert senha_apos_tocar.chamada_em == senha_rechamada.chamada_em  # nada mudou
ok("Etapa 4: 'Tocar' repete a chamada sem alterar status/timestamp")

resp = c_coleta.post(f"/atendimento/coleta/filas/{ids['fila_a2']}/aguardar", data={})
assert resp.status_code == 302
senha_aguardando_de_novo = db_get(Senha, id_senha)
assert senha_aguardando_de_novo.status == "aguardando" and senha_aguardando_de_novo.local_id is None
with app.app_context():
    log_voltou = LogAtendimento.query.filter_by(senha_id=id_senha, evento="voltou_aguardar").first()
    assert log_voltou is not None
ok("Etapa 4: 'Aguardar' devolve a senha para a espera (mesma fila) e grava log 'voltou_aguardar'")

resp = c_coleta.post(
    f"/atendimento/coleta/filas/{ids['fila_a2']}/chamar",
    data={"codigo": senha_aguardando_de_novo.codigo},
)
assert resp.status_code == 302

resp = c_admin_b.post(f"/atendimento/coleta/filas/{ids['fila_a2']}/finalizar", data={})
assert resp.status_code == 404
ok("Isolamento (etapa 4): admin da empresa B recebe 404 ao tentar finalizar em fila da empresa A")

resp = c_coleta.post(
    f"/atendimento/coleta/filas/{ids['fila_a2']}/finalizar", data={}, follow_redirects=True
)
assert resp.status_code == 200

senha_final = db_get(Senha, id_senha)
assert senha_final.status == "finalizada"
assert senha_final.finalizada_em is not None
ok("Etapa 4: 'Finalizar' muda status para 'finalizada' e grava finalizada_em")

with app.app_context():
    log_fim = LogAtendimento.query.filter_by(senha_id=id_senha, evento="fim_atendimento").first()
    assert log_fim is not None and log_fim.usuario_id == ids["coleta_a"] and log_fim.setor == "coleta"
ok("Etapa 4: log 'fim_atendimento' gravado corretamente")

# tentar finalizar de novo (já finalizada, ninguém mais chamado neste local) é rejeitado
resp = c_coleta.post(
    f"/atendimento/coleta/filas/{ids['fila_a2']}/finalizar", data={}, follow_redirects=True
)
assert "Nenhuma senha chamada neste local".encode("utf-8") in resp.data
with app.app_context():
    total_logs_fim = LogAtendimento.query.filter_by(senha_id=id_senha, evento="fim_atendimento").count()
    assert total_logs_fim == 1
ok("Finalizar sem nenhuma senha chamada no local é rejeitado, sem duplicar o log 'fim_atendimento'")

# ---------------------------------------------------------------------------
# Etapa 5: o ciclo completo agora alimenta os relatórios da Fase 9 com dados reais.
# ---------------------------------------------------------------------------
with app.app_context():
    from datetime import datetime, timedelta

    from app.relatorios.helpers import Filtros
    from app.relatorios.queries import ranking_operadores, tempo_atendimento

    filtros = Filtros(
        empresa_id=ids["empresa_a"], unidades_permitidas=None, unidade_id=None, fila_id=None,
        operador_id=None, data_inicio=datetime.utcnow() - timedelta(days=1),
        data_fim_exclusivo=datetime.utcnow() + timedelta(days=1), data_inicio_str="x", data_fim_str="x",
    )
    linhas_tempo = tempo_atendimento(filtros)
    assert any(l["operador_id"] == ids["coleta_a"] for l in linhas_tempo)
    ok("Relatório 'tempo de atendimento' (Fase 9) agora retorna dados reais após a finalização")

    ranking = ranking_operadores(filtros)
    linha_coleta = next(l for l in ranking if l["operador_id"] == ids["coleta_a"])
    assert linha_coleta["tempo_medio_segundos"] is not None
    ok("Ranking de operadores (Fase 9) agora calcula tempo médio real para o operador da coleta")

print()
print(f"TODOS OS {len(passos)} TESTES DE REGRESSÃO END-TO-END DA FASE 10 PASSARAM")
