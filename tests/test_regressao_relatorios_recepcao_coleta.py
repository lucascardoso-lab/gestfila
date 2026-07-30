"""
Teste de regressão: métrica de tempo do PRIMEIRO segmento de atendimento (ex.:
Recepção antes de realocar para a Coleta, seção 5.3/adendo 10.2) no relatório
"tempo de atendimento" (seção 7 do BLUEPRINT) - _pares_inicio_realocado() e sua
integração em tempo_atendimento(), sem alterar _pares_inicio_fim()/o segmento já
existente (última chamada -> fim_atendimento).

Cobre os 3 cenários combinados antes de implementar:
1. Senha só-recepção (nunca realocada): NENHUMA linha nova - só a de sempre.
2. Senha Recepção -> Coleta: exatamente 2 linhas (uma por segmento), sem overlap
   de eventos entre elas (nenhum log usado nos dois pareamentos).
3. Senha com múltiplos realocados (Recepção -> Fila A -> Fila B/Coleta): só o
   PRIMEIRO segmento (chamada inicial -> 1º realocado) e o ÚLTIMO (última chamada
   -> fim_atendimento) são medidos - o segmento intermediário (Fila A) não gera
   nenhuma linha, por design (escopo assumido, registrado no BLUEPRINT).

Constrói os logs_atendimento diretamente via ORM (não via rotas HTTP) e chama
tempo_atendimento()/Filtros diretamente - mesmo estilo dos demais testes de
consulta pura deste módulo (sem precisar de contexto de request/login).

Execução: python -m tests.test_regressao_relatorios_recepcao_coleta (a partir da
raiz do projeto, com o virtualenv ativado).
"""
import os
from datetime import datetime, timedelta

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

from app import create_app
from app.extensions import db
from app.models import Empresa, Fila, Local, LogAtendimento, Senha, Unidade, Usuario
from app.relatorios.helpers import Filtros
from app.relatorios.queries import tempo_atendimento

app = create_app("testing")

passos = []


def ok(msg):
    passos.append(msg)
    print("OK:", msg)


with app.app_context():
    db.create_all()

    empresa = Empresa(nome="Clinica A")
    db.session.add(empresa)
    db.session.commit()

    unidade = Unidade(empresa_id=empresa.id, nome="Unidade A1")
    db.session.add(unidade)
    db.session.commit()

    fila_recepcao = Fila(empresa_id=empresa.id, unidade_id=unidade.id, nome="Recepção Geral", sigla="RG")
    fila_a = Fila(empresa_id=empresa.id, unidade_id=unidade.id, nome="Fila A", sigla="FA")
    fila_coleta = Fila(empresa_id=empresa.id, unidade_id=unidade.id, nome="Coleta", sigla="CO")
    db.session.add_all([fila_recepcao, fila_a, fila_coleta])
    db.session.commit()

    local_coleta = Local(
        empresa_id=empresa.id, unidade_id=unidade.id, nome="Guichê Coleta 1",
        nome_exibicao="Guichê Coleta 1", identificador_admin="[UNI] Guichê Coleta 1",
    )
    db.session.add(local_coleta)
    db.session.commit()

    def novo_usuario(login):
        u = Usuario(empresa_id=empresa.id, nome=login, login=login)
        u.set_senha("senha123")
        u.unidades = [unidade]
        db.session.add(u)
        db.session.commit()
        return u.id

    id_op_s1 = novo_usuario("op_s1_recepcao")
    id_op_s2_recepcao = novo_usuario("op_s2_recepcao")
    id_op_s2_coleta = novo_usuario("op_s2_coleta")
    id_op_s3_recepcao = novo_usuario("op_s3_recepcao")
    id_op_s3_fila_a = novo_usuario("op_s3_fila_a")
    id_op_s3_coleta = novo_usuario("op_s3_coleta")

    t0 = datetime.utcnow() - timedelta(hours=2)

    def nova_senha(fila_id, codigo, local_id=None):
        s = Senha(
            empresa_id=empresa.id, unidade_id=unidade.id, fila_id=fila_id,
            codigo=codigo, sequencial=1, prioridade=False, status="finalizada", local_id=local_id,
        )
        db.session.add(s)
        db.session.commit()
        return s.id

    def log(senha_id, evento, usuario_id, setor, timestamp):
        db.session.add(
            LogAtendimento(
                senha_id=senha_id, evento=evento, usuario_id=usuario_id,
                unidade_id=unidade.id, setor=setor, timestamp=timestamp,
            )
        )

    # -------------------------------------------------------------------
    # Senha 1: só-recepção, nunca realocada. chamada_senha -> fim_atendimento.
    # -------------------------------------------------------------------
    id_s1 = nova_senha(fila_recepcao.id, "S1001")
    log(id_s1, "chamada_senha", id_op_s1, "recepcao", t0)
    log(id_s1, "fim_atendimento", id_op_s1, "recepcao", t0 + timedelta(seconds=300))

    # -------------------------------------------------------------------
    # Senha 2: Recepção -> Coleta. chamada(recepcao) -> realocado -> chamada(coleta)
    # -> fim_atendimento. Estado atual (pós-realocação): fila_coleta/local_coleta.
    # -------------------------------------------------------------------
    id_s2 = nova_senha(fila_coleta.id, "S2001", local_id=local_coleta.id)
    log(id_s2, "chamada_senha", id_op_s2_recepcao, "recepcao", t0)
    log(id_s2, "realocado", id_op_s2_recepcao, "recepcao", t0 + timedelta(seconds=120))  # segmento 1: 120s
    log(id_s2, "chamada_senha", id_op_s2_coleta, "coleta", t0 + timedelta(seconds=200))
    log(id_s2, "fim_atendimento", id_op_s2_coleta, "coleta", t0 + timedelta(seconds=380))  # segmento 2: 180s

    # -------------------------------------------------------------------
    # Senha 3: Recepção -> Fila A -> Coleta (2 realocados). Só o 1º segmento
    # (Recepção, 50s) e o ÚLTIMO (Coleta, 60s) devem ser medidos - o segmento da
    # Fila A (70s) fica de fora, por design.
    # -------------------------------------------------------------------
    id_s3 = nova_senha(fila_coleta.id, "S3001", local_id=local_coleta.id)
    log(id_s3, "chamada_senha", id_op_s3_recepcao, "recepcao", t0)
    log(id_s3, "realocado", id_op_s3_recepcao, "recepcao", t0 + timedelta(seconds=50))  # 1º realocado: segmento 50s
    log(id_s3, "chamada_senha", id_op_s3_fila_a, "coleta", t0 + timedelta(seconds=80))
    log(id_s3, "realocado", id_op_s3_fila_a, "coleta", t0 + timedelta(seconds=150))  # 2º realocado: segmento 70s (não medido)
    log(id_s3, "chamada_senha", id_op_s3_coleta, "coleta", t0 + timedelta(seconds=200))
    log(id_s3, "fim_atendimento", id_op_s3_coleta, "coleta", t0 + timedelta(seconds=260))  # último segmento: 60s

    db.session.commit()

    ids = dict(
        empresa=empresa.id, unidade=unidade.id,
        fila_recepcao=fila_recepcao.id, fila_a=fila_a.id, fila_coleta=fila_coleta.id, local_coleta=local_coleta.id,
        op_s1=id_op_s1, op_s2_recepcao=id_op_s2_recepcao, op_s2_coleta=id_op_s2_coleta,
        op_s3_recepcao=id_op_s3_recepcao, op_s3_fila_a=id_op_s3_fila_a, op_s3_coleta=id_op_s3_coleta,
    )

print("IDS", ids)


def linhas_relatorio():
    with app.app_context():
        filtros = Filtros(
            empresa_id=ids["empresa"], unidades_permitidas=None, unidade_id=None,
            fila_id=None, operador_id=None,
            data_inicio=datetime.utcnow() - timedelta(days=1),
            data_fim_exclusivo=datetime.utcnow() + timedelta(days=1),
            data_inicio_str="", data_fim_str="",
        )
        return tempo_atendimento(filtros)


def linha_de(linhas, operador_id):
    encontradas = [l for l in linhas if l["operador_id"] == operador_id]
    assert len(encontradas) == 1, (operador_id, encontradas)
    return encontradas[0]


linhas = linhas_relatorio()

# ---------------------------------------------------------------------------
# 1) Senha só-recepção: nenhuma linha nova gerada pelo segmento de realocação -
#    só a linha de sempre (chamada -> fim_atendimento), com fila/local reais.
# ---------------------------------------------------------------------------
linha_s1 = linha_de(linhas, ids["op_s1"])
assert linha_s1["setor"] == "recepcao"
assert linha_s1["fila_id"] == ids["fila_recepcao"]
assert linha_s1["quantidade"] == 1
assert linha_s1["tempo_medio_segundos"] == 300.0
ok("Senha só-recepção (nunca realocada): 1 única linha (fila_id real), tempo=300s - nenhuma linha extra")

# ---------------------------------------------------------------------------
# 2) Senha Recepção -> Coleta: exatamente 2 linhas, uma por segmento, sem
#    overlap (tempos diferentes, cada um correto para seu próprio segmento).
# ---------------------------------------------------------------------------
linha_s2_recepcao = linha_de(linhas, ids["op_s2_recepcao"])
assert linha_s2_recepcao["setor"] == "recepcao"
assert linha_s2_recepcao["fila_id"] is None  # sem fila/local (Opção A, sem migration)
assert linha_s2_recepcao["local_id"] is None
assert linha_s2_recepcao["quantidade"] == 1
assert linha_s2_recepcao["tempo_medio_segundos"] == 120.0
ok("Recepção->Coleta: segmento da Recepção mede 120s (chamada->realocado), fila/local = '—' (None)")

linha_s2_coleta = linha_de(linhas, ids["op_s2_coleta"])
assert linha_s2_coleta["setor"] == "coleta"
assert linha_s2_coleta["fila_id"] == ids["fila_coleta"]
assert linha_s2_coleta["local_id"] == ids["local_coleta"]
assert linha_s2_coleta["quantidade"] == 1
assert linha_s2_coleta["tempo_medio_segundos"] == 180.0
ok("Recepção->Coleta: segmento da Coleta mede 180s (última chamada->fim), fila/local reais - sem overlap com a Recepção")

# ---------------------------------------------------------------------------
# 3) Senha com múltiplos realocados: só o 1º segmento (Recepção, 50s) e o
#    ÚLTIMO (Coleta, 60s) aparecem - o segmento intermediário (Fila A, 70s) NÃO
#    gera nenhuma linha em lugar nenhum.
# ---------------------------------------------------------------------------
linha_s3_recepcao = linha_de(linhas, ids["op_s3_recepcao"])
assert linha_s3_recepcao["setor"] == "recepcao"
assert linha_s3_recepcao["fila_id"] is None
assert linha_s3_recepcao["quantidade"] == 1
assert linha_s3_recepcao["tempo_medio_segundos"] == 50.0
ok("Múltiplos realocados: 1º segmento (Recepção) medido corretamente = 50s")

linha_s3_coleta = linha_de(linhas, ids["op_s3_coleta"])
assert linha_s3_coleta["setor"] == "coleta"
assert linha_s3_coleta["fila_id"] == ids["fila_coleta"]
assert linha_s3_coleta["quantidade"] == 1
assert linha_s3_coleta["tempo_medio_segundos"] == 60.0
ok("Múltiplos realocados: último segmento (Coleta, até fim_atendimento) medido corretamente = 60s")

linhas_fila_a = [l for l in linhas if l["operador_id"] == ids["op_s3_fila_a"]]
assert linhas_fila_a == []
ok("Múltiplos realocados: segmento intermediário (Fila A, 70s) NÃO gera nenhuma linha - escopo assumido (só 1º hand-off)")

print()
print(f"TODOS OS {len(passos)} TESTES DA MÉTRICA DE TEMPO RECEPÇÃO->COLETA (RELATÓRIOS) PASSARAM")
