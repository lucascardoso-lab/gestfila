from datetime import datetime

from app.extensions import db
from app.utils.tempo import janela_dia_clinica_em_utc


class TurnoAtivo(db.Model):
    """Configuração de turno do operador (seção 10.19) - filas (com peso de rodízio)
    e Local escolhidos por ELE MESMO ao iniciar o expediente, na tela 'Iniciar Turno'
    (inspirada na tela 'Alocar fila' de um sistema concorrente, print de referência
    fornecido junto ao pedido) - complementa o rodízio admin-configurado por Local
    (`LocalRodizioFila`, adendo 10.8): quando este operador tem um turno ativo para o
    Local em questão, `filas_config` manda no rodízio (ver
    `app/atendimento/helpers.py::_itens_rotacao`); sem turno ativo, comportamento
    idêntico a antes (usa `Local.rodizio_ordenado`).

    Uma linha por usuário (`unique=True` em `usuario_id`) - reiniciar o turno
    atualiza a mesma linha em vez de acumular histórico; não há necessidade de
    guardar turnos passados, só o estado atual (`ativo` ou não). Isso também é o que
    permite recuperar o turno após reload/crash (adendo 10.11): a tela 'Iniciar
    Turno' e as rotas por-Local sempre consultam esta linha, nunca um estado só em
    memória do navegador.

    `filas_config` (JSON): lista de dicts, cada um
    `{"fila_id": int, "ordem": int, "chamadas_seguidas": int}` - mesmo par de campos
    já usado por `LocalRodizioFila`, só que guardado como JSON em vez de linhas
    próprias, já que aqui é configuração de UM operador, não do Local em si. Filas
    repetidas ao montar a lista (o operador pode adicionar a mesma fila mais de uma
    vez, só para ajustar o peso) são mescladas em uma única entrada antes de salvar
    (soma de `chamadas_seguidas`) - evita ambiguidade no motor de rodízio, que
    identifica a fila "da vez" pelo `fila_id` (ver `_proximo_item_na_rotacao`).
    """

    __tablename__ = "turnos_ativos"

    id = db.Column(db.Integer, primary_key=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False, unique=True)
    local_id = db.Column(db.Integer, db.ForeignKey("locais.id"), nullable=False)
    filas_config = db.Column(db.JSON, nullable=False)
    iniciado_em = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    ativo = db.Column(db.Boolean, default=True, nullable=False)

    usuario = db.relationship("Usuario", backref=db.backref("turno_ativo", uselist=False))
    local = db.relationship("Local")

    def __repr__(self):
        return f"<TurnoAtivo usuario={self.usuario_id} local={self.local_id} ativo={self.ativo}>"


def turnos_ativos_de_dias_anteriores():
    """Query (não executada) dos turnos ainda `ativo` iniciados ANTES do dia
    civil de hoje da clínica (adendo 10.79) - `iniciado_em` é UTC naive, limite
    pela janela do 10.39. Base única da varredura do middleware e do `flask
    encerrar-pendentes`."""
    inicio_hoje, _fim = janela_dia_clinica_em_utc()
    return TurnoAtivo.query.filter(TurnoAtivo.ativo.is_(True), TurnoAtivo.iniciado_em < inicio_hoje)
