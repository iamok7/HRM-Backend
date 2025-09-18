# imports at top
from sqlalchemy import select, distinct
from flask import Blueprint, jsonify, g, current_app
from flask_jwt_extended import jwt_required, get_jwt_identity
from hrms_api.extensions import db
from hrms_api.models.user import User
from hrms_api.models.security import Role, Permission, RolePermission

bp = Blueprint("security", __name__, url_prefix="/api/v1/security")

def _ok(data, meta=None, status=200):
    return jsonify({"success": True, "data": data, "meta": meta or {}}), status

def _err(msg, status=400, code=None):
    p = {"success": False, "error": {"message": msg}}
    if code: p["error"]["code"] = code
    return jsonify(p), status

def _load_permissions_for_user(user_id: int) -> set[str]:
    """
    Resolve perms via explicit tables (User.roles.secondary + role_permissions).
    Works even if ORM relationships/backrefs are misconfigured.
    """
    try:
        # discover the user<->role association table from the User.roles relationship
        user_roles_tbl = User.roles.property.secondary  # Table object
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
        current_app.logger.exception("perm-load explicit failed")
        return set()

@bp.get("/me-permissions")
@jwt_required()
def me_permissions():
    ident = get_jwt_identity()
    # resolve user by id or email
    try:
        user = db.session.get(User, int(ident)) if str(ident).isdigit() \
            else db.session.execute(select(User).where(User.email == str(ident))).scalar_one_or_none()
    except Exception:
        current_app.logger.exception("identity->user resolution failed")
        user = None
    if not user:
        return _err("User not found", status=404, code="user.not_found")

    perms = _load_permissions_for_user(user.id)
    g.me_permissions = perms
    return _ok({"permissions": sorted(perms)})
