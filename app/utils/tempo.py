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
em tal instante" (ver tests/test_regressao_fuso_horario.py)."""
from datetime import datetime
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
