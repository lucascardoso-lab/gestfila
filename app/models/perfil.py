from app.extensions import db
from .associations import usuario_perfil


class Perfil(db.Model):
    __tablename__ = "perfis"

    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(30), unique=True, nullable=False)
    descricao = db.Column(db.String(255), nullable=True)
    permissoes = db.Column(db.JSON, nullable=True)

    usuarios = db.relationship("Usuario", secondary=usuario_perfil, back_populates="perfis")

    def __repr__(self):
        return f"<Perfil {self.id} {self.nome}>"
