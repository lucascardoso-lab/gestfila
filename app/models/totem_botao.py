from app.extensions import db


class TotemBotao(db.Model):
    __tablename__ = "totem_botoes"

    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey("empresas.id"), nullable=False)
    unidade_id = db.Column(db.Integer, db.ForeignKey("unidades.id"), nullable=False)
    parent_id = db.Column(db.Integer, db.ForeignKey("totem_botoes.id"), nullable=True)
    servico_id = db.Column(db.Integer, db.ForeignKey("servicos.id"), nullable=True)  # legado - não usar
    fila_id = db.Column(db.Integer, db.ForeignKey("filas.id"), nullable=True)
    label = db.Column(db.String(120), nullable=False)
    ordem = db.Column(db.Integer, default=0, nullable=False)
    ativo = db.Column(db.Boolean, default=True, nullable=False)

    empresa = db.relationship("Empresa")
    unidade = db.relationship("Unidade")
    pai = db.relationship("TotemBotao", remote_side=[id], back_populates="filhos")
    filhos = db.relationship("TotemBotao", back_populates="pai")
    servico = db.relationship("Servico")  # legado - não usar em código novo
    fila = db.relationship("Fila")

    def __repr__(self):
        return f"<TotemBotao {self.id} {self.label}>"
