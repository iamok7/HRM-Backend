from functools import wraps
from flask import g, jsonify, current_app
from flask_jwt_extended import jwt_required, get_jwt_identity
from sqlalchemy import select, distinct
from hrms_api.extensions import db
from hrms_api.models.user import User
from hrms_api.models.security import Role, Permission, RolePermission

def _json_err(msg, status=403, code=None):
    p = {"success": False, "error": {"message": msg}}
    if code: p["error"]["code"] = code
    return jsonify(p), status

def _perm_set_for(user_id: int) -> set[str]:
    try:
        user_roles_tbl = User.roles.property.secondary
        roles_tbl      = Role.__table__
        perms_tbl      = Permission.__table__
        role_perms_tbl = RolePermission.__table__
        users_tbl      = User.__table__

        stmt = (
            select(distinct(perms_tbl.c.code))
            .select_from(users_tbl)
            .join(user_roles_tbl, user_roles_tbl.c.user_id == users_tbl.c.id)
            .join(roles_tbl, roles_tbl.c.id == user_roles_tbl.c.role_id)
            .join(role_perms_tbl, role_perms_tbl.c.role_id == roles_tbl.c.id)
            .join(perms_tbl, perms_tbl.c.id == role_perms_tbl.c.permission_id)
            .where(users_tbl.c.id == user_id)
        )
        rows = db.session.execute(stmt).all()
        return {row[0] for row in rows if row and row[0]}
    except Exception:
        current_app.logger.exception("RBAC explicit permission lookup error")
        return set()

def require_perm(permission_code: str):
    def decorator(fn):
        @wraps(fn)
        @jwt_required()
        def wrapper(*args, **kwargs):
            ident = get_jwt_identity()
            user = db.session.get(User, int(ident)) if str(ident).isdigit() \
                else db.session.execute(select(User).where(User.email == str(ident))).scalar_one_or_none()
            if not user:
                return _json_err("Unauthorized", status=401, code="auth.unknown_user")

            perms = getattr(g, "_perm_cache", None)
            if perms is None:
                perms = _perm_set_for(user.id)
                g._perm_cache = perms

            if permission_code not in perms:
                current_app.logger.warning(
                    "RBAC deny user=%s needs=%s has=%d perms (sample)=%s",
                    getattr(user, "email", user.id),
                    permission_code, len(perms),
                    ", ".join(sorted(list(perms))[:15])
                )
                return _json_err("Forbidden", status=403, code="auth.forbidden")

            return fn(*args, **kwargs)
        return wrapper
    return decorator
