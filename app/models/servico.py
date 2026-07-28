from app.extensions import db


class Servico(db.Model):
    __tablename__ = "servicos"

    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey("empresas.id"), nullable=False)
    unidade_id = db.Column(db.Integer, db.ForeignKey("unidades.id"), nullable=False)
    nome = db.Column(db.String(150), nullable=False)
    sigla = db.Column(db.String(4), nullable=False)
    digitos_sequencial = db.Column(db.SmallInteger, default=3, nullable=False)
    zera_diariamente = db.Column(db.Boolean, default=True, nullable=False)
    prioridade_padrao = db.Column(db.SmallInteger, default=0, nullable=False)
    ativo = db.Column(db.Boolean, default=True, nullable=False)

    empresa = db.relationship("Empresa", back_populates="servicos")
    unidade = db.relationship("Unidade")

    __table_args__ = (
        db.UniqueConstraint("unidade_id", "sigla", name="uq_servico_unidade_sigla"),
    )

    def __repr__(self):
        return f"<Servico {self.id} {self.sigla}>"
