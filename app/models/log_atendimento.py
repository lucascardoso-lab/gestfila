from datetime import datetime

from app.extensions import db

EVENTO_LOG = (
    "gerada", "chamada_senha", "chamada_nome", "inicio_atendimento", "fim_atendimento",
    "realocado", "voltou_aguardar", "encerramento_automatico_sistema",
    "expirada_aguardando",  # adendo 10.77 - 'aguardando' de dia anterior cancelada pelo sistema
)
SETOR_LOG = ("recepcao", "coleta", "outro")


class LogAtendimento(db.Model):
    __tablename__ = "logs_atendimento"

    id = db.Column(db.Integer, primary_key=True)
    senha_id = db.Column(db.Integer, db.ForeignKey("senhas.id"), nullable=False)
    evento = db.Column(db.Enum(*EVENTO_LOG, name="evento_log"), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    usuario_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=True)
    unidade_id = db.Column(db.Integer, db.ForeignKey("unidades.id"), nullable=False)
    setor = db.Column(db.Enum(*SETOR_LOG, name="setor_log"), nullable=True)
    # Snapshot de fila/local NO MOMENTO deste evento específico (adendo 10.62) -
    # nullable de propósito: linhas gravadas antes desta coluna existir ficam NULL
    # pra sempre (nunca preenchidas retroativamente), só linhas novas passam a
    # trazer o valor. Sem isso, o relatório de tempo de atendimento não tinha como
    # saber em qual fila/local um segmento intermediário de uma senha realocada
    # várias vezes de fato ocorreu - só o estado ATUAL da Senha, que já reflete o
    # destino da realocação, não a origem (ver app/relatorios/queries.py).
    fila_id = db.Column(db.Integer, db.ForeignKey("filas.id"), nullable=True)
    local_id = db.Column(db.Integer, db.ForeignKey("locais.id"), nullable=True)

    senha = db.relationship("Senha")
    usuario = db.relationship("Usuario")
    unidade = db.relationship("Unidade")
    fila = db.relationship("Fila")
    local = db.relationship("Local")

    def __repr__(self):
        return f"<LogAtendimento {self.id} {self.evento}>"
