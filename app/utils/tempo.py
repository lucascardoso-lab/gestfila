"""Hora de parede da clínica, independente do fuso do SO do servidor.

Motivação (BLUEPRINT.md, 10.36): decisões de "relógio de parede" da clínica -
o encerramento automático às 23h (10.18) e o horário de funcionamento das filas
(`Fila.aberta_em`, 10.1) - precisam da hora LOCAL DA CLÍNICA, não da hora do
sistema operacional do servidor. Confiar em `datetime.now()` (naive, fuso do SO)
significa que um deploy Linux em UTC (default comum) dispararia o encerramento
das 23:00 às 20:00 de Brasília, e avaliaria filas abertas/fechadas 3h fora. Como
config de fuso do SO é frágil (um redeploy/reconfiguração de infra pode resetar
sem ninguém perceber), o fuso é resolvido AQUI, no código.

`datetime.now(tz)` calcula o instante corrente e o expressa em `tz` - o resultado
é o mesmo independentemente de como o SO está configurado. `_instante_utc()` é um
seam separado só para os testes conseguirem simular "o relógio do servidor está
em tal instante" (ver tests/test_regressao_fuso_horario.py).

`janela_dia_clinica_em_utc()` (10.39) resolve uma variante do mesmo problema:
colunas gravadas em UTC "naive" (`Senha.criada_em`/`chamada_em`, seção 8) não
podem ser comparadas com `func.date(coluna) == datetime.utcnow().date()` para
decidir "isso é de hoje" - isso compara o dia CIVIL EM UTC, não o dia civil da
clínica (a virada de dia UTC cai às 21h de Brasília, 3h antes da meia-noite
real - mesma classe de bug que a 10.36/10.37 corrigiram em outros dois
call-sites, só que aqui a comparação já "parecia" consistente por comparar UTC
com UTC, o que escondia o erro).

`para_horario_clinica()` (10.74) é o ÚNICO ponto de conversão de um valor já
gravado (UTC naive) para o fuso da clínica - para AGRUPAR por dia civil da
clínica (`.date()` depois de converter, nunca antes) ou exibir data/hora
formatada no servidor.

REGRA (10.74): hora/data exibida pelo servidor sempre via
`para_horario_clinica()` ou o filtro Jinja `hora_clinica`
(`formatar_hora_clinica`); nunca `strftime`/`.date()` direto sobre uma coluna
UTC. Datetimes enviados ao JS seguem a convenção já existente: ISO + "Z" (o
navegador converte)."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from flask import current_app, has_app_context

FUSO_PADRAO = "America/Sao_Paulo"


def _nome_fuso():
    # Configurável (Config.FUSO_HORARIO), com default explícito - nunca depende
    # do fuso do SO. Fora de contexto de app (ex.: chamada direta num script),
    # cai no padrão sem quebrar.
    if has_app_context():
        return current_app.config.get("FUSO_HORARIO", FUSO_PADRAO)
    return FUSO_PADRAO


def _instante_utc():
    """Instante corrente como datetime AWARE em UTC - o "relógio do servidor".
    Seam de teste: patchar isto simula o servidor num instante/fuso específico."""
    return datetime.now(ZoneInfo("UTC"))


def agora_na_clinica():
    """Instante atual convertido para o fuso da clínica (aware). Use `.time()`/
    `.hour`/`.weekday()` para as comparações de relógio de parede."""
    return _instante_utc().astimezone(ZoneInfo(_nome_fuso()))


def janela_dia_clinica_em_utc(momento_clinica=None):
    """Início (inclusive) e fim (exclusivo) do dia CIVIL da clínica "agora",
    como instantes UTC NAIVE - para filtrar colunas gravadas em UTC naive
    (`Senha.criada_em`/`chamada_em`, default=datetime.utcnow) por "hoje na
    clínica" com um `BETWEEN`/range, nunca com `func.date(coluna) ==
    algo.date()` (ver motivação no docstring do módulo, 10.39).

    `momento_clinica` opcional (aware, já no fuso da clínica) permite fixar
    "hoje" a partir de um instante específico - por padrão usa `agora_na_clinica()`.
    """
    momento = momento_clinica or agora_na_clinica()
    inicio_local = momento.replace(hour=0, minute=0, second=0, microsecond=0)
    fim_local = inicio_local + timedelta(days=1)
    inicio_utc = inicio_local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    fim_utc = fim_local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    return inicio_utc, fim_utc


def para_horario_clinica(momento):
    """Converte um instante gravado em UTC "naive" (colunas com
    default=datetime.utcnow - seção 8) para o fuso da clínica (aware). Use
    `.date()` no RESULTADO para o dia civil da clínica - `coluna.date()` direto
    dá o dia civil em UTC, que vira às 21h de Brasília (10.74, mesma classe de
    bug do 10.37/10.39/10.54). Aceita também um datetime já aware (só muda o
    fuso). `None` -> `None`."""
    if momento is None:
        return None
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=ZoneInfo("UTC"))
    return momento.astimezone(ZoneInfo(_nome_fuso()))


def formatar_hora_clinica(momento, formato="%H:%M"):
    """Filtro Jinja `hora_clinica` (registrado em create_app, 10.74):
    `{{ coluna_utc | hora_clinica }}` -> "21:50"; com formato:
    `{{ coluna_utc | hora_clinica('%d/%m/%Y %H:%M') }}`. `None` -> "" (o
    template decide o que mostrar no lugar, ex.: "—")."""
    convertido = para_horario_clinica(momento)
    return convertido.strftime(formato) if convertido is not None else ""
