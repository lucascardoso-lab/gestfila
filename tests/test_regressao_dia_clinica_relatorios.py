"""
Teste de regressão: agrupamento "por dia" e "hoje" no dia civil da CLÍNICA,
não de UTC (adendo 10.74 - mesma classe de bug do 10.37/10.39/10.54).

Antes: `tempo_medio_espera` e `volume_senhas` agrupavam por
`timestamp.date()`/`criada_em.date()` (dia em UTC) e os KPIs "hoje" do
dashboard usavam `datetime.utcnow().date()` - a virada do dia UTC cai às 21h
de Brasília, então uma senha das 21h50 aparecia no dia SEGUINTE e os KPIs
zeravam às 21h.

Relógio simulado (patch de app.utils.tempo._instante_utc, mesmo padrão de
test_regressao_fuso_horario.py) em 09/10/2026 01:00 UTC = 08/10 22:00 de
Brasília. Senhas:
  A - 08/10 21:50 Brasília (09/10 00:50 UTC) -> dia 08/10
  B - 08/10 12:00 Brasília (08/10 15:00 UTC) -> dia 08/10
  C - 07/10 23:30 Brasília (08/10 02:30 UTC) -> dia 07/10

Execução: python -m tests.test_regressao_dia_clinica_relatorios (a partir da
raiz do projeto, com o virtualenv ativado).
"""
import os

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

import io
from datetime import date, datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd

from app import create_app
from app.core.dashboard_queries import calcular_kpis, chamadas_por_hora_hoje
from app.extensions import db
from app.models import Empresa, Fila, Local, LogAtendimento, Perfil, Senha, Unidade, Usuario
from app.utils.tempo import formatar_hora_clinica, para_horario_clinica

UTC = ZoneInfo("UTC")
SP = ZoneInfo("America/Sao_Paulo")
app = create_app("testing")

passos = []


def ok(msg):
    passos.append(msg)
    print("OK:", msg)


def relogio_22h_brasilia():
    return patch("app.utils.tempo._instante_utc", return_value=datetime(2026, 10, 9, 1, 0, tzinfo=UTC))


# ===========================================================================
# 0) para_horario_clinica: único ponto de conversão UTC -> clínica.
# ===========================================================================
with app.app_context():
    convertido = para_horario_clinica(datetime(2026, 10, 9, 0, 50))
    assert convertido == datetime(2026, 10, 8, 21, 50, tzinfo=SP) and convertido.date() == date(2026, 10, 8)
    assert para_horario_clinica(datetime(2026, 10, 9, 0, 50, tzinfo=UTC)) == convertido
    assert para_horario_clinica(None) is None
ok("para_horario_clinica: 09/10 00:50 UTC (naive ou aware) -> 08/10 21:50 em Brasília; None -> None")

with app.app_context():
    db.create_all()
    perfil = Perfil(nome="admin")
    empresa = Empresa(nome="Clinica Fuso")
    db.session.add_all([perfil, empresa])
    db.session.commit()
    unidade = Unidade(empresa_id=empresa.id, nome="Matriz", sigla="MAT")
    db.session.add(unidade)
    db.session.commit()
    fila = Fila(empresa_id=empresa.id, unidade_id=unidade.id, nome="Recepção")
    db.session.add(fila)
    db.session.commit()
    admin = Usuario(empresa_id=empresa.id, nome="Admin", login="admin_fuso")
    admin.set_senha("senha123")
    admin.perfis = [perfil]
    db.session.add(admin)
    db.session.commit()

    def senha_com_espera(codigo, criada_utc, minutos_espera):
        s = Senha(
            empresa_id=empresa.id, unidade_id=unidade.id, fila_id=fila.id, codigo=codigo,
            sequencial=1, prioridade=False, status="chamada", criada_em=criada_utc,
        )
        db.session.add(s)
        db.session.commit()
        for evento, momento in (("gerada", criada_utc),
                                ("chamada_senha", criada_utc + timedelta(minutes=minutos_espera))):
            db.session.add(LogAtendimento(
                senha_id=s.id, evento=evento, timestamp=momento, usuario_id=admin.id,
                unidade_id=unidade.id, fila_id=fila.id, setor="recepcao",
            ))
        db.session.commit()

    senha_com_espera("A", datetime(2026, 10, 9, 0, 50), 5)    # 08/10 21:50 Brasília
    senha_com_espera("B", datetime(2026, 10, 8, 15, 0), 10)   # 08/10 12:00 Brasília
    senha_com_espera("C", datetime(2026, 10, 8, 2, 30), 10)   # 07/10 23:30 Brasília
    ids = dict(empresa=empresa.id, unidade=unidade.id)

c = app.test_client()
assert c.post("/login", data={"login": "admin_fuso", "senha": "senha123"}, follow_redirects=True).status_code == 200
PERIODO = "data_inicio=2026-10-07&data_fim=2026-10-08"


def csv(rota):
    with relogio_22h_brasilia():
        resp = c.get(f"/relatorios/{rota}?{PERIODO}&formato=csv")
    assert resp.status_code == 200, resp.status_code
    resp.direct_passthrough = False
    return pd.read_csv(io.StringIO(resp.get_data().decode("utf-8-sig")))


# ===========================================================================
# 1) Volume: senha das 21h50 no dia 08/10, a das 23h30 do dia 07 no dia 07.
# ===========================================================================
volume = csv("volume")
por_dia = dict(zip(volume["data"], volume["total"]))
assert por_dia == {"2026-10-07": 1, "2026-10-08": 2}, por_dia
ok("Volume: a senha das 21h50 conta no dia 08/10 (não 09/10) e a das 23h30 de 07/10 no dia 07/10")

# ===========================================================================
# 2) Tempo médio de espera: mesmo agrupamento pelo dia da clínica.
# ===========================================================================
espera = csv("tempo-espera")
linhas = {d: (q, t) for d, q, t in zip(espera["data"], espera["quantidade"], espera["tempo_medio_segundos"])}
assert linhas == {"2026-10-07": (1, 600.0), "2026-10-08": (2, 450.0)}, linhas
ok("Tempo médio de espera: esperas abertas às 21h50 e 12h00 no dia 08/10 (média 450 s); a das 23h30 no dia 07/10")

# ===========================================================================
# 3) KPIs "hoje" do dashboard às 22h de Brasília.
# ===========================================================================
with app.app_context(), relogio_22h_brasilia():
    empresa = db.session.get(Empresa, ids["empresa"])
    kpis = calcular_kpis(empresa, [ids["unidade"]])
    horas = chamadas_por_hora_hoje([ids["unidade"]])
assert kpis["chamadas_hoje"] == 2, kpis  # A e B; a chamada de C foi 23h40 de 07/10
assert kpis["tempo_medio_espera_segundos"] == 450, kpis
assert sorted(horas) == ["2026-10-08T15:10:00", "2026-10-09T00:55:00"], horas
ok("KPIs às 22h: 'hoje' = 08/10 da clínica (2 chamadas, espera média 450 s); não zera às 21h")
ok("Gráfico por hora do dashboard recebe as chamadas do dia da clínica (inclusive a das 21h55)")

# ===========================================================================
# 4) Filtro Jinja hora_clinica + as duas telas que formatavam hora UTC no
#    servidor (mostravam 3h a mais).
# ===========================================================================
with app.app_context():
    assert formatar_hora_clinica(datetime(2026, 10, 9, 0, 50)) == "21:50"
    assert formatar_hora_clinica(datetime(2026, 10, 9, 0, 50), "%d/%m/%Y %H:%M") == "08/10/2026 21:50"
    assert formatar_hora_clinica(None) == ""
    assert app.jinja_env.filters["hora_clinica"] is formatar_hora_clinica
ok("Filtro hora_clinica: formato padrão %H:%M, formato opcional e None -> ''")

with relogio_22h_brasilia():
    pagina = c.get("/relatorios/dashboard").get_data(as_text=True)
assert "<td>08/10 21:55:00</td>" in pagina, "hora da chamada A no fuso da clínica"
assert "09/10 00:55:00" not in pagina
ok("relatorios/dashboard 'Últimas chamadas': chamada das 00:55 UTC aparece como 08/10 21:55:00")

with app.app_context():
    local = Local(empresa_id=ids["empresa"], unidade_id=ids["unidade"], nome="Guichê 1",
                  nome_exibicao="Guichê 1", identificador_admin="[MAT] Guichê 1")
    db.session.add(local)
    db.session.commit()
    operador = Usuario.query.filter_by(login="admin_fuso").one()
    operador.local_atual_id = local.id
    operador.local_desde = datetime(2026, 10, 9, 0, 40)  # gravado com utcnow() - 08/10 21:40 Brasília
    db.session.commit()
with relogio_22h_brasilia():
    pagina = c.get("/admin/locais/ocupacao").get_data(as_text=True)
assert "<td>08/10/2026 21:40</td>" in pagina, "coluna Desde no fuso da clínica"
assert "09/10/2026 00:40" not in pagina
ok("admin/locais/ocupacao 'Desde': local_desde 00:40 UTC aparece como 08/10/2026 21:40")

print(f"\n{len(passos)} verificações OK")
