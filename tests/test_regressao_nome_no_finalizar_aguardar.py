"""
Teste de regressão (adendo 10.82): o Nome preenchido no Finalizar/Aguardar
gravava um log 'chamada_nome' na MESMA request do 'fim_atendimento'/
'voltou_aguardar'. `_pares_inicio_fim` pega a chamada MAIS RECENTE como
início do último atendimento, então media esse segmento como ~0 s (no banco
local, 4 de 4 senhas com o nome no Finalizar saíam 0,0 s - ex.: 18:27:03 ->
18:32:09, 5:06 reais). Mesma causa raiz já corrigida no Realocar (10.62 /
test_regressao_realocar_nao_conta_como_chamada.py).

Correção: Finalizar e Aguardar (telas por fila e por Local) passam
`registrar_como_chamada=False` para talvez_atualizar_nome() - nome_paciente/
chamada_nome_em continuam gravados na senha; só o log deixa de existir.

Cobre, via requests HTTP reais (fluxo de produção, não logs forjados):
1. Finalizar com Nome (tela por fila): sem log 'chamada_nome', nome gravado,
   último atendimento medido do chamada_senha real (300 s, não 0 s) em
   _pares_inicio_fim, tempo_atendimento, ranking (tempo e volume = 1) e
   tempos_por_senha.
2. Aguardar com Nome (tela por fila): sem log 'chamada_nome', nome gravado.
3. Finalizar e Aguardar com Nome na tela por Local: idem.
4. Feed "últimas chamadas" do dashboard: 1 chamada por visita (antes eram 2).

Execução: python -m tests.test_regressao_nome_no_finalizar_aguardar (a partir
da raiz do projeto, com o virtualenv ativado).
"""
import os
from datetime import datetime, timedelta

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

from app import create_app
from app.extensions import db
from app.models import Empresa, Fila, Local, LocalRodizioFila, LogAtendimento, Perfil, Senha, Unidade, Usuario
from app.relatorios.helpers import Filtros
from app.relatorios.queries import (
    _pares_inicio_fim, ranking_operadores, tempo_atendimento, tempos_por_senha, ultimas_chamadas,
)

app = create_app("testing")

passos = []


def ok(msg):
    passos.append(msg)
    print("OK:", msg)


with app.app_context():
    db.create_all()

    perfil = Perfil(nome="executor")
    db.session.add(perfil)
    db.session.commit()

    empresa = Empresa(nome="Clinica A")
    db.session.add(empresa)
    db.session.commit()

    unidade = Unidade(empresa_id=empresa.id, nome="Unidade A1")
    db.session.add(unidade)
    db.session.commit()

    fila = Fila(empresa_id=empresa.id, unidade_id=unidade.id, nome="Fila Coleta", sigla="CL")
    db.session.add(fila)
    db.session.commit()

    local_fila = Local(
        empresa_id=empresa.id, unidade_id=unidade.id, nome="Coleta 01", tipo="coleta",
        nome_exibicao="Coleta 01", identificador_admin="[UNI] Coleta 01",
    )
    local_rodizio = Local(
        empresa_id=empresa.id, unidade_id=unidade.id, nome="Coleta 02", tipo="coleta",
        nome_exibicao="Coleta 02", identificador_admin="[UNI] Coleta 02",
    )
    db.session.add_all([local_fila, local_rodizio])
    db.session.commit()
    db.session.add(LocalRodizioFila(local_id=local_rodizio.id, fila_id=fila.id, ordem=0))

    senhas = {}
    for seq, codigo in enumerate(("CL001", "CL002", "CL003", "CL004"), start=1):
        s = Senha(
            empresa_id=empresa.id, unidade_id=unidade.id, fila_id=fila.id,
            codigo=codigo, sequencial=seq, prioridade=False, status="aguardando",
        )
        db.session.add(s)
        db.session.commit()
        db.session.add(LogAtendimento(senha_id=s.id, evento="gerada", unidade_id=unidade.id, fila_id=fila.id))
        senhas[codigo] = s.id
    db.session.commit()

    operador = Usuario(empresa_id=empresa.id, nome="coleta_nome", login="coleta_nome")
    operador.set_senha("senha123")
    operador.perfis = [perfil]
    operador.unidades = [unidade]
    db.session.add(operador)
    db.session.commit()

    ids = dict(empresa=empresa.id, unidade=unidade.id, fila=fila.id, local_fila=local_fila.id,
               local_rodizio=local_rodizio.id, operador=operador.id)


def cliente_no_local(local_id):
    c = app.test_client()
    resp = c.post("/login", data={"login": "coleta_nome", "senha": "senha123"}, follow_redirects=True)
    assert resp.status_code == 200
    with c.session_transaction() as sess:
        sess["atendimento_local_id"] = local_id
    return c


def eventos(codigo):
    with app.app_context():
        return [l.evento for l in LogAtendimento.query.filter_by(senha_id=senhas[codigo]).order_by(LogAtendimento.id)]


def nome_gravado(codigo):
    with app.app_context():
        s = db.session.get(Senha, senhas[codigo])
        return s.nome_paciente, s.chamada_nome_em


def _filtros():
    return Filtros(
        empresa_id=ids["empresa"], unidades_permitidas=None, unidade_id=None,
        fila_id=None, operador_id=None,
        data_inicio=datetime.utcnow() - timedelta(days=1),
        data_fim_exclusivo=datetime.utcnow() + timedelta(days=1),
        data_inicio_str="", data_fim_str="",
    )


c_fila = cliente_no_local(ids["local_fila"])
base_fila = f"/atendimento/coleta/filas/{ids['fila']}"

# 1) Finalizar com Nome, tela por fila
assert c_fila.post(f"{base_fila}/chamar", data={"codigo": "CL001"}, follow_redirects=True).status_code == 200
assert c_fila.post(f"{base_fila}/finalizar", data={"nome": "Maria Coleta"}, follow_redirects=True).status_code == 200
assert eventos("CL001") == ["gerada", "chamada_senha", "fim_atendimento"], eventos("CL001")
nome, nome_em = nome_gravado("CL001")
assert nome == "Maria Coleta" and nome_em is not None
ok("Finalizar com Nome (por fila): sem log 'chamada_nome'; nome_paciente/chamada_nome_em gravados na senha")

# Timestamps determinísticos: gerada 0 s, chamada 60 s, fim 360 s -> atendimento = 300 s.
with app.app_context():
    t0 = datetime.utcnow() - timedelta(hours=1)
    for log, offset in zip(LogAtendimento.query.filter_by(senha_id=senhas["CL001"]).order_by(LogAtendimento.id), (0, 60, 360)):
        log.timestamp = t0 + timedelta(seconds=offset)
    db.session.commit()

with app.app_context():
    pares = [p for p in _pares_inicio_fim(_filtros()) if p[0].senha_id == senhas["CL001"]]
    assert len(pares) == 1 and pares[0][0].evento == "chamada_senha"
    assert (pares[0][1].timestamp - pares[0][0].timestamp).total_seconds() == 300.0
    linha = next(l for l in tempo_atendimento(_filtros()) if l["operador_id"] == ids["operador"])
    assert linha["tempo_medio_segundos"] == 300.0 and linha["quantidade"] == 1, linha
    ranking = next(r for r in ranking_operadores(_filtros()) if r["operador_id"] == ids["operador"])
    assert ranking["volume"] == 1 and ranking["tempo_medio_segundos"] == 300.0, ranking
    por_senha, _ = tempos_por_senha(_filtros())
    cl001 = next(l for l in por_senha if l["codigo"] == "CL001")
    assert cl001["atendimento_total_segundos"] == 300.0, cl001
ok("Último atendimento = 300 s (não 0 s) em _pares_inicio_fim, tempo_atendimento, ranking (volume 1) e tempos_por_senha")

# 2) Aguardar com Nome, tela por fila
assert c_fila.post(f"{base_fila}/chamar", data={"codigo": "CL002"}, follow_redirects=True).status_code == 200
assert c_fila.post(f"{base_fila}/aguardar", data={"nome": "Joao Espera"}, follow_redirects=True).status_code == 200
assert eventos("CL002") == ["gerada", "chamada_senha", "voltou_aguardar"], eventos("CL002")
assert nome_gravado("CL002")[0] == "Joao Espera"
ok("Aguardar com Nome (por fila): sem log 'chamada_nome'; nome_paciente gravado")

# 3) Tela por Local (rodízio): Finalizar e Aguardar com Nome
c_local = cliente_no_local(ids["local_rodizio"])
base_local = f"/atendimento/coleta/local/{ids['local_rodizio']}"
assert c_local.post(f"{base_local}/chamar", data={"codigo": "CL003"}, follow_redirects=True).status_code == 200
assert c_local.post(f"{base_local}/finalizar", data={"nome": "Ana Local"}, follow_redirects=True).status_code == 200
assert eventos("CL003") == ["gerada", "chamada_senha", "fim_atendimento"], eventos("CL003")
assert nome_gravado("CL003")[0] == "Ana Local"
assert c_local.post(f"{base_local}/chamar", data={"codigo": "CL004"}, follow_redirects=True).status_code == 200
assert c_local.post(f"{base_local}/aguardar", data={"nome": "Rui Local"}, follow_redirects=True).status_code == 200
assert eventos("CL004") == ["gerada", "chamada_senha", "voltou_aguardar"], eventos("CL004")
assert nome_gravado("CL004")[0] == "Rui Local"
ok("Tela por Local: Finalizar e Aguardar com Nome também não gravam 'chamada_nome'")

# 4) Feed "últimas chamadas": uma linha por chamada real (4 visitas = 4 chamadas)
with app.app_context():
    feed = ultimas_chamadas(db.session.get(Empresa, ids["empresa"]), [ids["unidade"]])
    assert len(feed) == 4, feed
    assert LogAtendimento.query.filter_by(evento="chamada_nome").count() == 0
ok("Feed 'últimas chamadas': 4 visitas = 4 chamadas (antes, 2 a mais pelo Nome no Finalizar)")

print()
print(f"TODOS OS {len(passos)} TESTES DE 'NOME NO FINALIZAR/AGUARDAR' PASSARAM")
