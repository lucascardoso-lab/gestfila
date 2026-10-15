from datetime import datetime

from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from app.extensions import db
from app.utils.tempo import janela_dia_clinica_em_utc
from .associations import usuario_perfil, usuario_unidade


class Usuario(UserMixin, db.Model):
    __tablename__ = "usuarios"

    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey("empresas.id"), nullable=True)  # NULL = super admin
    nome = db.Column(db.String(150), nullable=False)
    login = db.Column(db.String(80), unique=True, nullable=False, index=True)
    senha_hash = db.Column(db.String(255), nullable=False)
    ativo = db.Column(db.Boolean, default=True, nullable=False)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    # Preparação pro GitHub (checklist de segurança): a senha do superadmin
    # semeado (seeds/seed_inicial.py) agora é gerada aleatoriamente a cada
    # execução, não mais um valor fixo documentado em texto claro no
    # repositório - mas uma senha "temporária" que ninguém é obrigado a
    # trocar vira permanente por simples inércia operacional. Este flag,
    # checado por app/middleware/forcar_troca_senha.py em toda request
    # autenticada, bloqueia qualquer tela (exceto a própria troca de senha e
    # logout) até o usuário trocar - fecha esse gap sem depender de alguém
    # lembrar de trocar manualmente. Default False: só True pra quem foi
    # criado precisando trocar (superadmin semeado); usuários normais
    # criados pelo admin da empresa não são afetados.
    deve_trocar_senha = db.Column(db.Boolean, default=False, nullable=False)

    # Local escolhido agora (seção 10.3), persistido no servidor - antes só vivia na
    # sessão do navegador (cookie), não era consultável por nenhuma outra tela/query.
    # Ver atendimento/local.py::escolher_local() (grava) e auth/routes.py::logout()
    # (limpa). Nullable: usuário pode nunca ter escolhido um local, ou estar deslogado.
    local_atual_id = db.Column(db.Integer, db.ForeignKey("locais.id"), nullable=True)
    local_desde = db.Column(db.DateTime, nullable=True)

    # Permite forçar logout remoto (ex.: botão "Desconectar" da tela de ocupação de
    # locais, admin/locais.py::desconectar_usuario_local) - a autenticação é por
    # cookie assinado client-side (Flask-Login), sem lista de sessões no servidor,
    # então "derrubar a sessão de outro usuário" não tem como ser feito apagando
    # nada no banco. Em vez disso: o número atual é gravado no cookie no login
    # (auth/routes.py); load_user() (app/__init__.py) compara o número do cookie
    # com este campo a cada request - se não bater (foi incrementado depois que o
    # cookie foi emitido), trata a requisição como não autenticada.
    sessao_versao = db.Column(db.Integer, default=0, nullable=False)

    empresa = db.relationship("Empresa", back_populates="usuarios")
    perfis = db.relationship("Perfil", secondary=usuario_perfil, back_populates="usuarios")
    unidades = db.relationship("Unidade", secondary=usuario_unidade, back_populates="usuarios")
    local_atual = db.relationship("Local")

    def set_senha(self, senha_plana):
        self.senha_hash = generate_password_hash(senha_plana)

    def checar_senha(self, senha_plana):
        return check_password_hash(self.senha_hash, senha_plana)

    @property
    def is_super_admin(self):
        return self.empresa_id is None

    def tem_perfil(self, *nomes):
        perfis_usuario = {p.nome for p in self.perfis}
        return bool(perfis_usuario.intersection(nomes))

    def tem_acesso_unidade(self, unidade_id):
        if self.is_super_admin:
            return True
        if self.tem_perfil("admin"):
            from .unidade import Unidade

            unidade = db.session.get(Unidade, unidade_id)
            return unidade is not None and unidade.empresa_id == self.empresa_id
        return any(u.id == unidade_id for u in self.unidades)

    def __repr__(self):
        return f"<Usuario {self.id} {self.login}>"


def usuarios_com_local_de_dias_anteriores():
    """Query (não executada) dos usuários que ainda "ocupam" um Local escolhido
    ANTES do dia civil de hoje da clínica (adendo 10.79) - `local_desde` é UTC
    naive (`datetime.utcnow()`), limite pela janela do 10.39. `local_desde`
    NULL com `local_atual_id` preenchido NÃO entra: as duas colunas são sempre
    gravadas juntas, e produção não tem nenhuma linha assim (FASE 1 do 10.79).
    Base única da varredura do middleware e do `flask encerrar-pendentes`."""
    inicio_hoje, _fim = janela_dia_clinica_em_utc()
    return Usuario.query.filter(Usuario.local_atual_id.isnot(None), Usuario.local_desde < inicio_hoje)
