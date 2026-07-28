from app.extensions import db
from .associations import fila_tipos_senha


class TipoSenha(db.Model):
    """Catálogo de tipos de senha (seção 10.4) - ex.: Idoso, Gestante, PCD, Normal.

    Por empresa, não por unidade (reaproveitado entre todas as unidades). Cada Fila
    escolhe um subconjunto deste catálogo via `fila_tipos_senha` (ver Fila.tipos_senha) -
    uma fila simples (ex.: Laboratório) pode não vincular nenhum tipo e seguir gerando
    senha direto, sem tela extra no totem.
    """

    __tablename__ = "tipos_senha"

    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey("empresas.id"), nullable=False)
    nome = db.Column(db.String(80), nullable=False)
    prefixo = db.Column(db.String(2), nullable=True)
    cor = db.Column(db.String(7), nullable=True)
    peso_prioridade = db.Column(db.SmallInteger, default=0, nullable=False)
    tempo_maximo_min = db.Column(db.Integer, nullable=True)
    ativo = db.Column(db.Boolean, default=True, nullable=False)

    empresa = db.relationship("Empresa")
    filas = db.relationship("Fila", secondary=fila_tipos_senha, back_populates="tipos_senha")

    def __repr__(self):
        return f"<TipoSenha {self.id} {self.nome}>"
