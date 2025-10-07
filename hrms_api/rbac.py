# hrms_api/rbac.py  — paste-ready
# NOTE: If your project uses a different base path for RBAC,
#       adjust url_prefix below (default here: /api/v1/rbac).

from __future__ import annotations
from functools import wraps
from typing import Set

from flask import g, jsonify, current_app, request, Blueprint
from flask_jwt_extended import jwt_required, get_jwt_identity
from sqlalchemy import select, distinct

from hrms_api.extensions import db
from hrms_api.models.user import User
from hrms_api.models.security import Role, Permission, RolePermission, UserRole

# ---------- Blueprint ----------
bp = Blueprint("rbac", __name__, url_prefix="/api/v1/rbac")

# ---------- Helpers ----------
def _ok(data=None, status=200, **meta):
    payload = {"success": True, "data": data}
    if meta:
        payload["meta"] = meta
    return jsonify(payload), status

def _fail(msg, status=400, code: str | None = None):
    p = {"success": False, "error": {"message": msg}}
    if code:
        p["error"]["code"] = code
    return jsonify(p), status

def _json():
    return (request.get_json(silent=True) or {}) if request.is_json else {}

# --- replace these helpers ---

def _pick(obj, *candidates):
    for f in candidates:
        if hasattr(obj, f):
            v = getattr(obj, f)
            if v not in (None, ""):
                return v
    return None

def _role_row(r: Role):
    return {
        "id": r.id,
        "code": r.code,
        "name": _pick(r, "name", "title", "label", "display_name", "role_name") or r.code
    }

def _perm_row(p: Permission):
    return {
        "id": p.id,
        "code": p.code,
        "name": _pick(p, "name", "title", "label", "display_name", "desc", "description") or p.code
    }

def _user_row(u: User): return {"id": u.id, "email": u.email, "name": getattr(u, "name", None)}

# ---------- Permission cache/lookup ----------
def _perm_set_for(user_id: int) -> Set[str]:
    """
    Efficient explicit permission lookup via Role -> RolePermission -> Permission.
    """
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

# ---------- Decorator: require_perm ----------
def require_perm(permission_code: str):
    """
    Usage:
      @bp.get("/something")
      @jwt_required()
      @require_perm("employees.read")
      def handler(): ...
    """
    def decorator(fn):
        @wraps(fn)
        @jwt_required()
        def wrapper(*args, **kwargs):
            ident = get_jwt_identity()
            # Accept either numeric user-id or email as identity
            user = (
                db.session.get(User, int(ident)) if str(ident).isdigit()
                else db.session.execute(select(User).where(User.email == str(ident))).scalar_one_or_none()
            )
            if not user:
                return _fail("Unauthorized", status=401, code="auth.unknown_user")

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
                return _fail("Forbidden", status=403, code="auth.forbidden")

            return fn(*args, **kwargs)
        return wrapper
    return decorator

# =====================================================================
#                              ENDPOINTS
# =====================================================================

# -------- ROLES --------
@bp.get("/roles")
@jwt_required()
@require_perm("rbac.manage")
def rbac_list_roles():
    rows = Role.query.order_by(Role.code.asc()).all()
    return _ok([_role_row(r) for r in rows])

@bp.post("/roles")
@jwt_required()
@require_perm("rbac.manage")
def rbac_create_role():
    """
    JSON: { "code": "manager", "name": "Manager" }
    Idempotent: creates if new; updates name if exists and provided.
    """
    j = _json()
    code = (j.get("code") or "").strip().lower()
    if not code:
        return _fail("code required", 422)
    name = (j.get("name") or "").strip() or code

    r = Role.query.filter_by(code=code).first()
    if not r:
        kwargs = {"code": code}
        for f in ("name", "title", "label", "display_name", "role_name"):
            if hasattr(Role, f):
                kwargs[f] = name
                break
        r = Role(**kwargs)
        db.session.add(r)
    else:
        if j.get("name"):
            for f in ("name", "title", "label", "display_name", "role_name"):
                if hasattr(r, f):
                    setattr(r, f, name)
                    break

    db.session.commit()
    return _ok(_role_row(r), 201)

@bp.delete("/roles/<role_code>")
@jwt_required()
@require_perm("rbac.manage")
def rbac_delete_role(role_code: str):
    r = Role.query.filter_by(code=role_code.lower()).first()
    if not r:
        return _fail("role not found", 404)
    RolePermission.query.filter_by(role_id=r.id).delete()
    UserRole.query.filter_by(role_id=r.id).delete()
    db.session.delete(r)
    db.session.commit()
    return _ok({"deleted": role_code})

# -------- PERMISSIONS --------
@bp.get("/perms")
@jwt_required()
@require_perm("rbac.manage")
def rbac_list_perms():
    rows = Permission.query.order_by(Permission.code.asc()).all()
    return _ok([_perm_row(p) for p in rows])

@bp.post("/perms")
@jwt_required()
@require_perm("rbac.manage")
def rbac_create_perm():
    """
    JSON: { "code": "employees.read", "name": "Read Employees" }
    Idempotent.
    """
    j = _json()
    code = (j.get("code") or "").strip().lower()
    if not code:
        return _fail("code required", 422)
    name = (j.get("name") or "").strip() or code

    p = Permission.query.filter_by(code=code).first()
    if not p:
        kwargs = {"code": code}
        for f in ("name", "title", "label", "display_name", "desc", "description"):
            if hasattr(Permission, f):
                kwargs[f] = name
                break
        p = Permission(**kwargs)
        db.session.add(p)
    else:
        if j.get("name"):
            for f in ("name", "title", "label", "display_name", "desc", "description"):
                if hasattr(p, f):
                    setattr(p, f, name)
                    break

    db.session.commit()
    return _ok(_perm_row(p), 201)

@bp.delete("/perms/<perm_code>")
@jwt_required()
@require_perm("rbac.manage")
def rbac_delete_perm(perm_code: str):
    p = Permission.query.filter_by(code=perm_code.lower()).first()
    if not p:
        return _fail("permission not found", 404)
    RolePermission.query.filter_by(permission_id=p.id).delete()
    db.session.delete(p)
    db.session.commit()
    return _ok({"deleted": perm_code})

# -------- ROLE <-> PERMISSION --------
@bp.post("/roles/<role_code>/grant")
@jwt_required()
@require_perm("rbac.manage")
def rbac_grant_perm(role_code: str):
    """ JSON: { "permission": "employees.read" } """
    j = _json()
    perm_code = (j.get("permission") or "").strip().lower()
    if not perm_code:
        return _fail("permission required", 422)

    r = Role.query.filter_by(code=role_code.lower()).first()
    if not r:
        return _fail("role not found", 404)
    p = Permission.query.filter_by(code=perm_code).first()
    if not p:
        return _fail("permission not found", 404)

    link = RolePermission.query.filter_by(role_id=r.id, permission_id=p.id).first()
    if not link:
        db.session.add(RolePermission(role_id=r.id, permission_id=p.id))
        db.session.commit()
    return _ok({"granted": {"role": r.code, "permission": p.code}})

@bp.post("/roles/<role_code>/revoke")
@jwt_required()
@require_perm("rbac.manage")
def rbac_revoke_perm(role_code: str):
    """ JSON: { "permission": "employees.read" } """
    j = _json()
    perm_code = (j.get("permission") or "").strip().lower()
    if not perm_code:
        return _fail("permission required", 422)

    r = Role.query.filter_by(code=role_code.lower()).first()
    if not r:
        return _fail("role not found", 404)
    p = Permission.query.filter_by(code=perm_code).first()
    if not p:
        return _fail("permission not found", 404)

    RolePermission.query.filter_by(role_id=r.id, permission_id=p.id).delete()
    db.session.commit()
    return _ok({"revoked": {"role": r.code, "permission": p.code}})

# -------- USER <-> ROLE --------
@bp.post("/users/assign")
@jwt_required()
@require_perm("rbac.manage")
def rbac_assign_role():
    """
    JSON: { "email": "emp@demo.local", "role": "employee" }
    """
    j = _json()
    email = (j.get("email") or "").strip().lower()
    role  = (j.get("role") or "").strip().lower()
    if not email or not role:
        return _fail("email and role required", 422)

    u = User.query.filter_by(email=email).first()
    if not u:
        return _fail("user not found", 404)
    r = Role.query.filter_by(code=role).first()
    if not r:
        return _fail("role not found", 404)

    link = UserRole.query.filter_by(user_id=u.id, role_id=r.id).first()
    if not link:
        db.session.add(UserRole(user_id=u.id, role_id=r.id))
        db.session.commit()
    return _ok({"assigned": {"user": u.email, "role": r.code}})

@bp.post("/users/unassign")
@jwt_required()
@require_perm("rbac.manage")
def rbac_unassign_role():
    """
    JSON: { "email": "emp@demo.local", "role": "employee" }
    """
    j = _json()
    email = (j.get("email") or "").strip().lower()
    role  = (j.get("role") or "").strip().lower()
    if not email or not role:
        return _fail("email and role required", 422)

    u = User.query.filter_by(email=email).first()
    if not u:
        return _fail("user not found", 404)
    r = Role.query.filter_by(code=role).first()
    if not r:
        return _fail("role not found", 404)

    UserRole.query.filter_by(user_id=u.id, role_id=r.id).delete()
    db.session.commit()
    return _ok({"unassigned": {"user": u.email, "role": r.code}})

# -------- QUICK DEFAULTS (idempotent) --------
@bp.post("/ensure-defaults")
@jwt_required()
@require_perm("rbac.manage")
def rbac_ensure_defaults():
    """
    Creates common roles (employee, manager, hr, admin) + a minimal permission set
    and grants a sensible subset to each role. Safe to call multiple times.
    """
    def ensure_role(code, name=None):
        r = Role.query.filter_by(code=code).first()
        if not r:
            kwargs = {"code": code}
            # write name into whichever display field exists
            disp = name or code
            for f in ("name", "title", "label", "display_name", "role_name"):
                if hasattr(Role, f):
                    kwargs[f] = disp
                    break
            r = Role(**kwargs)
            db.session.add(r); db.session.flush()
        return r

    def ensure_perm(code, name=None):
        p = Permission.query.filter_by(code=code).first()
        if not p:
            kwargs = {"code": code}
            disp = name or code
            for f in ("name", "title", "label", "display_name", "desc", "description"):
                if hasattr(Permission, f):
                    kwargs[f] = disp
                    break
            p = Permission(**kwargs)
            db.session.add(p); db.session.flush()
        return p


    r_employee = ensure_role("employee", "Employee")
    r_manager  = ensure_role("manager",  "Manager")
    r_hr       = ensure_role("hr",       "HR")
    r_admin    = ensure_role("admin",    "Administrator")

    perm_codes = [
        "employees.read","employees.create","employees.update",
        "master.locations.read","master.departments.read",
        "attendance.read","leave.read","rbac.manage"
    ]
    perms = {c: ensure_perm(c) for c in perm_codes}
    db.session.commit()

    def grant(role, perm):
        if not RolePermission.query.filter_by(role_id=role.id, permission_id=perm.id).first():
            db.session.add(RolePermission(role_id=role.id, permission_id=perm.id))

    grant(r_employee, perms["employees.read"])
    grant(r_employee, perms["attendance.read"])
    grant(r_employee, perms["leave.read"])

    grant(r_manager,  perms["employees.read"])
    grant(r_manager,  perms["attendance.read"])
    grant(r_manager,  perms["leave.read"])

    grant(r_hr, perms["employees.read"])
    grant(r_hr, perms["employees.create"])
    grant(r_hr, perms["employees.update"])
    grant(r_hr, perms["master.locations.read"])
    grant(r_hr, perms["master.departments.read"])

    grant(r_admin, perms["rbac.manage"])

    db.session.commit()
    return _ok({
        "roles": [_role_row(x) for x in (r_employee, r_manager, r_hr, r_admin)],
        "perms": sorted(perms.keys()),
        "note":  "idempotent—safe to call many times"
    })
