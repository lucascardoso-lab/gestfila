"""
Teste de regressão: campos De/Até dos relatórios (pedido do cliente, print de
"Tempo médio de espera" mostrando os campos sem calendário).

Três mudanças, aplicadas nas 4 telas que usam o filtro De/Até (compartilham o
mesmo partial `relatorios/_macros.html::form_filtros` e o mesmo resolvedor
`relatorios/helpers.py::resolver_filtros` - corrigido uma vez só, não tela por
tela):

1. type="text" -> type="date" (calendário nativo do navegador, sem lib nova).
2. Sem filtro na querystring, o padrão passa a ser HOJE (De = Até = dia CIVIL
   da clínica, via agora_na_clinica() - seção 10.37) em vez do intervalo de 30
   dias que existia antes.
3. `data_inicio`/`data_fim_exclusivo` (usados para comparar com
   `Senha.criada_em`, gravado em UTC naive) passam pela mesma conversão
   fuso->UTC de `janela_dia_clinica_em_utc()` (10.39/10.44) em vez de um
   `datetime.combine()` naive direto - sem isso seria o MESMO bug de 3h que a
   10.39 já corrigiu uma vez, reaberto neste call-site (uma senha criada às
   22h de Brasília cairia fora do filtro "hoje", porque em UTC já seria
   madrugada do dia seguinte).

`relatorios/dashboard.html` (dashboard operacional) NÃO tem esse filtro (não
usa resolver_filtros/form_filtros - é uma visão "agora", sem período) - fora
do escopo por não ter o campo em questão.

Relógio do servidor SIMULADO (mesmo padrão de tests/test_regressao_fuso_
horario.py, patch de app.utils.tempo._instante_utc) - não usa datetime.utcnow()
"puro" tanto na fixture quanto nas requisições, porque comparar contra um
"agora" não-sincronizado com o relógio que o servidor realmente usa deixaria
o teste flaky justo na janela 21h-23:59 de Brasília (exatamente a janela em
que esse bug se manifesta - mesma confusão real já vista na 10.39).

Execução: python -m tests.test_regressao_relatorios_filtro_data (a partir da
raiz do projeto, com o virtualenv ativado).
"""
import os
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

from app import create_app
from app.extensions import db
from app.models import Empresa, Fila, Perfil, Senha, Unidade, Usuario

app = create_app("testing")

UTC = ZoneInfo("UTC")

passos = []


def ok(msg):
    passos.append(msg)
    print("OK:", msg)


def servidor_em_utc(ano, mes, dia, hora, minuto):
    """Mesmo helper de test_regressao_fuso_horario.py - finge que o relógio do
    servidor está nesse instante UTC, simulando um deploy Linux em UTC."""
    return patch("app.utils.tempo._instante_utc", return_value=datetime(ano, mes, dia, hora, minuto, tzinfo=UTC))


# "Agora" simulado: 2026-06-16 01:00 UTC = 2026-06-15 22:00 em Brasília (UTC-3,
# sem horário de verão desde 2019) - deliberadamente dentro da janela 21h-23:59
# de Brasília, onde o dia civil da clínica (15/06) já diverge do dia civil em
# UTC (16/06) - é exatamente essa divergência que expõe o bug de 3h se
# resolver_filtros() não converter fuso antes de comparar com Senha.criada_em.
HOJE_CLINICA = "2026-06-15"
CRIADA_HOJE_UTC = datetime(2026, 6, 16, 1, 0)  # mesmo instante do "agora" simulado
CRIADA_ANTIGA_UTC = CRIADA_HOJE_UTC - timedelta(days=5)

with app.app_context():
    db.create_all()

    perfil_admin = Perfil(nome="admin")
    db.session.add(perfil_admin)
    db.session.commit()

    empresa = Empresa(nome="Clinica Filtro Data")
    db.session.add(empresa)
    db.session.commit()

    unidade = Unidade(empresa_id=empresa.id, nome="Matriz")
    db.session.add(unidade)
    db.session.commit()

    fila = Fila(empresa_id=empresa.id, unidade_id=unidade.id, nome="Fila Data", sigla="FD")
    db.session.add(fila)
    db.session.commit()

    # senha_hoje: criada às 22h de Brasília (dia civil 15/06) - com o bug de 3h
    # (datetime.combine sem conversão de fuso), o filtro "hoje" (De=Até=15/06,
    # naive) excluiria essa senha, porque em UTC ela já é madrugada de 16/06.
    senha_hoje = Senha(
        empresa_id=empresa.id, unidade_id=unidade.id, fila_id=fila.id,
        codigo="FD001", sequencial=1, prioridade=False, status="aguardando",
        criada_em=CRIADA_HOJE_UTC,
    )
    senha_antiga = Senha(
        empresa_id=empresa.id, unidade_id=unidade.id, fila_id=fila.id,
        codigo="FD002", sequencial=2, prioridade=False, status="aguardando",
        criada_em=CRIADA_ANTIGA_UTC,
    )
    db.session.add_all([senha_hoje, senha_antiga])
    db.session.commit()

    u = Usuario(empresa_id=empresa.id, nome="admin_filtro_data", login="admin_filtro_data")
    u.set_senha("senha123")
    u.perfis = [perfil_admin]
    db.session.add(u)
    db.session.commit()

    ids = dict(empresa=empresa.id)


def linhas_da_tabela(texto):
    """Conta <tr> dentro do <tbody> - "Fila Data" também aparece no <select>
    de filtro (option), então contar ocorrências no texto inteiro dá falso
    positivo; isola só o corpo da tabela de resultados."""
    if "<tbody>" not in texto:
        return 0
    corpo = texto[texto.index("<tbody>"):texto.index("</tbody>")]
    return corpo.count("<tr>")


def login_como(login):
    c = app.test_client()
    resp = c.post("/login", data={"login": login, "senha": "senha123"}, follow_redirects=True)
    assert resp.status_code == 200, (login, resp.status_code)
    return c


c = login_como("admin_filtro_data")

with servidor_em_utc(2026, 6, 16, 1, 0):
    # ===========================================================================
    # 1) type="date" presente nas 4 telas que usam o filtro De/Até - sem mais
    #    type="text" pros campos data_inicio/data_fim em nenhuma delas.
    # ===========================================================================
    telas = {
        "tempo_espera": "/relatorios/tempo-espera",
        "tempo_atendimento": "/relatorios/tempo-atendimento",
        "volume": "/relatorios/volume",
        "ranking": "/relatorios/ranking-operadores",
    }

    for nome_tela, url in telas.items():
        resp = c.get(url)
        assert resp.status_code == 200, (nome_tela, resp.status_code)
        texto = resp.data.decode("utf-8")
        assert 'id="data_inicio" name="data_inicio"' in texto, nome_tela
        assert 'type="date" id="data_inicio"' in texto, nome_tela
        assert 'type="date" id="data_fim"' in texto, nome_tela
        ok(f"{nome_tela}: campos De/Até renderizam como type=\"date\"")

    # ===========================================================================
    # 2) Sem filtro na querystring, com o relógio simulado às 22h de Brasília
    #    (15/06): a senha criada nesse instante (madrugada de 16/06 em UTC) DEVE
    #    aparecer - é o caso que travaria o bug de 3h se resolver_filtros() não
    #    convertesse fuso antes de comparar. A antiga (5 dias antes) fica de fora.
    # ===========================================================================
    resp = c.get("/relatorios/volume")
    assert resp.status_code == 200
    texto = resp.data.decode("utf-8")
    assert linhas_da_tabela(texto) == 1, "esperava 1 linha (só a senha de hoje, 22h Brasília)"
    ok("volume sem filtro (relógio simulado 22h Brasília): mostra a senha criada às 22h - não exclui por bug de 3h")

    # Campos De/Até vêm pré-preenchidos com o dia CIVIL da clínica (15/06), não
    # o dia em UTC (16/06) - prova direta de que o padrão usa agora_na_clinica().
    assert f'value="{HOJE_CLINICA}"' in texto
    ok(f"volume sem filtro: campos De/Até vêm preenchidos com o dia civil da clínica ({HOJE_CLINICA}), não o dia em UTC")

    # ===========================================================================
    # 3) Filtro explícito continua funcionando como antes: intervalo cobrindo as
    #    duas datas mostra as duas linhas (dias diferentes -> grupos diferentes).
    # ===========================================================================
    data_inicio = "2026-06-09"
    data_fim = HOJE_CLINICA
    resp = c.get(f"/relatorios/volume?data_inicio={data_inicio}&data_fim={data_fim}")
    assert resp.status_code == 200
    texto = resp.data.decode("utf-8")
    assert linhas_da_tabela(texto) == 2, "esperava 2 linhas (hoje + antiga) com o intervalo de 6 dias"
    ok("volume com filtro explícito (6 dias): mostra as duas linhas, comportamento inalterado")

print()
print(f"TODOS OS {len(passos)} TESTES DE FILTRO DE DATA DOS RELATÓRIOS PASSARAM")
