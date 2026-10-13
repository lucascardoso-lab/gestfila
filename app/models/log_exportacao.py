from datetime import datetime

from app.extensions import db

# LGPD (README, seção "Dado sensível"): registrar quem exportou o quê é parte
# de conseguir responder "quem teve acesso a este dado" se algum dia for
# preciso - não é redundante com LogAtendimento (que audita o ATENDIMENTO em
# si, não o ACESSO/exportação de dados por um usuário do admin). Adendo 10.67.
FORMATOS_EXPORTACAO = ("csv", "xlsx", "pdf")


class LogExportacaoRelatorio(db.Model):
    __tablename__ = "logs_exportacao_relatorio"

    id = db.Column(db.Integer, primary_key=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False)
    empresa_id = db.Column(db.Integer, db.ForeignKey("empresas.id"), nullable=False)

    # Nome interno do relatório (ex.: "tempo_medio_espera", "tempo_atendimento",
    # "volume_senhas", "ranking_operadores") - mesmo `nome_base` já usado em
    # app/relatorios/export.py, não um enum: novos relatórios não exigem
    # migration nova só para registrar o tipo.
    tipo_relatorio = db.Column(db.String(50), nullable=False)
    formato = db.Column(db.Enum(*FORMATOS_EXPORTACAO, name="formato_exportacao"), nullable=False)

    # Filtros aplicados no momento da exportação - snapshot, não FK "viva" que
    # precisaria sobreviver à exclusão de uma fila/unidade anos depois; nullable
    # porque cada filtro é opcional na tela (None = "todas/todos").
    unidade_id = db.Column(db.Integer, db.ForeignKey("unidades.id"), nullable=True)
    fila_id = db.Column(db.Integer, db.ForeignKey("filas.id"), nullable=True)
    operador_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=True)
    # Filtro de Área (adendo 10.76) - FK e índice com nome explícito.
    area_fila_id = db.Column(
        db.Integer, db.ForeignKey("areas_fila.id", name="fk_logs_exportacao_area_fila"), nullable=True, index=False,
    )
    # AAAA-MM-DD (mesmo formato de Filtros.data_inicio_str/data_fim_str,
    # app/relatorios/helpers.py) - período sempre preenchido (nunca opcional,
    # resolver_filtros já aplica o default "hoje").
    data_inicio = db.Column(db.String(10), nullable=False)
    data_fim = db.Column(db.String(10), nullable=False)

    timestamp = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    usuario = db.relationship("Usuario", foreign_keys=[usuario_id])
    empresa = db.relationship("Empresa")
    unidade = db.relationship("Unidade")
    fila = db.relationship("Fila")
    operador = db.relationship("Usuario", foreign_keys=[operador_id])
    area_fila = db.relationship("AreaFila")

    __table_args__ = (db.Index("ix_logs_exportacao_relatorio_area_fila_id", "area_fila_id"),)

    def __repr__(self):
        return f"<LogExportacaoRelatorio {self.id} {self.tipo_relatorio} {self.formato}>"
