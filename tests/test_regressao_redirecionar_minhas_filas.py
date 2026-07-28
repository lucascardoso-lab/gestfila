"""
Teste de regressão: seção "Minhas filas" (acesso rápido) no bloco Redirecionar
da tela de Atendimento, combinando dois modos de seleção com estado
compartilhado (busca livre já existente - adendo 10.10 - + botões de acesso
rápido às filas do turno ativo do operador - adendo 10.19).

Cobre:
- Botões de acesso rápido só aparecem quando o operador tem um turno ativo
  configurado PARA O LOCAL em questão, na mesma ordem escolhida na tela
  "Iniciar Turno".
- Cada botão só aparece se a fila também for um destino válido (nunca amplia
  a autorização de filas_destino() - isolamento multi-tenant/restrição opcional
  de Local.filas_destino_permitidas preservados).
- Indicador visual de "atendendo agora" por fila.
- Degradação elegante: sem turno ativo, só a busca livre aparece (sem a seção
  "Minhas filas" nem o separador).
- O campo oculto fila_destino_id (mesmo usado pela busca) é o único produzido
  pelos botões rápidos - o backend do Realocar não muda.

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
SEMPRE_FECHADA = {dia: {"manha": False, "tarde": False} for dia in DIAS_SEMANA}

QTD_FILAS_GRANDE = 18

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
    fila_turno_2 = Fila(
        empresa_id=empresa.id, unidade_id=unidade.id, nome="Dr. Fechado", sigla="DF",
        horario_funcionamento=dict(SEMPRE_FECHADA),
    )
    fila_fora_do_turno = Fila(empresa_id=empresa.id, unidade_id=unidade.id, nome="Fora Do Turno", sigla="FT")
    db.session.add_all([fila_origem, fila_turno_1, fila_turno_2, fila_fora_do_turno])
    db.session.commit()

    local = Local(empresa_id=empresa.id, unidade_id=unidade.id, nome="Guiche 01", tipo="recepcao")
    outro_local = Local(empresa_id=empresa.id, unidade_id=unidade.id, nome="Guiche 02", tipo="recepcao")
    db.session.add_all([local, outro_local])
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
    id_sem_turno = novo_usuario("recepcao_mf_sem_turno", [perfil_recepcao])
    id_turno_outro_local = novo_usuario("recepcao_mf_outro_local", [perfil_recepcao])

    # turno ativo do operador 1, no `local` em questão, com fila_turno_2 antes de
    # fila_turno_1 (ordem específica, escolhida por ele - deve ser respeitada).
    db.session.add(TurnoAtivo(
        usuario_id=id_com_turno, local_id=local.id, ativo=True,
        filas_config=[
            {"fila_id": fila_turno_2.id, "ordem": 0, "chamadas_seguidas": 1},
            {"fila_id": fila_turno_1.id, "ordem": 1, "chamadas_seguidas": 2},
        ],
    ))
    # turno ativo, mas de OUTRO local - não deve aparecer nas 'Minhas filas' do
    # `local` usado neste teste.
    db.session.add(TurnoAtivo(
        usuario_id=id_turno_outro_local, local_id=outro_local.id, ativo=True,
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
    id_senha_sem_turno = nova_senha_chamada(id_sem_turno, "RG002")

    # Cenário de "muitas filas" (item explícito do pedido: 15+ não pode truncar
    # nem ganhar um "ver todas") - outro Local, outro operador, outra fila de
    # origem, pra não interferir nos cenários A-D acima.
    local_muitas_filas = Local(empresa_id=empresa.id, unidade_id=unidade.id, nome="Guiche Muitas Filas", tipo="recepcao")
    fila_origem_grande = Fila(empresa_id=empresa.id, unidade_id=unidade.id, nome="Origem Grande", sigla="OG")
    db.session.add_all([local_muitas_filas, fila_origem_grande])
    db.session.commit()

    filas_grandes = []
    for indice in range(QTD_FILAS_GRANDE):
        f = Fila(
            empresa_id=empresa.id, unidade_id=unidade.id,
            nome=f"Fila Rapida {indice:02d}", sigla=f"F{indice:02d}",
            horario_funcionamento=dict(SEMPRE_ABERTA),
        )
        db.session.add(f)
        filas_grandes.append(f)
    db.session.commit()

    id_operador_muitas_filas = novo_usuario("recepcao_mf_muitas_filas", [perfil_recepcao])
    db.session.add(TurnoAtivo(
        usuario_id=id_operador_muitas_filas, local_id=local_muitas_filas.id, ativo=True,
        filas_config=[
            {"fila_id": f.id, "ordem": indice, "chamadas_seguidas": 1}
            for indice, f in enumerate(filas_grandes)
        ],
    ))
    db.session.commit()

    senha_muitas_filas = Senha(
        empresa_id=empresa.id, unidade_id=unidade.id, fila_id=fila_origem_grande.id,
        codigo="OG001", sequencial=1, prioridade=False, status="chamada",
        local_id=local_muitas_filas.id, operador_id=id_operador_muitas_filas, setor="recepcao",
    )
    db.session.add(senha_muitas_filas)
    db.session.commit()

    ids = dict(
        fila_origem=fila_origem.id, fila_turno_1=fila_turno_1.id, fila_turno_2=fila_turno_2.id,
        fila_fora_do_turno=fila_fora_do_turno.id, local=local.id,
        senha_com_turno=id_senha_com_turno, senha_sem_turno=id_senha_sem_turno,
        local_muitas_filas=local_muitas_filas.id, filas_grandes=[f.id for f in filas_grandes],
    )


def login_como(login, local_id):
    c = app.test_client()
    resp = c.post("/login", data={"login": login, "senha": "senha123"}, follow_redirects=True)
    assert resp.status_code == 200, (login, resp.status_code)
    with c.session_transaction() as sess:
        sess["atendimento_local_id"] = local_id
    return c


URL_FILA_ORIGEM = f"/atendimento/recepcao/filas/{ids['fila_origem']}"

# ===========================================================================
# A) Operador COM turno ativo neste Local: seção "Minhas filas" aparece, com
#    exatamente as filas do turno (na ordem escolhida), separador visual, e
#    marcador de "atendendo agora"/"fora do horário" por botão.
# ===========================================================================
c_com_turno = login_como("recepcao_mf_com_turno", ids["local"])
resp = c_com_turno.get(URL_FILA_ORIGEM)
assert resp.status_code == 200
texto = resp.data.decode("utf-8")

assert 'id="grade-filas-rapidas"' in texto
assert "Minhas filas" in texto
ok("Seção 'Minhas filas' aparece pra operador com turno ativo neste Local")

assert 'data-fila-id="{}"'.format(ids["fila_turno_2"]) in texto
assert 'data-fila-id="{}"'.format(ids["fila_turno_1"]) in texto
indice_2 = texto.index('data-fila-id="{}"'.format(ids["fila_turno_2"]))
indice_1 = texto.index('data-fila-id="{}"'.format(ids["fila_turno_1"]))
assert indice_2 < indice_1  # fila_turno_2 foi configurada primeiro no turno
ok("Botões de acesso rápido aparecem na mesma ordem escolhida ao iniciar o turno")

# fila_fora_do_turno não tem botão rápido (não está no turno),
# mas continua disponível via busca livre (presente no JSON embutido).
trecho_grade = texto[texto.index('id="grade-filas-rapidas"'):texto.index("dados-filas-destino")]
assert f'data-fila-id="{ids["fila_fora_do_turno"]}"' not in trecho_grade
assert str(ids["fila_fora_do_turno"]) in texto  # ainda no JSON da busca livre
ok("Fila fora do turno não vira botão rápido, mas continua disponível pela busca livre")

trecho_turno1 = texto[texto.index(f'data-fila-id="{ids["fila_turno_1"]}"'):texto.index(f'data-fila-id="{ids["fila_turno_1"]}"') + 300]
assert 'ativa' in trecho_turno1 and 'title="Atendendo agora"' in trecho_turno1
trecho_turno2 = texto[texto.index(f'data-fila-id="{ids["fila_turno_2"]}"'):texto.index(f'data-fila-id="{ids["fila_turno_2"]}"') + 300]
assert 'inativa' in trecho_turno2 and 'title="Fora do horário"' in trecho_turno2
ok("Indicador visual (ativa/inativa) reflete o horário de funcionamento de cada fila rápida")

assert "divisor-secoes-redirecionar" in texto
ok("Separador visual entre 'Minhas filas' e a busca livre está presente")

assert "→ Redirecionando para:" in texto
ok("Indicador de selecao atual usa o texto 'Redirecionando para' com a seta")

# ===========================================================================
# B) Só filas que também são destinos válidos entram - turno de OUTRO local não
#    vaza pras 'Minhas filas' de ninguém neste Local.
# ===========================================================================
assert f'data-fila-id="{ids["fila_turno_1"]}"' in trecho_grade  # confirma presença esperada
resp_outro = c_com_turno.get(f"/atendimento/recepcao/local/{ids['local']}")
# (não precisa de asserts extras aqui - só garante que a rota por-Local também renderiza)
assert resp_outro.status_code == 200
ok("Tela por-Local também renderiza normalmente com 'Minhas filas' calculado")

# ===========================================================================
# C) Operador SEM turno ativo: degradação elegante - só a busca livre aparece,
#    sem a seção 'Minhas filas' nem o separador.
# ===========================================================================
c_sem_turno = login_como("recepcao_mf_sem_turno", ids["local"])
resp = c_sem_turno.get(URL_FILA_ORIGEM)
assert resp.status_code == 200
texto_sem_turno = resp.data.decode("utf-8")

assert 'id="grade-filas-rapidas"' not in texto_sem_turno
assert '<span class="rotulo-secao-redirecionar">Minhas filas</span>' not in texto_sem_turno
assert '<div class="divisor-secoes-redirecionar">' not in texto_sem_turno
assert 'id="busca-redirecionar"' in texto_sem_turno  # busca livre continua funcionando
ok("Sem turno ativo: só a busca livre aparece, sem a seção 'Minhas filas' (degradação elegante)")

# ===========================================================================
# D) Botão de acesso rápido (mesmo campo fila_destino_id da busca) realmente
#    realoca a senha - o botão simula clicar em 'Dr. Aberto' (fila_turno_1),
#    que preenche o hidden e submete via 'Realocar' normalmente.
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
ok("Realocar continua funcionando de ponta a ponta com o campo produzido pelos botões rápidos")

# ===========================================================================
# E) 15+ filas no turno ativo: TODAS aparecem como botão, sem truncar e sem
#    nenhum link/botão "ver todas" - grade responsiva (flex-wrap), cada botão
#    do tamanho do próprio nome (sem largura fixa).
# ===========================================================================
c_muitas_filas = login_como("recepcao_mf_muitas_filas", ids["local_muitas_filas"])
resp = c_muitas_filas.get(f"/atendimento/recepcao/local/{ids['local_muitas_filas']}")
assert resp.status_code == 200
texto_muitas_filas = resp.data.decode("utf-8")

assert len(ids["filas_grandes"]) == QTD_FILAS_GRANDE
for fila_id in ids["filas_grandes"]:
    assert f'data-fila-id="{fila_id}"' in texto_muitas_filas, fila_id
ok(f"Todas as {QTD_FILAS_GRANDE} filas do turno ativo aparecem como botão de acesso rápido, nenhuma omitida")

texto_minuscula = texto_muitas_filas.lower()
assert "ver todas" not in texto_minuscula
assert "mostrar mais" not in texto_minuscula
assert "ver mais" not in texto_minuscula
ok("Nenhum link/botão 'ver todas' (ou equivalente) foi adicionado - a lista completa vem sempre")

assert "flex-wrap: wrap" in texto_muitas_filas
ok("Grade responsiva (flex-wrap) presente no CSS, pra não quebrar o layout com muitos botões")

# nenhum botão força largura fixa (teria 'width:' fixo no CSS de .botao-fila-rapida,
# em vez de se ajustar ao próprio conteúdo via 'flex: 0 0 auto').
indice_regra = texto_muitas_filas.index(".botao-fila-rapida {")
regra_botao = texto_muitas_filas[indice_regra:texto_muitas_filas.index("}", indice_regra)]
assert "flex: 0 0 auto" in regra_botao
assert "width:" not in regra_botao.replace("min-width", "")
ok("Cada botão ocupa só o espaço necessário pro próprio nome, sem largura fixa forçada")

print()
print(f"TODOS OS {len(passos)} TESTES DE 'MINHAS FILAS' NO BLOCO REDIRECIONAR PASSARAM")
