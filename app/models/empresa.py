from datetime import datetime

from app.extensions import db

TEXTO_CHAMADA_SENHA_PADRAO = "Atenção, senha {senha}. Por favor, dirija-se a {local}."
TEXTO_CHAMADA_NOME_PADRAO = "{nome}, por favor, dirija-se a {local}."


class Empresa(db.Model):
    __tablename__ = "empresas"

    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(150), nullable=False)
    cnpj = db.Column(db.String(18), unique=True, nullable=True)
    ativo = db.Column(db.Boolean, default=True, nullable=False)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    # texto do áudio de chamada (painel/TV, seção 6) - placeholders {senha}/{local}/
    # {nome} substituídos no client-side (painel/tv.html). Default é o texto decidido
    # com o cliente (não o texto que estava hardcoded antes desta feature existir).
    texto_chamada_senha_template = db.Column(
        db.String(255), nullable=False, default=TEXTO_CHAMADA_SENHA_PADRAO
    )
    texto_chamada_nome_template = db.Column(
        db.String(255), nullable=False, default=TEXTO_CHAMADA_NOME_PADRAO
    )

    # Adendo 10.43: logo opcional por empresa (cupom/TV/admin). Guarda só o
    # nome do arquivo determinístico ('empresa_<id>.png'), nunca o nome
    # original enviado pelo usuário nem o caminho completo - o arquivo físico
    # fica em app/static/uploads/logos/ (app/utils/uploads.py resolve o
    # caminho). None = sem logo, cai no fallback de nome em texto em todo
    # lugar que exibe a marca da empresa.
    logo_filename = db.Column(db.String(60), nullable=True)

    unidades = db.relationship("Unidade", back_populates="empresa")
    usuarios = db.relationship("Usuario", back_populates="empresa")
    servicos = db.relationship("Servico", back_populates="empresa")

    def __repr__(self):
        return f"<Empresa {self.id} {self.nome}>"
