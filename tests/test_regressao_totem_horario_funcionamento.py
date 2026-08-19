"""
Teste de regressão (adendo novo, ver BLUEPRINT): botões de fila fora do
horário de funcionamento (seção 10.1, `Fila.aberta_em()`) ficam escondidos no
totem (`hidden`), em vez de clicáveis sem funcionar - e a tela se atualiza
SOZINHA (endpoint de poll, `app/totem/routes.py::status_visibilidade_filas`),
sem depender de F5.

Investigação que motivou o desenho (não presumido): não existe reimplementação
de `Fila.aberta_em()` em JavaScript neste projeto (os widgets "Atendendo
agora" da seção 10.10 só embutem um booleano JÁ calculado pelo servidor) - e o
totem é multi-página (cada submenu é uma navegação separada), então a cascata
de um botão-menu (visível se algum descendente-folha, em qualquer
profundidade, estiver aberto) não pode ser calculada no cliente sem os dados
de páginas ainda não carregadas. Por isso o cálculo é 100% do servidor
(`_status_visibilidade_botoes`), embutido no HTML inicial E servido de novo a
cada poll - sem nenhuma lógica de dia/período duplicada em JS, sem depender do
relógio do navegador do totem.

Cobre (estrutural, sem runtime de navegador - mesma limitação de sempre
deste projeto): fila com horário configurado, dentro do horário agora -
botão visível; fora do horário - escondido; fila sem horario_funcionamento -
sempre visível; botão-menu com TODOS os filhos fora do horário - também
escondido; botão-menu com PELO MENOS UM filho dentro do horário - continua
visível; presença do embutimento inicial + setInterval no HTML; endpoint de
poll responde com o mesmo cálculo.

O relógio é SIMULADO (mesmo padrão de test_regressao_fuso_horario.py, patch
de app.utils.tempo._instante_utc) numa segunda-feira de manhã em Brasília -
horário escolhido de propósito longe de qualquer virada de dia/DST.

Execução: python -m tests.test_regressao_totem_horario_funcionamento (a
partir da raiz do projeto, com o virtualenv ativado).
"""
import os
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

from app import create_app
from app.extensions import db
from app.models import Empresa, Fila, Totem, TotemBotao, Unidade

app = create_app("testing")

UTC = ZoneInfo("UTC")

passos = []


def ok(msg):
    passos.append(msg)
    print("OK:", msg)


# 2024-01-08 (segunda-feira) 13:00 UTC = 10:00 em Brasília (UTC-3, sem DST
# desde 2019) - manhã (hour < 12), longe de qualquer virada de dia.
SEGUNDA_MANHA_BRASILIA = patch(
    "app.utils.tempo._instante_utc", return_value=datetime(2024, 1, 8, 13, 0, tzinfo=UTC)
)

HORARIO_ABERTO_SEGUNDA_MANHA = {"segunda": {"manha": True, "tarde": False}}
HORARIO_FECHADO_SEGUNDA_MANHA = {"segunda": {"manha": False, "tarde": True}}

with app.app_context():
    db.create_all()

    empresa = Empresa(nome="Clinica Horario Totem")
    db.session.add(empresa)
    db.session.commit()

    unidade = Unidade(empresa_id=empresa.id, nome="Unidade 1")
    db.session.add(unidade)
    db.session.commit()

    fila_aberta = Fila(
        empresa_id=empresa.id, unidade_id=unidade.id, nome="Fila Aberta",
        horario_funcionamento=dict(HORARIO_ABERTO_SEGUNDA_MANHA),
    )
    fila_fechada = Fila(
        empresa_id=empresa.id, unidade_id=unidade.id, nome="Fila Fechada",
        horario_funcionamento=dict(HORARIO_FECHADO_SEGUNDA_MANHA),
    )
    fila_sem_config = Fila(
        empresa_id=empresa.id, unidade_id=unidade.id, nome="Fila Sem Config",
        horario_funcionamento={},
    )
    db.session.add_all([fila_aberta, fila_fechada, fila_sem_config])
    db.session.commit()

    # Botões-folha diretos na raiz.
    botao_aberto = TotemBotao(
        empresa_id=empresa.id, unidade_id=unidade.id, label="Aberta", fila_id=fila_aberta.id, sigla="ABT", ordem=1,
    )
    botao_fechado = TotemBotao(
        empresa_id=empresa.id, unidade_id=unidade.id, label="Fechada", fila_id=fila_fechada.id, sigla="FEC", ordem=2,
    )
    botao_sem_config = TotemBotao(
        empresa_id=empresa.id, unidade_id=unidade.id, label="Sem Config", fila_id=fila_sem_config.id, sigla="SCF", ordem=3,
    )
    db.session.add_all([botao_aberto, botao_fechado, botao_sem_config])
    db.session.commit()

    # Botão-menu com TODOS os filhos fechados - deve ficar escondido também.
    menu_todos_fechados = TotemBotao(empresa_id=empresa.id, unidade_id=unidade.id, label="Menu Todos Fechados", ordem=4)
    db.session.add(menu_todos_fechados)
    db.session.commit()
    filho_fechado_1 = TotemBotao(
        empresa_id=empresa.id, unidade_id=unidade.id, label="Filho Fechado 1", parent_id=menu_todos_fechados.id,
        fila_id=fila_fechada.id, sigla="FC1", ordem=1,
    )
    filho_fechado_2 = TotemBotao(
        empresa_id=empresa.id, unidade_id=unidade.id, label="Filho Fechado 2", parent_id=menu_todos_fechados.id,
        fila_id=fila_fechada.id, sigla="FC2", ordem=2,
    )
    db.session.add_all([filho_fechado_1, filho_fechado_2])
    db.session.commit()

    # Botão-menu com PELO MENOS UM filho aberto - continua visível.
    menu_misto = TotemBotao(empresa_id=empresa.id, unidade_id=unidade.id, label="Menu Misto", ordem=5)
    db.session.add(menu_misto)
    db.session.commit()
    filho_misto_aberto = TotemBotao(
        empresa_id=empresa.id, unidade_id=unidade.id, label="Filho Misto Aberto", parent_id=menu_misto.id,
        fila_id=fila_aberta.id, sigla="FMA", ordem=1,
    )
    filho_misto_fechado = TotemBotao(
        empresa_id=empresa.id, unidade_id=unidade.id, label="Filho Misto Fechado", parent_id=menu_misto.id,
        fila_id=fila_fechada.id, sigla="FMF", ordem=2,
    )
    db.session.add_all([filho_misto_aberto, filho_misto_fechado])
    db.session.commit()

    # Botão-menu puramente navegacional, sem NENHUM descendente-folha - nunca
    # deve ser escondido por esta lógica (nada aqui pra avaliar).
    menu_sem_folhas = TotemBotao(empresa_id=empresa.id, unidade_id=unidade.id, label="Menu Sem Folhas", ordem=6)
    db.session.add(menu_sem_folhas)
    db.session.commit()
    submenu_vazio = TotemBotao(
        empresa_id=empresa.id, unidade_id=unidade.id, label="Submenu Vazio", parent_id=menu_sem_folhas.id, ordem=1,
    )
    db.session.add(submenu_vazio)
    db.session.commit()

    totem = Totem(empresa_id=empresa.id, unidade_id=unidade.id, nome="Totem 1", slug="totem-horario-funcionamento")
    db.session.add(totem)
    db.session.commit()

    ids = dict(
        empresa=empresa.id, unidade=unidade.id,
        fila_aberta=fila_aberta.id, fila_fechada=fila_fechada.id, fila_sem_config=fila_sem_config.id,
        botao_aberto=botao_aberto.id, botao_fechado=botao_fechado.id, botao_sem_config=botao_sem_config.id,
        menu_todos_fechados=menu_todos_fechados.id, filho_fechado_1=filho_fechado_1.id, filho_fechado_2=filho_fechado_2.id,
        menu_misto=menu_misto.id, filho_misto_aberto=filho_misto_aberto.id, filho_misto_fechado=filho_misto_fechado.id,
        menu_sem_folhas=menu_sem_folhas.id, submenu_vazio=submenu_vazio.id,
        totem_slug=totem.slug,
    )

c = app.test_client()

with SEGUNDA_MANHA_BRASILIA:
    resp = c.get(f"/totem/{ids['totem_slug']}")
    assert resp.status_code == 200
    texto = resp.data.decode("utf-8")
    texto_compacto = texto.replace(" ", "")  # tojson pode inserir espaço após ":" - normaliza antes de comparar

    # ===========================================================================
    # 1) Fila dentro do horário agora -> botão visível (não marcado hidden no
    #    HTML embutido inicial, item "STATUS_INICIAL" reflete True).
    # ===========================================================================
    assert f'"{ids["botao_aberto"]}":true' in texto_compacto
    ok("Fila dentro do horário agora: STATUS_INICIAL marca o botão como visível (true)")

    # ===========================================================================
    # 2) Fila fora do horário agora -> botão escondido (STATUS_INICIAL false).
    # ===========================================================================
    assert f'"{ids["botao_fechado"]}":false' in texto_compacto
    ok("Fila fora do horário agora: STATUS_INICIAL marca o botão como escondido (false)")

    # ===========================================================================
    # 3) Fila SEM horario_funcionamento configurado -> sempre visível (ausência
    #    de configuração nunca esconde, tratado explicitamente como aberta).
    # ===========================================================================
    assert f'"{ids["botao_sem_config"]}":true' in texto_compacto
    ok("Fila sem horario_funcionamento configurado: sempre visível (fallback 'sempre aberta')")

    # ===========================================================================
    # 4) Botão-menu com TODOS os filhos fora do horário -> também escondido.
    # ===========================================================================
    assert f'"{ids["menu_todos_fechados"]}":false' in texto_compacto
    ok("Botão-menu com TODOS os descendentes-folha fora do horário: também escondido")

    # ===========================================================================
    # 5) Botão-menu com PELO MENOS UM filho dentro do horário -> continua visível.
    # ===========================================================================
    assert f'"{ids["menu_misto"]}":true' in texto_compacto
    ok("Botão-menu com PELO MENOS UM descendente-folha dentro do horário: continua visível")

    # ===========================================================================
    # 6) Botão-menu SEM nenhum descendente-folha (puramente navegacional) ->
    #    nunca escondido por esta lógica.
    # ===========================================================================
    assert f'"{ids["menu_sem_folhas"]}":true' in texto_compacto
    ok("Botão-menu sem nenhum descendente-folha: nunca escondido (nada a avaliar)")

    # ===========================================================================
    # 7) data-botao-id presente nos elementos certos, e a lógica de atualização
    #    periódica (setInterval, sem depender de F5) está no HTML.
    # ===========================================================================
    assert f'data-botao-id="{ids["botao_aberto"]}"' in texto
    assert f'data-botao-id="{ids["botao_fechado"]}"' in texto
    assert f'data-botao-id="{ids["menu_todos_fechados"]}"' in texto
    ok("data-botao-id presente nos botões-folha e nos botões-menu renderizados")

    assert "setInterval(atualizar, 45000)" in texto
    assert "function aplicarStatus(" in texto
    ok("JS de atualização periódica (setInterval, ~45s) presente no HTML, sem depender de F5")

    # ===========================================================================
    # 8) Endpoint de poll responde com o MESMO cálculo (mesma função reaproveitada).
    # ===========================================================================
    resp_poll = c.get(f"/totem/{ids['totem_slug']}/status-visibilidade-filas")
    assert resp_poll.status_code == 200
    status_poll = resp_poll.get_json()
    assert status_poll[str(ids["botao_aberto"])] is True
    assert status_poll[str(ids["botao_fechado"])] is False
    assert status_poll[str(ids["botao_sem_config"])] is True
    assert status_poll[str(ids["menu_todos_fechados"])] is False
    assert status_poll[str(ids["menu_misto"])] is True
    assert status_poll[str(ids["menu_sem_folhas"])] is True
    ok("Endpoint de poll (status-visibilidade-filas) responde com o mesmo cálculo do embutido inicial")

print()
print(f"TODOS OS {len(passos)} TESTES DE HORARIO DE FUNCIONAMENTO NO TOTEM PASSARAM")
