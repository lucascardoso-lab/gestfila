from functools import wraps

from flask import abort
from flask_login import current_user


def roles_required(*nomes_perfil):
    """Libera o acesso para super admin ou usuários com algum dos perfis informados."""

    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)
            if current_user.is_super_admin or current_user.tem_perfil(*nomes_perfil):
                return view_func(*args, **kwargs)
            abort(403)

        return wrapped

    return decorator


def super_admin_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_super_admin:
            abort(403)
        return view_func(*args, **kwargs)

    return wrapped
