from app.extensions import db
from .associations import usuario_unidade


class Unidade(db.Model):
    __tablename__ = "unidades"

    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey("empresas.id"), nullable=False)
    nome = db.Column(db.String(150), nullable=False)
    sigla = db.Column(db.String(4), nullable=True)
    ativo = db.Column(db.Boolean, default=True, nullable=False)

    empresa = db.relationship("Empresa", back_populates="unidades")
    usuarios = db.relationship("Usuario", secondary=usuario_unidade, back_populates="unidades")

    @property
    def sigla_exibicao(self):
        """Prefixo curto pra distinguir itens de unidades diferentes em listas/selects
        que misturam várias unidades (ex.: '[MAT] Recepção 1-01'). `sigla` é opcional -
        sem ela, cai nos 3 primeiros caracteres do nome como fallback, sempre em
        maiúsculas."""
        return (self.sigla or self.nome[:3]).upper()

    def __repr__(self):
        return f"<Unidade {self.id} {self.nome}>"
