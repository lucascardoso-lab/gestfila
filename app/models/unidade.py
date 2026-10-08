from app.extensions import db
from .associations import usuario_unidade


class Unidade(db.Model):
    __tablename__ = "unidades"

    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey("empresas.id"), nullable=False)
    nome = db.Column(db.String(150), nullable=False)
    sigla = db.Column(db.String(4), nullable=True)
    ativo = db.Column(db.Boolean, default=True, nullable=False)

    # Impressão do cupom via window.print() (adendo que substitui a 10.6) - endereço/
    # telefone ficam em Unidade, não em Empresa, porque cada unidade é um local físico
    # distinto (uma mesma empresa pode ter várias unidades em endereços diferentes) -
    # o cupom impresso no totem de uma unidade deve mostrar o endereço DAQUELA unidade,
    # não um endereço genérico da empresa toda. Opcionais: cadastro existente não
    # quebra, cupom simplesmente omite o que não tiver.
    endereco = db.Column(db.Text, nullable=True)
    telefone = db.Column(db.String(30), nullable=True)

    # Rede de segurança do banco para a validação da tela de Unidades (adendo
    # 10.71) - NULL (sem sigla, usa o fallback) pode repetir à vontade.
    __table_args__ = (
        db.UniqueConstraint("empresa_id", "sigla", name="uq_unidades_empresa_sigla"),
    )

    empresa = db.relationship("Empresa", back_populates="unidades")
    usuarios = db.relationship("Usuario", secondary=usuario_unidade, back_populates="unidades")

    @property
    def sigla_exibicao(self):
        """Prefixo curto pra distinguir itens de unidades diferentes em listas/selects
        que misturam várias unidades (ex.: '[MAT] Recepção 1-01'). `sigla` é opcional -
        sem ela, cai nos 3 primeiros caracteres do nome como fallback, sempre em
        maiúsculas."""
        return Unidade.sigla_efetiva(self.sigla, self.nome)

    @staticmethod
    def sigla_efetiva(sigla, nome):
        """Mesma regra de sigla_exibicao, para valores ainda não gravados (ex.:
        validar o formulário de Unidade antes de salvar - adendo 10.71)."""
        return (sigla or nome[:3]).upper()

    def __repr__(self):
        return f"<Unidade {self.id} {self.nome}>"
