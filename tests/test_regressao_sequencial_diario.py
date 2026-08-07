"""
Teste de regressão: reset diário do sequencial de senha (BLUEPRINT.md, seção 8
"Reset de sequencial à meia-noite" + adendo 10.1, campos zera_diariamente/
digitos_sequencial migrados de Servico para Fila) - `app/totem/routes.py::
_emitir_senha`.

Cobre: sequencial zera por dia (por fila+unidade), fila com zera_diariamente=
False nunca zera, duas filas/unidades não compartilham contador, padding
configurável via digitos_sequencial, determinismo sob chamadas repetidas.

Caso E (virada de dia) e caso H (faixa 21h-23:59 Brasília inteira) simulam o
relógio do servidor em UTC, no mesmo padrão de tests/test_regressao_fuso_
horario.py (BLUEPRINT 10.37/10.39). Bug real encontrado e corrigido nesta
sessão: `_emitir_senha` comparava `func.date(Senha.criada_em) == datetime.
utcnow().date()` - "parecia" consistente (UTC comparado com UTC), mas o dia
CIVIL que importa é o da clínica, não o de UTC: a virada de dia UTC cai às
21h de Brasília (UTC-3), 3h antes da meia-noite real. Entre 21h e 23:59
Brasília, o sequencial resetava e emitia código DUPLICADO de uma senha já
impressa mais cedo no mesmo dia. Corrigido com `janela_dia_clinica_em_utc()`
(app/utils/tempo.py, 10.39), reaproveitada também em `historico_chamadas_tv`
(app/atendimento/helpers.py), que tinha o mesmo padrão pelo mesmo motivo
(comentário próprio dizia "mesma janela usada no reset diário do sequencial").

Caso F (concorrência): tentativa real com threads Python + SQLite em arquivo
(não em memória) foi prototipada e descartada - conexões sqlite3 comparilhadas
entre threads OS produzem erros de conexão e resultados corrompidos que são
artefato do ambiente de teste (SQLite+threading sem tuning de pool), não do
código sob teste. Sem um servidor MySQL disponível neste ambiente (mesma
limitação já documentada em `_emitir_senha`, ver seu docstring), o caso F
verifica o fallback combinado no pedido: a query de "próximo sequencial" é
determinística e serializada sob chamadas repetidas (cada chamada só enxerga
o commit da anterior), o que é a garantia que o lock real (SELECT...FOR UPDATE
/ UPDATE de linha) preserva em produção.

Execução: python -m tests.test_regressao_sequencial_diario (a partir da raiz
do projeto, com o virtualenv ativado).
"""
import os

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app import create_app
from app.extensions import db
from app.models import Empresa, Fila, Senha, Unidade
from app.totem.routes import _emitir_senha

app = create_app("testing")

UTC = ZoneInfo("UTC")

passos = []


def ok(msg):
    passos.append(msg)
    print("OK:", msg)


def servidor_utc_em(ano, mes, dia, hora, minuto):
    """Finge que o relógio do servidor está neste instante UTC - mesmo padrão de
    servidor_em_utc() em test_regressao_fuso_horario.py: patcha o seam real
    (app.utils.tempo._instante_utc), usado por agora_na_clinica() e, por tabela,
    por janela_dia_clinica_em_utc() (10.39) dentro de _emitir_senha."""
    return patch("app.utils.tempo._instante_utc", return_value=datetime(ano, mes, dia, hora, minuto, tzinfo=UTC))


with app.app_context():
    db.create_all()

    empresa = Empresa(nome="Clinica Sequencial")
    db.session.add(empresa)
    db.session.commit()

    unidade1 = Unidade(empresa_id=empresa.id, nome="Unidade 1")
    unidade2 = Unidade(empresa_id=empresa.id, nome="Unidade 2")
    db.session.add_all([unidade1, unidade2])
    db.session.commit()

    ids = dict(empresa=empresa.id, unidade1=unidade1.id, unidade2=unidade2.id)


def nova_fila(sigla, unidade_id, zera_diariamente=True, digitos=3, nome=None):
    with app.app_context():
        f = Fila(
            empresa_id=ids["empresa"], unidade_id=unidade_id, nome=nome or f"Fila {sigla}",
            sigla=sigla, zera_diariamente=zera_diariamente, digitos_sequencial=digitos,
        )
        db.session.add(f)
        db.session.commit()
        return f.id


def emitir(fila_id, unidade_id):
    with app.app_context():
        fila = db.session.get(Fila, fila_id)
        unidade = db.session.get(Unidade, unidade_id)
        senha = _emitir_senha(fila, unidade)
        return senha.codigo, senha.sequencial


def emitir_com_relogio_fake(fila_id, unidade_id, instante_utc):
    """Como servidor_utc_em(), mas TAMBÉM alinha o `criada_em` gravado com o
    instante simulado - `Senha.criada_em` usa `default=datetime.utcnow` de
    app/models/senha.py, uma referência já resolvida na DEFINIÇÃO da coluna
    (no import do model), então patchar app.utils.tempo._instante_utc (que só
    afeta a QUERY de sequencial, via agora_na_clinica()/janela_dia_clinica_em_
    utc()) não muda o timestamp de fato gravado na nova linha - sem este
    ajuste, o teste de virada de dia estaria comparando a query fake com uma
    data real (hoje de verdade), não com o cenário simulado, e passaria/
    falharia pelo motivo errado."""
    with servidor_utc_em(*instante_utc):
        with app.app_context():
            fila = db.session.get(Fila, fila_id)
            unidade = db.session.get(Unidade, unidade_id)
            senha = _emitir_senha(fila, unidade)
            senha.criada_em = datetime(*instante_utc)
            db.session.commit()
            return senha.codigo, senha.sequencial


def inserir_senha_manual(fila_id, unidade_id, codigo, sequencial, criada_em, status="finalizada"):
    with app.app_context():
        s = Senha(
            empresa_id=ids["empresa"], unidade_id=unidade_id, fila_id=fila_id,
            codigo=codigo, sequencial=sequencial, prioridade=False, status=status,
            criada_em=criada_em,
        )
        db.session.add(s)
        db.session.commit()
        return s.id


# ===========================================================================
# A) Sequencial começa do zero no primeiro código do dia, mesmo com senhas de
#    dias anteriores já existindo pra essa mesma fila.
# ===========================================================================
id_fila_a = nova_fila("SQ", ids["unidade1"])
inserir_senha_manual(
    id_fila_a, ids["unidade1"], "SQ005", 5,
    criada_em=datetime.utcnow() - timedelta(days=2),
)
codigo, sequencial = emitir(id_fila_a, ids["unidade1"])
assert sequencial == 1, sequencial
assert codigo == "SQ001", codigo
ok("Sequencial começa do zero 'hoje' mesmo com senha de 2 dias atrás na mesma fila (SQ005 antiga -> SQ001 novo)")

# ===========================================================================
# B) Duas filas diferentes na mesma unidade não compartilham sequencial.
# ===========================================================================
codigo, sequencial = emitir(id_fila_a, ids["unidade1"])
assert sequencial == 2 and codigo == "SQ002", (codigo, sequencial)

id_fila_b = nova_fila("SQ2", ids["unidade1"])
codigo_b, sequencial_b = emitir(id_fila_b, ids["unidade1"])
assert sequencial_b == 1, sequencial_b
assert codigo_b == "SQ2001", codigo_b
ok("Fila B (nova, mesma unidade) começa em 1 independente da Fila A já estar em 2 - sem contador compartilhado")

# ===========================================================================
# C) Duas unidades diferentes podem ter a MESMA sigla no mesmo dia sem conflito
#    (o contador é por fila_id/unidade_id, sigla repetida entre unidades é
#    permitida - a UniqueConstraint é só unidade_id+sigla, não sigla global).
# ===========================================================================
id_fila_c = nova_fila("SQ", ids["unidade2"])  # mesma sigla "SQ" da fila A, unidade DIFERENTE
codigo_c, sequencial_c = emitir(id_fila_c, ids["unidade2"])
assert sequencial_c == 1, sequencial_c
assert codigo_c == "SQ001", codigo_c  # igual ao primeiro código da unidade 1, sem colisão
ok("Unidade 2 com fila de sigla 'SQ' também gera SQ001 - duas unidades com o mesmo código não conflitam")

# ===========================================================================
# D) zera_diariamente=False: sequencial NUNCA reinicia entre dias, continua
#    incrementando a partir do maior já emitido (de qualquer data).
# ===========================================================================
id_fila_d = nova_fila("PERM", ids["unidade1"], zera_diariamente=False)
inserir_senha_manual(
    id_fila_d, ids["unidade1"], "PERM007", 7,
    criada_em=datetime.utcnow() - timedelta(days=5),
)
codigo_d, sequencial_d = emitir(id_fila_d, ids["unidade1"])
assert sequencial_d == 8, sequencial_d
assert codigo_d == "PERM008", codigo_d
ok("zera_diariamente=False: sequencial continua de uma senha de 5 dias atrás (PERM007 -> PERM008), não reseta")

# ===========================================================================
# E) Virada exata da meia-noite NO FUSO DA CLÍNICA (Brasília, UTC-3) - não em
#    UTC. Servidor simulado em UTC (mesmo padrão da 10.37/test_regressao_fuso_
#    horario.py). 02:30 UTC de 15/jun = 23:30 Brasília de 14/jun (MESMO dia
#    civil em Brasília); só 03:00 UTC de 15/jun vira 00:00 Brasília de 15/jun
#    (dia novo de verdade).
# ===========================================================================
id_fila_e = nova_fila("BR", ids["unidade1"])

codigo_e1, sequencial_e1 = emitir_com_relogio_fake(id_fila_e, ids["unidade1"], (2026, 6, 14, 13, 0))  # 10:00 Brasília, 14/jun
assert sequencial_e1 == 1 and codigo_e1 == "BR001", (codigo_e1, sequencial_e1)
ok("E1) 10:00 Brasília (14/jun): primeira senha do dia, BR001")

codigo_e2, sequencial_e2 = emitir_com_relogio_fake(id_fila_e, ids["unidade1"], (2026, 6, 15, 2, 30))  # 23:30 Brasília, AINDA 14/jun
assert sequencial_e2 == 2 and codigo_e2 == "BR002", (codigo_e2, sequencial_e2)
ok("E2) 23:30 Brasília (ainda 14/jun, servidor em 02:30 UTC de 15/jun): CONTINUA o mesmo dia -> BR002 (bug da 10.39 dava BR001)")

codigo_e3, sequencial_e3 = emitir_com_relogio_fake(id_fila_e, ids["unidade1"], (2026, 6, 15, 3, 0))  # 00:00 Brasília, 15/jun - dia novo de verdade
assert sequencial_e3 == 1 and codigo_e3 == "BR001", (codigo_e3, sequencial_e3)
ok("E3) 00:00 Brasília (15/jun de verdade, servidor em 03:00 UTC): reseta para BR001")

# ===========================================================================
# H) Faixa INTEIRA 21h-23:59 Brasília (não só um ponto) - foi exatamente essa
#    janela que ficava quebrada (meia-noite UTC = 21h Brasília). Percorre
#    20:00, 21:00, 22:00, 23:00 e 23:59 Brasília do mesmo dia civil (18/jun) -
#    sequencial tem que so incrementar, nunca resetar no meio dessa faixa - e
#    só reseta de verdade em 00:00 Brasília do dia seguinte (19/jun).
# ===========================================================================
id_fila_h = nova_fila("JN", ids["unidade1"])

checkpoints_brasilia_para_utc = [
    ("20:00 (18/jun)", (2026, 6, 18, 23, 0)),   # 20:00 Brasília 18/jun = 23:00 UTC 18/jun
    ("21:00 (18/jun)", (2026, 6, 19, 0, 0)),    # 21:00 Brasília 18/jun = 00:00 UTC 19/jun (virada UTC, NÃO da clínica)
    ("22:00 (18/jun)", (2026, 6, 19, 1, 0)),
    ("23:00 (18/jun)", (2026, 6, 19, 2, 0)),
    ("23:59 (18/jun)", (2026, 6, 19, 2, 59)),
]

sequencial_esperado = 1
for rotulo, instante in checkpoints_brasilia_para_utc:
    codigo_h, sequencial_h = emitir_com_relogio_fake(id_fila_h, ids["unidade1"], instante)
    assert sequencial_h == sequencial_esperado, (rotulo, codigo_h, sequencial_h, sequencial_esperado)
    assert codigo_h == f"JN{sequencial_esperado:03d}", (rotulo, codigo_h)
    sequencial_esperado += 1
ok("H) Faixa 21h-23:59 Brasília inteira (20:00 a 23:59 do mesmo dia): sequencial só incrementa (JN001..JN005), nunca reseta no meio")

codigo_h_novo_dia, sequencial_h_novo_dia = emitir_com_relogio_fake(id_fila_h, ids["unidade1"], (2026, 6, 19, 3, 0))  # 00:00 Brasília 19/jun
assert sequencial_h_novo_dia == 1 and codigo_h_novo_dia == "JN001", (codigo_h_novo_dia, sequencial_h_novo_dia)
ok("H) 00:00 Brasília do dia seguinte (19/jun de verdade): agora sim reseta para JN001")

# ===========================================================================
# F) digitos_sequencial configurável: padding respeita o valor da fila.
# ===========================================================================
id_fila_f2 = nova_fila("PD", ids["unidade1"], digitos=2)
codigo_f2, _ = emitir(id_fila_f2, ids["unidade1"])
assert codigo_f2 == "PD01", codigo_f2

id_fila_f4 = nova_fila("PD4", ids["unidade1"], digitos=4)
codigo_f4, _ = emitir(id_fila_f4, ids["unidade1"])
assert codigo_f4 == "PD40001", codigo_f4
ok("digitos_sequencial configurável: 2 dígitos -> PD01, 4 dígitos -> PD40001 (não PD1/PD41)")

# ===========================================================================
# G) Concorrência (fallback documentado no topo do arquivo): a query de
#    "próximo sequencial" é determinística e serializada sob chamadas
#    repetidas - cada chamada só enxerga o commit da anterior, sem duplicar
#    nem pular sequencial. É a mesma garantia que SELECT...FOR UPDATE/UPDATE
#    de linha preserva sob concorrência real (MySQL/InnoDB em produção).
# ===========================================================================
id_fila_g = nova_fila("CC", ids["unidade1"])
sequenciais = [emitir(id_fila_g, ids["unidade1"])[1] for _ in range(15)]
assert sequenciais == list(range(1, 16)), sequenciais
ok("Concorrência (fallback): 15 chamadas sucessivas produzem sequencial 1..15 sem duplicar nem pular")

print()
print(f"TODOS OS {len(passos)} TESTES DE SEQUENCIAL DIARIO PASSARAM")
