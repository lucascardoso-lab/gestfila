from datetime import datetime

from app.extensions import db

EVENTO_LOG = (
    "gerada", "chamada_senha", "chamada_nome", "inicio_atendimento", "fim_atendimento",
    "realocado", "voltou_aguardar", "encerramento_automatico_sistema",
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

    senha = db.relationship("Senha")
    usuario = db.relationship("Usuario")
    unidade = db.relationship("Unidade")

    def __repr__(self):
        return f"<LogAtendimento {self.id} {self.evento}>"
