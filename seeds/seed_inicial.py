"""Popula os perfis padrão (seção 3 do BLUEPRINT) e cria o usuário super admin inicial.

Uso: python seeds/seed_inicial.py
"""
import os
import secrets
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app
from app.extensions import db
from app.models import Perfil, Usuario

PERFIS_PADRAO = [
    {"nome": "admin", "descricao": "Administrador da empresa"},
    {"nome": "gerencial", "descricao": "Somente leitura de relatórios"},
    {"nome": "atendente", "descricao": "Operação do totem e chamada de senha (setor recepção)"},
    {"nome": "executor", "descricao": "Chamada de senha e chamada por nome (setor coleta)"},
    {"nome": "atendente_executor", "descricao": "União dos perfis atendente e executor"},
]

SUPER_ADMIN_LOGIN = "superadmin"


def seed():
    app = create_app()
    with app.app_context():
        for dados in PERFIS_PADRAO:
            if not Perfil.query.filter_by(nome=dados["nome"]).first():
                db.session.add(Perfil(**dados))

        super_admin = Usuario.query.filter_by(login=SUPER_ADMIN_LOGIN).first()
        if super_admin is None:
            # Checklist de segurança pra publicação no GitHub: senha gerada
            # aleatoriamente a cada execução do seed (nunca mais um valor fixo
            # documentado em texto claro no repositório) + deve_trocar_senha=True
            # (app/middleware/forcar_troca_senha.py) - o sistema bloqueia
            # qualquer tela além da própria troca de senha até ela ser
            # definida, então a senha impressa abaixo nunca tem chance de virar
            # permanente por esquecimento operacional.
            senha_temporaria = secrets.token_urlsafe(12)
            super_admin = Usuario(nome="Super Admin", login=SUPER_ADMIN_LOGIN, empresa_id=None)
            super_admin.set_senha(senha_temporaria)
            super_admin.deve_trocar_senha = True
            db.session.add(super_admin)
            db.session.commit()
            print("Seed concluído: perfis padrão e usuário super admin criados.")
            print(f"Login: {SUPER_ADMIN_LOGIN} / Senha temporária: {senha_temporaria}")
            print("Essa senha só serve pra UM primeiro acesso - o sistema já força a troca antes de liberar qualquer tela.")
        else:
            db.session.commit()
            print("Seed concluído: perfis padrão garantidos (usuário super admin já existia).")


if __name__ == "__main__":
    seed()
