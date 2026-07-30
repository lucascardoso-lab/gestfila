"""
Teste de regressão: forma definitiva de "Chamar" pra recepcao/coleta/recepcao_coleta
- ajuste sobre a mesma tela desta sessão de trabalho, depois de duas idas e vindas:

1. Primeiro o botão "Chamar" principal (seleção automática/rodízio) foi escondido
   desses perfis (só admin via), sem alternativa nenhuma pra painel_local (que
   nunca teve um "Chamar" por linha, ao contrário de painel_fila).
2. O cliente reportou que recepção/coleta precisam chamar senhas na prática e
   pediu pra reativar - reativado pra TODOS os perfis, nas duas telas.
3. Cliente esclareceu (com print, círculo vermelho): o que ele queria de volta era
   a opção de chamar UMA senha específica por linha na tabela "Senhas em espera"
   (que já existia em painel_fila, mas nunca existiu em painel_local) - não
   necessariamente o botão principal de seleção automática. Com o "Chamar" por
   linha agora existindo nas DUAS telas, o botão principal volta a ficar restrito
   a admin/super admin (estado final: igual ao item 1, mas com painel_local tendo
   ganho a paridade de recursos que faltava).

Estado final, nas DUAS telas (painel_fila e painel_local):
- Botão "Chamar" principal (`#botao-chamar`, seleção automática/rodízio): só
  admin/super admin veem.
- Botão "Chamar" por linha, na tabela "Senhas em espera" (chama especificamente
  aquela senha, por código): TODOS os perfis operacionais veem - inclusive
  painel_local, que ganhou essa coluna "Ações" nesta correção.

RBAC de backend nunca mudou em nenhuma das três versões - as rotas
`chamar`/`chamar_local` sempre aceitaram qualquer perfil operacional autorizado
por `exigir_acesso_setor`; a diferença sempre foi só de UI.

Execução: python -m tests.test_regressao_botao_chamar_por_linha (a partir da raiz
do projeto, com o virtualenv ativado).
"""
import os

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


with app.app_context():
    db.create_all()

    perfil_recepcao = Perfil(nome="atendente")
    perfil_coleta = Perfil(nome="executor")
    perfil_recepcao_coleta = Perfil(nome="atendente_executor")
    perfil_admin = Perfil(nome="admin")
    db.session.add_all([perfil_recepcao, perfil_coleta, perfil_recepcao_coleta, perfil_admin])
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

    db.session.add(LocalRodizioFila(local_id=local.id, fila_id=fila.id, ordem=0, chamadas_seguidas=1))
    db.session.commit()

    senha_espera = Senha(
        empresa_id=empresa.id, unidade_id=unidade.id, fila_id=fila.id,
        codigo="FA001", sequencial=1, prioridade=False, status="aguardando",
    )
    db.session.add(senha_espera)
    db.session.commit()

    def novo_usuario(login, perfis):
        u = Usuario(empresa_id=empresa.id, nome=login, login=login)
        u.set_senha("senha123")
        u.perfis = perfis
        u.unidades = [unidade]
        db.session.add(u)
        db.session.commit()
        return u.id

    id_recepcao = novo_usuario("recepcao_bl", [perfil_recepcao])
    id_coleta = novo_usuario("coleta_bl", [perfil_coleta])
    id_recepcao_coleta = novo_usuario("recepcao_coleta_bl", [perfil_recepcao_coleta])
    id_admin = novo_usuario("admin_bl", [perfil_admin])

    ids = dict(fila=fila.id, local=local.id, senha_espera=senha_espera.id)


def login_como(login):
    c = app.test_client()
    resp = c.post("/login", data={"login": login, "senha": "senha123"}, follow_redirects=True)
    assert resp.status_code == 200, (login, resp.status_code)
    with c.session_transaction() as sess:
        sess["atendimento_local_id"] = ids["local"]
    return c


def tem_botao_principal(texto, url_chamar):
    indice = texto.find(f'id="botao-chamar"')
    if indice == -1:
        return False
    # confirma que é o botão certo (mesmo formaction), não só a presença do id.
    return f'formaction="{url_chamar}"' in texto[indice:indice + 200]


def tem_botao_por_linha(texto, url_chamar, codigo):
    # o botão por linha usa a MESMA action (chamar/chamar_local) do botão
    # principal, mas com um <input type="hidden" name="codigo" value="...">
    # próprio daquela senha - localizamos pelo trecho que contém o código.
    indice = texto.find(f'value="{codigo}"')
    if indice == -1:
        return False
    trecho = texto[max(0, indice - 400):indice + 400]
    return f'action="{url_chamar}"' in trecho and ">Chamar<" in trecho


for login, setor in [
    ("recepcao_bl", "recepcao"),
    ("coleta_bl", "coleta"),
    ("recepcao_coleta_bl", "recepcao"),
]:
    c = login_como(login)

    resp = c.get(f"/atendimento/{setor}/filas/{ids['fila']}")
    assert resp.status_code == 200
    texto = resp.data.decode("utf-8")
    url_chamar = f"/atendimento/{setor}/filas/{ids['fila']}/chamar"
    assert not tem_botao_principal(texto, url_chamar), (login, "painel_fila", "principal deveria estar oculto")
    assert tem_botao_por_linha(texto, url_chamar, "FA001"), (login, "painel_fila", "por linha deveria aparecer")
    ok(f"painel_fila ({login}): botão principal oculto, botão por linha aparece")

    resp = c.get(f"/atendimento/{setor}/local/{ids['local']}")
    assert resp.status_code == 200
    texto = resp.data.decode("utf-8")
    url_chamar_local = f"/atendimento/{setor}/local/{ids['local']}/chamar"
    assert not tem_botao_principal(texto, url_chamar_local), (login, "painel_local", "principal deveria estar oculto")
    assert tem_botao_por_linha(texto, url_chamar_local, "FA001"), (login, "painel_local", "por linha deveria aparecer")
    ok(f"painel_local ({login}): botão principal oculto, botão por linha aparece")

c_admin = login_como("admin_bl")
resp = c_admin.get(f"/atendimento/recepcao/filas/{ids['fila']}")
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
url_chamar = f"/atendimento/recepcao/filas/{ids['fila']}/chamar"
assert tem_botao_principal(texto, url_chamar)
assert tem_botao_por_linha(texto, url_chamar, "FA001")
ok("painel_fila (admin): botão principal E botão por linha aparecem")

resp = c_admin.get(f"/atendimento/recepcao/local/{ids['local']}")
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
url_chamar_local = f"/atendimento/recepcao/local/{ids['local']}/chamar"
assert tem_botao_principal(texto, url_chamar_local)
assert tem_botao_por_linha(texto, url_chamar_local, "FA001")
ok("painel_local (admin): botão principal E botão por linha aparecem")

# o botão por linha (recepção, sem o principal) de fato funciona - chama a
# senha específica pelo código escondido no form daquela linha.
c_recepcao = login_como("recepcao_bl")
resp = c_recepcao.post(
    f"/atendimento/recepcao/filas/{ids['fila']}/chamar",
    data={"codigo": "FA001"},
    follow_redirects=True,
)
assert resp.status_code == 200
assert "FA001 chamada".encode("utf-8") in resp.data
with app.app_context():
    assert db.session.get(Senha, ids["senha_espera"]).status == "chamada"
ok("Botão por linha (recepção, sem o principal visível) chama a senha específica normalmente por trás")

print()
print(f"TODOS OS {len(passos)} TESTES DO BOTAO CHAMAR POR LINHA PASSARAM")
