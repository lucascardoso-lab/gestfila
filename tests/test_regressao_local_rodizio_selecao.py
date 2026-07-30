"""
Teste de regressão: lógica de seleção do rodízio por Local (adendo 10.8, Fase
10.8-C) - selecionar_proxima_fila_e_senha(local), e a generalização de
senha_atual_no_local/senhas_atendidas_no_local + senhas_aguardando_local(local).

Ponto de atenção específico (reforçado pelo usuário antes desta fase): a janela de
histórico usada para reconstruir "quantas chamadas seguidas da mesma fila" é a soma
de `chamadas_seguidas` das filas candidatas DAQUELE Local especificamente -
dinâmica, nunca uma constante fixa global. Cobre isso com dois Locais configurados
com somas diferentes, chamados de forma intercalada (para não deixar nenhuma dúvida
de que não há estado compartilhado entre eles).

Execução: python -m tests.test_regressao_local_rodizio_selecao (a partir da raiz
do projeto, com o virtualenv ativado).
"""
import os
from datetime import datetime, timedelta

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

from app import create_app
from app.extensions import db
from app.models import Empresa, Fila, Local, LocalRodizioFila, Senha, Unidade, Usuario
from app.models.fila import DIAS_SEMANA
from app.atendimento.helpers import (
    selecionar_proxima_fila_e_senha,
    senha_atual_no_local,
    senhas_aguardando_local,
    senhas_atendidas_no_local,
)

app = create_app("testing")

passos = []


def ok(msg):
    passos.append(msg)
    print("OK:", msg)


HORARIO_FECHADO = {dia: {"manha": False, "tarde": False} for dia in DIAS_SEMANA}

with app.app_context():
    db.create_all()

    empresa = Empresa(nome="Clinica A")
    db.session.add(empresa)
    db.session.commit()

    unidade = Unidade(empresa_id=empresa.id, nome="Unidade A1")
    db.session.add(unidade)
    db.session.commit()

    def nova_fila(nome, sigla, horario_fechado=False, ativo=True):
        kwargs = {"horario_funcionamento": dict(HORARIO_FECHADO)} if horario_fechado else {}
        f = Fila(empresa_id=empresa.id, unidade_id=unidade.id, nome=nome, sigla=sigla, ativo=ativo, **kwargs)
        db.session.add(f)
        db.session.commit()
        return f

    fila_a1 = nova_fila("A1", "A1")
    fila_a2 = nova_fila("A2", "A2")
    fila_b1 = nova_fila("B1", "B1")
    fila_b2 = nova_fila("B2", "B2")
    fila_e1 = nova_fila("E1", "E1")
    fila_e2 = nova_fila("E2", "E2")
    fila_fechada = nova_fila("Fechada", "FC", horario_fechado=True)
    fila_inativa = nova_fila("Inativa", "IA", ativo=False)
    fila_normal_g = nova_fila("Normal G", "NG")
    fila_h1 = nova_fila("H1", "H1")
    fila_h2 = nova_fila("H2", "H2")

    local_a = Local(
        empresa_id=empresa.id, unidade_id=unidade.id, nome="Local A",
        nome_exibicao="Local A", identificador_admin="[UNI] Local A",
    )
    local_b = Local(
        empresa_id=empresa.id, unidade_id=unidade.id, nome="Local B",
        nome_exibicao="Local B", identificador_admin="[UNI] Local B",
    )
    local_e = Local(
        empresa_id=empresa.id, unidade_id=unidade.id, nome="Local E (fallback)",
        nome_exibicao="Local E (fallback)", identificador_admin="[UNI] Local E (fallback)",
    )
    local_f = Local(
        empresa_id=empresa.id, unidade_id=unidade.id, nome="Local F (horario/inativo)",
        nome_exibicao="Local F (horario/inativo)", identificador_admin="[UNI] Local F (horario/inativo)",
    )
    local_g = Local(
        empresa_id=empresa.id, unidade_id=unidade.id, nome="Local G (sem candidatas)",
        nome_exibicao="Local G (sem candidatas)", identificador_admin="[UNI] Local G (sem candidatas)",
    )
    local_h = Local(
        empresa_id=empresa.id, unidade_id=unidade.id, nome="Local H (consultas gerais)",
        nome_exibicao="Local H (consultas gerais)", identificador_admin="[UNI] Local H (consultas gerais)",
    )
    local_sem_rodizio = Local(
        empresa_id=empresa.id, unidade_id=unidade.id, nome="Local Sem Rodizio",
        nome_exibicao="Local Sem Rodizio", identificador_admin="[UNI] Local Sem Rodizio",
    )
    db.session.add_all([local_a, local_b, local_e, local_f, local_g, local_h, local_sem_rodizio])
    db.session.commit()

    # Local A: soma = 2 + 1 = 3 (ciclo esperado: A1, A1, A2)
    db.session.add_all([
        LocalRodizioFila(local_id=local_a.id, fila_id=fila_a1.id, ordem=0, chamadas_seguidas=2),
        LocalRodizioFila(local_id=local_a.id, fila_id=fila_a2.id, ordem=1, chamadas_seguidas=1),
    ])
    # Local B: soma = 1 + 3 = 4 (ciclo esperado: B1, B2, B2, B2) - soma DIFERENTE da de Local A
    db.session.add_all([
        LocalRodizioFila(local_id=local_b.id, fila_id=fila_b1.id, ordem=0, chamadas_seguidas=1),
        LocalRodizioFila(local_id=local_b.id, fila_id=fila_b2.id, ordem=1, chamadas_seguidas=3),
    ])
    # Local E: fila_e1 tem cota alta (5) mas só 2 senhas aguardando - testa fallback
    db.session.add_all([
        LocalRodizioFila(local_id=local_e.id, fila_id=fila_e1.id, ordem=0, chamadas_seguidas=5),
        LocalRodizioFila(local_id=local_e.id, fila_id=fila_e2.id, ordem=1, chamadas_seguidas=1),
    ])
    # Local F: fila fechada + fila inativa + 1 fila normal - só a normal é candidata
    db.session.add_all([
        LocalRodizioFila(local_id=local_f.id, fila_id=fila_fechada.id, ordem=0, chamadas_seguidas=1),
        LocalRodizioFila(local_id=local_f.id, fila_id=fila_inativa.id, ordem=1, chamadas_seguidas=1),
        LocalRodizioFila(local_id=local_f.id, fila_id=fila_normal_g.id, ordem=2, chamadas_seguidas=1),
    ])
    # Local G: só tem a fila fechada configurada - nenhuma candidata disponível
    db.session.add_all([
        LocalRodizioFila(local_id=local_g.id, fila_id=fila_fechada.id, ordem=0, chamadas_seguidas=1),
    ])
    db.session.commit()

    def novas_senhas_aguardando(fila, quantidade, prefixo_codigo):
        for i in range(quantidade):
            db.session.add(Senha(
                empresa_id=empresa.id, unidade_id=unidade.id, fila_id=fila.id,
                codigo=f"{prefixo_codigo}{i:03d}", sequencial=i + 1, prioridade=False, status="aguardando",
            ))
        db.session.commit()

    novas_senhas_aguardando(fila_a1, 20, "A1")
    novas_senhas_aguardando(fila_a2, 20, "A2")
    novas_senhas_aguardando(fila_b1, 20, "B1")
    novas_senhas_aguardando(fila_b2, 20, "B2")
    novas_senhas_aguardando(fila_e1, 2, "E1")   # só 2 - propositalmente escasso
    novas_senhas_aguardando(fila_e2, 20, "E2")
    novas_senhas_aguardando(fila_normal_g, 5, "NG")

    ids = dict(
        empresa=empresa.id, unidade=unidade.id,
        local_a=local_a.id, local_b=local_b.id, local_e=local_e.id, local_f=local_f.id,
        local_g=local_g.id, local_h=local_h.id, local_sem_rodizio=local_sem_rodizio.id,
        fila_h1=fila_h1.id, fila_h2=fila_h2.id,
    )

print("IDS", ids)


def db_get(modelo, id_):
    with app.app_context():
        return db.session.get(modelo, id_)


def simular_chamada(local, contador):
    """Chama selecionar_proxima_fila_e_senha(local) e registra o mínimo necessário
    (status/chamada_em/local_id) para a próxima chamada já enxergar esse histórico -
    equivalente ao que registrar_chamada_senha() faria, sem precisar de rota HTTP
    (Fase 10.8-C é só a lógica, a rota é a Fase 10.8-D)."""
    fila, senha = selecionar_proxima_fila_e_senha(local)
    assert senha is not None, f"esperava uma senha disponível (contador={contador})"
    contador[0] += 1
    senha.status = "chamada"
    senha.chamada_em = datetime.utcnow() + timedelta(seconds=contador[0])
    senha.local_id = local.id
    db.session.commit()
    return fila.nome


# ---------------------------------------------------------------------------
# 1) Soma dinâmica por Local: Local A (soma=3, ciclo A1,A1,A2) e Local B (soma=4,
#    ciclo B1,B2,B2,B2) chamados de forma INTERCALADA - prova que a janela de
#    histórico de um não interfere na do outro (nada de constante global).
# ---------------------------------------------------------------------------
with app.app_context():
    local_a_db = db.session.get(Local, ids["local_a"])
    local_b_db = db.session.get(Local, ids["local_b"])
    contador = [0]

    sequencia_a = []
    sequencia_b = []
    for _ in range(8):
        sequencia_a.append(simular_chamada(local_a_db, contador))
        sequencia_b.append(simular_chamada(local_b_db, contador))

    assert sequencia_a == ["A1", "A1", "A2", "A1", "A1", "A2", "A1", "A1"]
    assert sequencia_b == ["B1", "B2", "B2", "B2", "B1", "B2", "B2", "B2"]
ok("Soma dinâmica: Local A (soma=3, ciclo A1-A1-A2) e Local B (soma=4, ciclo B1-B2-B2-B2) "
   "chamados intercalados - cada um respeita SÓ a própria configuração, sem interferência mútua")

# ---------------------------------------------------------------------------
# 2) Primeira chamada (sem histórico nenhum) começa pela fila de menor `ordem`.
# ---------------------------------------------------------------------------
with app.app_context():
    local_f_db = db.session.get(Local, ids["local_f"])
    fila, senha = selecionar_proxima_fila_e_senha(local_f_db)
    assert fila.nome == "Normal G"  # únca candidata disponível (as outras 2 estão fechada/inativa)
ok("Fila fechada (horário) e fila inativa são puladas - só 'Normal G' é candidata neste Local")

# ---------------------------------------------------------------------------
# 3) Local sem NENHUMA candidata disponível retorna (None, None), não erro.
# ---------------------------------------------------------------------------
with app.app_context():
    local_g_db = db.session.get(Local, ids["local_g"])
    fila, senha = selecionar_proxima_fila_e_senha(local_g_db)
    assert fila is None and senha is None
ok("Local cuja única fila configurada está fechada: retorna (None, None), sem erro")

# ---------------------------------------------------------------------------
# 4) Fallback: fila da vez configurada com cota alta, mas sem ninguém esperando -
#    o rodízio pula pra próxima fila da lista, sem travar.
# ---------------------------------------------------------------------------
with app.app_context():
    local_e_db = db.session.get(Local, ids["local_e"])
    contador = [0]
    # E1 tem chamadas_seguidas=5 mas só 2 senhas aguardando - as 2 primeiras chamadas
    # ficam com E1 (cota alta ainda não asgotada do ponto de vista do rodízio), a
    # 3ª chamada teria E1 de novo pela cota, mas E1 já não tem mais ninguém -> cai pra E2.
    r1 = simular_chamada(local_e_db, contador)
    r2 = simular_chamada(local_e_db, contador)
    r3 = simular_chamada(local_e_db, contador)
    assert r1 == "E1" and r2 == "E1"
    assert r3 == "E2"  # fallback: E1 ainda seria a vez pela cota, mas está vazia
ok("Fallback: fila da vez (E1, cota=5) fica sem ninguém esperando após 2 chamadas - "
   "rodízio cai para E2 automaticamente, sem travar nem retornar senha=None")

# ---------------------------------------------------------------------------
# 5) senha_atual_no_local/senhas_atendidas_no_local generalizadas: fila_ids=[...]
#    preserva o filtro fixo (comportamento antigo, usado pela tela por-fila);
#    fila_ids=None agrega qualquer fila do Local (novo, tela por-Local).
#
#    senha_atual_no_local passou a exigir `operador` (controle de atendimento -
#    escopado por operador, não só por local) - as duas senhas abaixo precisam de
#    operador_id preenchido pra aparecerem na busca.
# ---------------------------------------------------------------------------
with app.app_context():
    operador_h = Usuario(empresa_id=ids["empresa"], nome="Operador H", login="operador_h_rodizio")
    operador_h.set_senha("senha123")
    db.session.add(operador_h)
    db.session.commit()
    ids["operador_h"] = operador_h.id

with app.app_context():
    local_h_db = db.session.get(Local, ids["local_h"])
    senha_h1 = Senha(
        empresa_id=ids["empresa"], unidade_id=ids["unidade"], fila_id=ids["fila_h1"],
        codigo="H1001", sequencial=1, prioridade=False, status="chamada",
        chamada_em=datetime.utcnow(), local_id=local_h_db.id, operador_id=ids["operador_h"],
    )
    db.session.add(senha_h1)
    db.session.commit()

    senha_h2 = Senha(
        empresa_id=ids["empresa"], unidade_id=ids["unidade"], fila_id=ids["fila_h2"],
        codigo="H2001", sequencial=1, prioridade=False, status="chamada",
        chamada_em=datetime.utcnow() + timedelta(seconds=5), local_id=local_h_db.id,  # mais recente
        operador_id=ids["operador_h"],
    )
    db.session.add(senha_h2)
    db.session.commit()

    operador_h_db = db.session.get(Usuario, ids["operador_h"])

    # comportamento antigo preservado: fila_ids=[fila_h1.id] só enxerga a senha da fila H1
    atual_so_h1 = senha_atual_no_local(local_h_db, operador_h_db, [ids["fila_h1"]])
    assert atual_so_h1.codigo == "H1001"

    # comportamento novo: sem fila_ids, enxerga a mais recente entre QUALQUER fila do Local
    atual_qualquer_fila = senha_atual_no_local(local_h_db, operador_h_db)
    assert atual_qualquer_fila.codigo == "H2001"
ok("senha_atual_no_local: fila_ids=[x] preserva o filtro antigo; fila_ids=None agrega qualquer fila do Local")

with app.app_context():
    local_h_db = db.session.get(Local, ids["local_h"])
    atendidas_so_h1 = senhas_atendidas_no_local(local_h_db, [ids["fila_h1"]])
    assert [s.codigo for s in atendidas_so_h1] == ["H1001"]

    atendidas_qualquer_fila = senhas_atendidas_no_local(local_h_db)
    assert {s.codigo for s in atendidas_qualquer_fila} == {"H1001", "H2001"}
ok("senhas_atendidas_no_local: mesma generalização - fila_ids=[x] preserva o antigo, None agrega tudo")

# ---------------------------------------------------------------------------
# 6) senhas_aguardando_local: agrega candidatas de todas as filas do rodízio;
#    Local sem rodízio configurado retorna lista vazia.
# ---------------------------------------------------------------------------
with app.app_context():
    local_a_db = db.session.get(Local, ids["local_a"])
    espera_a = senhas_aguardando_local(local_a_db)
    nomes_filas_espera = {s.fila.nome for s in espera_a}
    assert nomes_filas_espera == {"A1", "A2"}  # agrega as duas filas do rodízio de Local A
ok("senhas_aguardando_local: agrega 'Senhas em espera' de todas as filas do rodízio (A1 e A2)")

with app.app_context():
    local_sem_rodizio_db = db.session.get(Local, ids["local_sem_rodizio"])
    assert senhas_aguardando_local(local_sem_rodizio_db) == []
ok("senhas_aguardando_local: Local sem rodízio configurado retorna lista vazia")

print()
print(f"TODOS OS {len(passos)} TESTES DE SELEÇÃO DO RODÍZIO POR LOCAL (FASE 10.8-C) PASSARAM")
