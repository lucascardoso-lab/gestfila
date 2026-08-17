"""
Teste de regressão do fallback de áudio pré-gravado no painel de TV (BLUEPRINT.md,
adendo do áudio pré-gravado): para Smart TVs cujo motor de voz do sistema (Web
Speech API / speechSynthesis) "finge funcionar" - aceita speak(), dispara onend,
mas nenhum som sai - a chamada é anunciada concatenando clipes .wav gravados.

Cobre:
- Tabela dourada do slug canônico (gerar_slug_audio_local): acento, caixa mista,
  hífen, espaço, ordinal, vazio, e os nomes reais de Local já confirmados. É a
  ÚNICA implementação de slug (Python, servidor) - o JS nunca recalcula; travar
  o comportamento exato aqui é o que garante que o arquivo gravado
  (local_<slug>.wav) e o que a TV busca no disco nunca divirjam.
- Payload de chamada inclui `local_audio_slug` JÁ CALCULADO no servidor, e só
  quando o arquivo local_<slug>.wav de fato existe (senão None) - assim o painel
  sabe, sem sondar arquivo no cliente, se toca "dirija-se a <local>" ou cai no
  genérico sem repetir a preposição.
- TV com usa_audio_pregravado=True renderiza o caminho pré-gravado; TV sem a flag
  (padrão) continua com o caminho speechSynthesis intacto (não-regressão).
- Rede de segurança do genérico (local_generico) presente no JS para o caso de
  local sem slug/arquivo.
- Não-duplicação: o disparo de áudio só acontece no evento 'chamada' ao vivo,
  nunca na reconstrução de histórico (mesma proteção do adendo 10.13).
- Aviso não-bloqueante no cadastro de Local (Opção A): só quando falta o arquivo
  E existe TV com o fallback ligado na MESMA unidade.

Limitação de infraestrutura (mesma dos outros testes de JS - ex.
test_regressao_som_configuravel.py): sem runtime de navegador aqui, a cobertura do
lado JS é estrutural (o código/contrato certo chega ao HTML servido), não a
execução real do áudio.

Execução: python -m tests.test_regressao_audio_pregravado (a partir da raiz do
projeto, com o virtualenv ativado).
"""
import os

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

from app import create_app
from app.atendimento.helpers import _payload_chamada
from app.extensions import db
from app.models import Empresa, Fila, Local, Perfil, Senha, Tv, Unidade, Usuario
from app.utils.slug import gerar_slug_audio_local

app = create_app("testing")

passos = []


def ok(msg):
    passos.append(msg)
    print("OK:", msg)


# ===========================================================================
# A) Tabela dourada do slug canônico (única implementação, Python/servidor).
# ===========================================================================
CASOS_SLUG = [
    # (nome_exibicao, slug esperado)
    ("COLETA", "coleta"),
    ("Coleta A", "coleta_a"),
    ("Consultorio 01", "consultorio_01"),
    ("Consultório 02", "consultorio_02"),          # acento
    ("Guiche 01", "guiche_01"),
    ("GUICHE 01", "guiche_01"),                     # caixa: colapsa com o de cima (mesma pronúncia)
    ("Recepção 01", "recepcao_01"),                 # acento + cedilha
    ("REC1-01", "rec1_01"),                         # hífen
    ("1º Andar", "1_andar"),                        # ordinal removido
    ("Sala   com   espaços", "sala_com_espacos"),   # espaços múltiplos colapsam num só _
    ("Café-Bar 2", "cafe_bar_2"),                   # acento + hífen + espaço
    ("Ão Éî Çü", "ao_ei_cu"),                       # vários acentos
    ("  Guiche 01  ", "guiche_01"),                 # espaços nas pontas viram nada
    ("", "local"),                                  # vazio -> fallback
    ("!!!", "local"),                               # só pontuação -> fallback
]
for nome, esperado in CASOS_SLUG:
    obtido = gerar_slug_audio_local(nome)
    assert obtido == esperado, f"slug({nome!r}) = {obtido!r}, esperado {esperado!r}"
ok(f"Tabela dourada do slug: {len(CASOS_SLUG)} casos (acento/caixa/hífen/espaço/ordinal/vazio) batem exatamente")

# Confirma que os 7 nomes reais de Local batem com os arquivos .wav de fato no repo.
PASTA_AUDIO = os.path.join(app.static_folder, "audio")
NOMES_LOCAIS_REAIS = ["COLETA", "Coleta A", "Consultorio 01", "Consultório 02", "Guiche 01", "Guiche 02", "Recepção 01"]
for nome in NOMES_LOCAIS_REAIS:
    slug = gerar_slug_audio_local(nome)
    caminho = os.path.join(PASTA_AUDIO, f"local_{slug}.wav")
    assert os.path.exists(caminho), f"esperava arquivo gravado para {nome!r}: local_{slug}.wav"
ok("Os 7 nomes reais de Local resolvem para arquivos local_<slug>.wav que existem no repo")

# O genérico e as frases fixas também têm que existir.
for obrigatorio in ["local_generico.wav", "frase_atencao_senha.wav", "frase_dirija_se.wav"]:
    assert os.path.exists(os.path.join(PASTA_AUDIO, obrigatorio)), f"faltando {obrigatorio}"
ok("Arquivos fixos essenciais (genérico + 2 frases) presentes no repo")


with app.app_context():
    db.create_all()

    perfil_admin = Perfil(nome="admin")
    db.session.add(perfil_admin)
    db.session.commit()

    empresa = Empresa(nome="Clinica Audio")
    db.session.add(empresa)
    db.session.commit()

    # unidade COM uma TV usando o fallback pré-gravado
    unidade_pg = Unidade(empresa_id=empresa.id, nome="Unidade Pregravado")
    # unidade SEM nenhuma TV com o fallback (só TV normal)
    unidade_normal = Unidade(empresa_id=empresa.id, nome="Unidade Normal")
    db.session.add_all([unidade_pg, unidade_normal])
    db.session.commit()

    fila_pg = Fila(empresa_id=empresa.id, unidade_id=unidade_pg.id, nome="Fila PG", sigla="EX")
    db.session.add(fila_pg)
    db.session.commit()

    # Local cujo áudio EXISTE no repo (Guiche 01 -> local_guiche_01.wav)
    local_com_audio = Local(
        empresa_id=empresa.id, unidade_id=unidade_pg.id, nome="Guiche 01",
        nome_exibicao="Guiche 01", identificador_admin="[PG] Guiche 01",
    )
    # Local cujo áudio NÃO existe (Guiche 99 -> local_guiche_99.wav, não gravado)
    local_sem_audio = Local(
        empresa_id=empresa.id, unidade_id=unidade_pg.id, nome="Guiche 99",
        nome_exibicao="Guiche 99", identificador_admin="[PG] Guiche 99",
    )
    db.session.add_all([local_com_audio, local_sem_audio])
    db.session.commit()

    # TVs para a checagem de renderização
    tv_pregravada = Tv(
        empresa_id=empresa.id, unidade_id=unidade_pg.id, nome="TV Pregravada",
        slug="tv-pregravada", usa_audio_pregravado=True,
    )
    tv_normal = Tv(
        empresa_id=empresa.id, unidade_id=unidade_normal.id, nome="TV Normal",
        slug="tv-normal", usa_audio_pregravado=False,
    )
    db.session.add_all([tv_pregravada, tv_normal])
    db.session.commit()

    # senhas para o payload
    senha_com_audio = Senha(
        empresa_id=empresa.id, unidade_id=unidade_pg.id, fila_id=fila_pg.id,
        codigo="EX003", sequencial=3, prioridade=False, status="chamada",
        local_id=local_com_audio.id,
    )
    senha_sem_audio = Senha(
        empresa_id=empresa.id, unidade_id=unidade_pg.id, fila_id=fila_pg.id,
        codigo="EX004", sequencial=4, prioridade=False, status="chamada",
        local_id=local_sem_audio.id,
    )
    senha_sem_local = Senha(
        empresa_id=empresa.id, unidade_id=unidade_pg.id, fila_id=fila_pg.id,
        codigo="EX005", sequencial=5, prioridade=False, status="chamada",
    )
    db.session.add_all([senha_com_audio, senha_sem_audio, senha_sem_local])
    db.session.commit()

    admin = Usuario(empresa_id=empresa.id, nome="Admin Audio", login="admin_audio")
    admin.set_senha("senha123")
    admin.perfis = [perfil_admin]
    db.session.add(admin)
    db.session.commit()

    ids = dict(
        empresa=empresa.id, unidade_pg=unidade_pg.id, unidade_normal=unidade_normal.id,
        senha_com_audio=senha_com_audio.id, senha_sem_audio=senha_sem_audio.id,
        senha_sem_local=senha_sem_local.id,
    )

    # =======================================================================
    # B) Payload inclui local_audio_slug calculado no servidor - só quando o
    #    arquivo existe (senão None).
    # =======================================================================
    p_com = _payload_chamada(db.session.get(Senha, ids["senha_com_audio"]))
    assert p_com["local_audio_slug"] == "guiche_01"
    ok("Payload: local com áudio gravado -> local_audio_slug='guiche_01' (arquivo existe)")

    p_sem = _payload_chamada(db.session.get(Senha, ids["senha_sem_audio"]))
    assert p_sem["local_audio_slug"] is None
    ok("Payload: local SEM áudio gravado (Guiche 99) -> local_audio_slug=None (cai no genérico)")

    p_sem_local = _payload_chamada(db.session.get(Senha, ids["senha_sem_local"]))
    assert p_sem_local["local_audio_slug"] is None
    ok("Payload: senha sem local -> local_audio_slug=None")


# ===========================================================================
# C) Renderização do painel: TV com fallback vs. TV normal (não-regressão).
# ===========================================================================
anon = app.test_client()

resp = anon.get("/painel/tv/tv-pregravada")
assert resp.status_code == 200
html_pg = resp.data.decode("utf-8")
assert "const TV_USA_AUDIO_PREGRAVADO = true;" in html_pg
assert "function falarChamadaPregravada(" in html_pg
assert "const AUDIO_BASE =" in html_pg
ok("TV com fallback: injeta TV_USA_AUDIO_PREGRAVADO=true, AUDIO_BASE e a função de concatenação")

# ordem correta dos segmentos e a rede de segurança do genérico presentes no JS
corpo_js = html_pg[html_pg.index("function segmentosChamadaPregravada"):html_pg.index("function falarChamada(")]
assert "frase_atencao_senha" in corpo_js
assert "letra_" in corpo_js and "numero_" in corpo_js
assert "frase_dirija_se" in corpo_js
assert "local_generico" in corpo_js  # rede de segurança quando não há slug
# genérico só quando NÃO há slug (substitui o par), não anexado depois da preposição
assert "if (dados.local_audio_slug)" in corpo_js
ok("JS monta a ordem correta (frase->letras->números->dirija-se->local) com genérico como fallback sem slug")

resp = anon.get("/painel/tv/tv-normal")
assert resp.status_code == 200
html_normal = resp.data.decode("utf-8")
assert "const TV_USA_AUDIO_PREGRAVADO = false;" in html_normal
# caminho speechSynthesis continua intacto (não-regressão) na TV normal
assert "function vozPtPreferida(" in html_normal
assert "speechSynthesis.speak(" in html_normal
ok("TV normal: TV_USA_AUDIO_PREGRAVADO=false e o caminho speechSynthesis segue intacto (sem regressão)")

# ===========================================================================
# D) Não-duplicação: o disparo de áudio (falarChamada) só acontece a partir do
#    evento 'chamada' ao vivo, nunca na reconstrução de histórico (adendo
#    10.13). Vale para os DOIS caminhos, já que ambos passam pelo mesmo
#    falarChamada() - com a fila real (adendo 10.58, BLUEPRINT.md),
#    socket.on('chamada') só enfileira; quem de fato chama falarChamada() é
#    processarProximaDaFila(), um único ponto de chamada (não duplicado por
#    caminho pré-gravado/speechSynthesis - a ramificação é DENTRO de
#    falarChamada(), não em dois call-sites).
# ===========================================================================
bloco_chamada = html_pg[html_pg.index('socket.on("chamada"'):html_pg.index('Smart TVs ficam com o navegador aberto')]
assert "enfileirarChamada(dados)" in bloco_chamada

def corpo_da_funcao_pg(texto, nome_funcao):
    inicio = texto.index(f"function {nome_funcao}(")
    abre = texto.index("{", inicio)
    profundidade = 0
    i = abre
    while True:
        if texto[i] == "{":
            profundidade += 1
        elif texto[i] == "}":
            profundidade -= 1
            if profundidade == 0:
                return texto[abre:i + 1]
        i += 1

corpo_processar_fila = corpo_da_funcao_pg(html_pg, "processarProximaDaFila")
assert corpo_processar_fila.count("falarChamada(item.dados") == 1
bloco_recarregar = html_pg[html_pg.index("function recarregarHistorico"):html_pg.index("const socket = io(")]
assert "falarChamada" not in bloco_recarregar
ok("Disparo de áudio só a partir do evento 'chamada' ao vivo (via fila), nunca em recarregarHistorico (não duplica em reload/reconexão)")

# ===========================================================================
# E) Aviso no cadastro de Local (Opção A): só quando falta o arquivo E há TV
#    com o fallback ligado na MESMA unidade.
# ===========================================================================
c_admin = app.test_client()
resp = c_admin.post("/login", data={"login": "admin_audio", "senha": "senha123"}, follow_redirects=True)
assert resp.status_code == 200

AVISO = "ainda não há áudio gravado"

# E.1) Local novo SEM áudio, na unidade que TEM TV pré-gravada -> avisa.
resp = c_admin.post(
    f"/admin/locais/novo?empresa_id={ids['empresa']}",
    data={"nome_exibicao": "Sala Nova Sem Audio", "identificador_admin": "[PG] Sala Nova Sem Audio",
          "unidade_id": ids["unidade_pg"]},
    follow_redirects=True,
)
assert resp.status_code == 200
assert AVISO.encode("utf-8") in resp.data
assert b"local_sala_nova_sem_audio.wav" in resp.data  # aponta o arquivo exato esperado
ok("Cadastro: Local sem áudio em unidade com TV pré-gravada -> aviso não-bloqueante com o nome do arquivo esperado")

# E.2) Local novo SEM áudio, na unidade SEM TV pré-gravada -> NÃO avisa.
resp = c_admin.post(
    f"/admin/locais/novo?empresa_id={ids['empresa']}",
    data={"nome_exibicao": "Sala Normal Sem Audio", "identificador_admin": "[NORM] Sala Normal Sem Audio",
          "unidade_id": ids["unidade_normal"]},
    follow_redirects=True,
)
assert resp.status_code == 200
assert AVISO.encode("utf-8") not in resp.data
ok("Cadastro: Local sem áudio em unidade SEM TV pré-gravada -> nenhum aviso (evita ruído)")

# E.3) Local novo COM áudio existente (Coleta A -> local_coleta_a.wav), na unidade
#      com TV pré-gravada -> NÃO avisa (arquivo existe).
resp = c_admin.post(
    f"/admin/locais/novo?empresa_id={ids['empresa']}",
    data={"nome_exibicao": "Coleta A", "identificador_admin": "[PG] Coleta A",
          "unidade_id": ids["unidade_pg"]},
    follow_redirects=True,
)
assert resp.status_code == 200
assert AVISO.encode("utf-8") not in resp.data
ok("Cadastro: Local com áudio já gravado (Coleta A) -> nenhum aviso, mesmo com TV pré-gravada na unidade")

print()
print(f"TODOS OS {len(passos)} TESTES DO ÁUDIO PRÉ-GRAVADO PASSARAM")
