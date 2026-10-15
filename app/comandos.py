"""Comandos `flask ...` do sistema (registrados em create_app).

`flask encerrar-pendentes --antes-de-hoje [--dry-run]` (adendos 10.77/10.79):
limpeza imediata do que ficou aberto antes do dia civil de hoje da clínica -
senhas abertas ('aguardando'/'chamada'/'em_atendimento'), turnos ativos e
Locais ocupados - a MESMA função que o middleware roda no 1º request de cada
dia (`encerrar_pendencias_de_dias_anteriores`), numa transação só. Útil no
deploy (pendências acumuladas antes da correção) e se o alerta do dashboard de
Relatórios acusar senhas de dias anteriores.

NÃO depende de `ENCERRAMENTO_SESSOES_ATIVO`: aquele gate só liga/desliga o
gatilho AUTOMÁTICO do middleware (desligado na suíte de testes); o comando é
uma ação explícita de quem o roda e age sempre.
"""
from collections import Counter

import click

from app.extensions import db
from app.middleware.encerramento import encerrar_pendencias_de_dias_anteriores
from app.models.senha import senhas_abertas_de_dias_anteriores
from app.models.turno_ativo import turnos_ativos_de_dias_anteriores
from app.models.usuario import usuarios_com_local_de_dias_anteriores
from app.utils.tempo import para_horario_clinica


def _resumo(senhas):
    """Linhas "dia da clínica | empresa | unidade | fila | status | qtd", ordenadas."""
    from app.models import Empresa, Fila, Unidade

    contagem = Counter(
        (para_horario_clinica(s.criada_em).date(), s.empresa_id, s.unidade_id, s.fila_id, s.status)
        for s in senhas
    )
    empresas = {e.id: e.nome for e in Empresa.query.filter(Empresa.id.in_({c[1] for c in contagem}))}
    unidades = {u.id: u.nome for u in Unidade.query.filter(Unidade.id.in_({c[2] for c in contagem}))}
    filas = {f.id: f.nome for f in Fila.query.filter(Fila.id.in_({c[3] for c in contagem}))}
    for (dia, empresa_id, unidade_id, fila_id, status), qtd in sorted(contagem.items()):
        yield (
            f"  {dia:%d/%m/%Y} | {empresas.get(empresa_id, empresa_id)} | {unidades.get(unidade_id, unidade_id)}"
            f" | {filas.get(fila_id, fila_id)} | {status} | {qtd}"
        )


def _resumo_por_usuario(linhas):
    """Linhas "dia da clínica | empresa | usuário | local" (adendo 10.79) - uma
    por usuário (um turno/local por usuário), ordenadas por dia. `linhas`:
    tuplas (instante UTC naive, usuário, local_id)."""
    from app.models import Empresa, Local

    empresas = {e.id: e.nome for e in Empresa.query.filter(Empresa.id.in_({u.empresa_id for _, u, _ in linhas}))}
    locais = {l.id: l.identificador_admin for l in Local.query.filter(Local.id.in_({lid for _, _, lid in linhas}))}
    for desde, usuario, local_id in sorted(linhas, key=lambda linha: (linha[0], linha[1].id)):
        yield (
            f"  {para_horario_clinica(desde):%d/%m/%Y} | {empresas.get(usuario.empresa_id, '-')}"
            f" | {usuario.nome} (id {usuario.id}) | {locais.get(local_id, local_id)} (id {local_id})"
        )


def register_comandos(app):
    @app.cli.command("encerrar-pendentes")
    @click.option(
        "--antes-de-hoje", is_flag=True,
        help="Obrigatório: encerra só o que é de antes do dia de hoje da clínica.",
    )
    @click.option("--dry-run", is_flag=True, help="Só mostra o que seria encerrado, sem alterar nada.")
    def encerrar_pendentes(antes_de_hoje, dry_run):
        """Encerra senhas, turnos e locais de dias anteriores (adendos 10.77/10.79)."""
        if not antes_de_hoje:
            # Exigido de propósito: o comando nunca encerra nada de HOJE.
            raise click.UsageError("Informe --antes-de-hoje (o comando nunca encerra nada de hoje).")

        pendentes = senhas_abertas_de_dias_anteriores().all()
        click.echo(f"Senhas abertas de dias anteriores: {len(pendentes)}")
        if pendentes:
            click.echo("  dia | empresa | unidade | fila | status | qtd")
            for linha in _resumo(pendentes):
                click.echo(linha)

        turnos = turnos_ativos_de_dias_anteriores().all()
        click.echo(f"Turnos ativos de dias anteriores: {len(turnos)}")
        if turnos:
            click.echo("  iniciado em | empresa | usuário | local")
            for linha in _resumo_por_usuario([(t.iniciado_em, t.usuario, t.local_id) for t in turnos]):
                click.echo(linha)

        ocupantes = usuarios_com_local_de_dias_anteriores().all()
        click.echo(f"Locais ocupados desde dias anteriores: {len(ocupantes)}")
        if ocupantes:
            click.echo("  ocupado desde | empresa | usuário | local")
            for linha in _resumo_por_usuario([(u.local_desde, u, u.local_atual_id) for u in ocupantes]):
                click.echo(linha)

        if dry_run:
            # Só houve SELECT, mas o rollback explícito garante que nada desta
            # sessão é gravado, mesmo que algo acima mude no futuro.
            db.session.rollback()
            click.echo("--dry-run: nada foi alterado (rollback).")
            return

        try:
            encerradas, turnos_desativados, locais_liberados = encerrar_pendencias_de_dias_anteriores()
        except Exception as erro:  # uma transação, um commit no fim
            db.session.rollback()
            raise click.ClickException(f"Falhou e nada foi gravado (rollback): {erro}") from erro
        click.echo(f"Encerradas: {encerradas} (status 'cancelada' + log de auditoria).")
        click.echo(f"Turnos desativados: {turnos_desativados}. Locais liberados: {locais_liberados}.")
