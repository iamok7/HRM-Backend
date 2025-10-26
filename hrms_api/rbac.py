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


# -------- USER: CREATE + OPTIONAL ROLE ASSIGN --------
from sqlalchemy.exc import IntegrityError

def _set_password(u: User, raw: str) -> bool:
    # Prefer your model’s method if present
    for m in ("set_password", "set_pw", "password_set", "set_password_hash"):
        if hasattr(u, m) and callable(getattr(u, m)):
            getattr(u, m)(raw)
            return True
    # Fallback to Werkzeug hash
    try:
        from werkzeug.security import generate_password_hash
        ph = generate_password_hash(raw)
        if hasattr(u, "password_hash"):
            u.password_hash = ph; return True
        if hasattr(u, "password"):
            u.password = ph; return True
    except Exception:
        current_app.logger.exception("Password hashing failed")
    return False

def _apply_user_name(u: User, name: str | None):
    if not name: return
    for f in ("full_name", "name", "display_name", "first_last", "title"):
        if hasattr(u, f):
            setattr(u, f, name)
            break
    # optional split if you keep first_name/last_name
    if " " in name:
        first, last = name.split(" ", 1)
        if hasattr(u, "first_name"): u.first_name = first
        if hasattr(u, "last_name"):  u.last_name  = last

@bp.post("/users")
@jwt_required()
@require_perm("rbac.manage")
def rbac_create_user():
    """
    Create a login user and (optionally) assign a role.
    JSON:
    {
      "email": "jane@demo.local",
      "password": "Secret#123",
      "name": "Jane Doe",          # optional (mapped to full_name/name/etc.)
      "role": "employee"           # optional; defaults to 'employee' if omitted
    }
    """
    j = _json()
    email = (j.get("email") or "").strip().lower()
    password = (j.get("password") or "")
    name = (j.get("name") or "").strip() or None
    role_code = (j.get("role") or "employee").strip().lower()

    if not email or not password:
        return _fail("email and password required", 422)
    if len(password) < 6:
        return _fail("password too short (min 6)", 422)

    # role must exist (ensure-defaults can create common ones)
    role = Role.query.filter_by(code=role_code).first()
    if not role:
        return _fail(f"role '{role_code}' not found", 404)

    try:
        # unique email check
        existing = User.query.filter_by(email=email).first()
        if existing:
            return _fail("user already exists", 409)

        u = User(email=email)
        _apply_user_name(u, name)
        if not _set_password(u, password):
            return _fail("unable to set password", 500)

        db.session.add(u); db.session.flush()

        # assign role
        if not UserRole.query.filter_by(user_id=u.id, role_id=role.id).first():
            db.session.add(UserRole(user_id=u.id, role_id=role.id))

        db.session.commit()
        return _ok({
            "id": u.id,
            "email": u.email,
            "name": getattr(u, "full_name", None) or getattr(u, "name", None),
            "assigned_role": role.code
        }, 201)

    except IntegrityError:
        db.session.rollback()
        return _fail("user already exists (unique email)", 409)
    except Exception as e:
        current_app.logger.exception("create user failed")
        db.session.rollback()
        return _fail("internal error", 500)



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

# -------- USER: INSPECT ROLES + PERMISSIONS --------
@bp.get("/users/inspect")
@jwt_required()
@require_perm("rbac.manage")   # only RBAC managers can inspect others
def rbac_user_inspect():
    """
    Query params (one of):
      - email=emp1@demo.local
      - user_id=42
    Returns user's roles and flattened permission codes.
    """
    email = (request.args.get("email") or "").strip().lower()
    user_id = request.args.get("user_id", type=int)

    u = None
    if user_id:
        u = db.session.get(User, user_id)
    if not u and email:
        u = User.query.filter_by(email=email).first()
    if not u:
        return _fail("user not found", 404)

    # roles via relationship (many-to-many)
    roles = []
    if hasattr(u, "roles"):
        roles = [ _role_row(r) for r in getattr(u, "roles") ]

    # permissions via existing helper
    perms = sorted(list(_perm_set_for(u.id)))

    return _ok({
        "user": _user_row(u),
        "roles": roles,
        "perms": perms,
        "counts": {"roles": len(roles), "perms": len(perms)}
    })

# -------- USER: RESET PASSWORD (HR/Admin) --------
@bp.post("/users/reset-password")
@jwt_required()
def rbac_user_reset_password():
    """
    Reset a user's password (HR/Admin).
    JSON (at least one identifier required):
    {
      "user_id": 42,            // OR
      "email": "emp1@demo.local",
      "password": "NewStrong#123"  // required, min 6 chars
    }
    """
    # Authorization: allow if caller has perm 'users.password.reset' OR role in {'admin','hr'}
    try:
        ident = get_jwt_identity()
        caller = db.session.get(User, int(ident)) if str(ident).isdigit() else None
    except Exception:
        caller = None
    allowed = False
    if caller is not None:
        try:
            roles = {getattr(r, 'code', '').lower() for r in getattr(caller, 'roles', [])}
        except Exception:
            roles = set()
        if 'admin' in roles or 'hr' in roles:
            allowed = True
        else:
            if 'users.password.reset' in _perm_set_for(caller.id):
                allowed = True
    if not allowed:
        return _fail("Forbidden", status=403, code="auth.forbidden")

    j = _json()
    user_id = j.get("user_id")
    email = (j.get("email") or "").strip().lower()
    password = j.get("password") or j.get("new_password")

    if not password or len(str(password)) < 6:
        return _fail("password is required (min 6 characters)", 422)
    if not user_id and not email:
        return _fail("provide user_id or email", 422)

    u = None
    if user_id:
        try:
            u = db.session.get(User, int(user_id))
        except Exception:
            u = None
    if not u and email:
        u = User.query.filter_by(email=email).first()
    if not u:
        return _fail("user not found", 404)

    if not _set_password(u, str(password)):
        return _fail("unable to set password", 500)

    try:
        db.session.add(u)
        db.session.commit()
    except Exception:
        db.session.rollback()
        return _fail("database error", 500)

    return _ok({
        "message": "password reset",
        "user": {"id": u.id, "email": u.email}
    })

# ===== SESSION / TOKEN SETTINGS =====
from datetime import datetime, timedelta, timezone
from sqlalchemy import Column, Integer, DateTime, String
from sqlalchemy.exc import SQLAlchemyError

# 1-row table persisted in DB
class AuthSettings(db.Model):
    __tablename__ = "auth_settings"
    id = Column(Integer, primary_key=True)
    access_expires_seconds = Column(Integer, nullable=False, default=3600)   # 60 min
    refresh_expires_seconds = Column(Integer, nullable=False, default=1209600)  # 14 days
    token_header_type = Column(String(32), nullable=False, default="Bearer")
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_by = Column(Integer, nullable=True)  # user_id of updater (optional)

def _ensure_auth_settings():
    s = AuthSettings.query.get(1)
    if not s:
        # Take defaults from app.config if present
        acc = current_app.config.get("JWT_ACCESS_TOKEN_EXPIRES")
        ref = current_app.config.get("JWT_REFRESH_TOKEN_EXPIRES")
        def to_secs(v, fallback):
            if isinstance(v, timedelta): return int(v.total_seconds())
            if isinstance(v, (int, float)): return int(v)
            return fallback
        s = AuthSettings(
            id=1,
            access_expires_seconds=to_secs(acc, 3600),
            refresh_expires_seconds=to_secs(ref, 1209600),
            token_header_type=current_app.config.get("JWT_HEADER_TYPE", "Bearer"),
        )
        db.session.add(s); db.session.commit()
    return s

def _apply_settings_to_app(s: AuthSettings):
    # Make them live immediately for new tokens
    current_app.config["JWT_ACCESS_TOKEN_EXPIRES"]  = timedelta(seconds=s.access_expires_seconds)
    current_app.config["JWT_REFRESH_TOKEN_EXPIRES"] = timedelta(seconds=s.refresh_expires_seconds)
    current_app.config["JWT_HEADER_TYPE"]           = s.token_header_type

@bp.get("/session/settings")
@jwt_required()
@require_perm("rbac.manage")
def rbac_get_session_settings():
    s = _ensure_auth_settings()
    _apply_settings_to_app(s)
    return _ok({
        "access_expires_seconds": s.access_expires_seconds,
        "access_expires_minutes": s.access_expires_seconds // 60,
        "refresh_expires_seconds": s.refresh_expires_seconds,
        "refresh_expires_days": round(s.refresh_expires_seconds / 86400, 2),
        "token_header_type": s.token_header_type,
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
        "updated_by": s.updated_by,
    })

@bp.post("/session/settings")
@jwt_required()
@require_perm("rbac.manage")
def rbac_update_session_settings():
    """
    JSON (any of these fields optional):
    {
      "access_minutes": 45,         # OR access_seconds
      "refresh_days": 7,            # OR refresh_seconds
      "token_header_type": "Bearer" # optional
    }
    """
    j = _json()
    s = _ensure_auth_settings()

    acc_sec = j.get("access_seconds")
    if acc_sec is None and "access_minutes" in j:
        acc_sec = int(j.get("access_minutes")) * 60

    ref_sec = j.get("refresh_seconds")
    if ref_sec is None and "refresh_days" in j:
        ref_sec = int(j.get("refresh_days")) * 86400

    if acc_sec is not None:
        if int(acc_sec) < 60: return _fail("access must be >= 60 seconds", 422)
        s.access_expires_seconds = int(acc_sec)

    if ref_sec is not None:
        if int(ref_sec) < 3600: return _fail("refresh must be >= 1 hour", 422)
        s.refresh_expires_seconds = int(ref_sec)

    tht = (j.get("token_header_type") or "").strip()
    if tht:
        s.token_header_type = tht

    # who updated?
    try:
        ident = get_jwt_identity()
        u = db.session.get(User, int(ident)) if str(ident).isdigit() else None
        s.updated_by = getattr(u, "id", None)
    except Exception:
        pass

    s.updated_at = datetime.now(timezone.utc)

    try:
        db.session.commit()
        _apply_settings_to_app(s)
        return _ok({
            "message": "session settings updated",
            "settings": {
                "access_expires_seconds": s.access_expires_seconds,
                "refresh_expires_seconds": s.refresh_expires_seconds,
                "token_header_type": s.token_header_type
            }
        })
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("failed to update session settings")
        return _fail("db error", 500)

# ---- TOKEN HEALTH ----
@bp.get("/token/health")
@jwt_required()
def rbac_token_health():
    """
    Returns validity info for the *current* token.
    """
    from flask_jwt_extended import get_jwt
    claims = get_jwt()
    now = datetime.now(timezone.utc)
    # JWT times are seconds since epoch (UTC)
    iat = datetime.fromtimestamp(claims.get("iat", 0), tz=timezone.utc) if "iat" in claims else None
    nbf = datetime.fromtimestamp(claims.get("nbf", 0), tz=timezone.utc) if "nbf" in claims else None
    exp = datetime.fromtimestamp(claims.get("exp", 0), tz=timezone.utc) if "exp" in claims else None
    secs_left = int((exp - now).total_seconds()) if exp else None
    status = "ok" if exp and secs_left is not None and secs_left > 0 else "expired"

    return _ok({
        "status": status,
        "identity": get_jwt_identity(),
        "issued_at": iat.isoformat() if iat else None,
        "not_before": nbf.isoformat() if nbf else None,
        "expires_at": exp.isoformat() if exp else None,
        "seconds_until_expiry": secs_left,
        "server_time": now.isoformat()
    })


from sqlalchemy.exc import ProgrammingError, OperationalError

def _ensure_auth_settings():
    # 1) make sure table exists (no-op if already there)
    try:
        AuthSettings.__table__.create(db.engine, checkfirst=True)
    except Exception:
        current_app.logger.exception("auth_settings create-if-missing failed (ignored)")

    # 2) ensure the singleton row
    s = db.session.get(AuthSettings, 1)
    if not s:
        acc = current_app.config.get("JWT_ACCESS_TOKEN_EXPIRES")
        ref = current_app.config.get("JWT_REFRESH_TOKEN_EXPIRES")

        def _secs(v, fallback):
            if isinstance(v, timedelta): return int(v.total_seconds())
            if isinstance(v, (int, float)): return int(v)
            return fallback

        s = AuthSettings(
            id=1,
            access_expires_seconds=_secs(acc, 3600),        # 60 min default
            refresh_expires_seconds=_secs(ref, 1209600),    # 14 days default
            token_header_type=current_app.config.get("JWT_HEADER_TYPE", "Bearer"),
        )
        db.session.add(s)
        db.session.commit()
    return s


# -------- SYSTEM: ROUTE INVENTORY / COUNTS (ALL BLUEPRINTS) --------
from collections import defaultdict

@bp.get("/system/routes")
@jwt_required()
@require_perm("rbac.manage")
def system_routes():
    """
    Returns ALL Flask routes in the app (every blueprint), with filters.

    Query params (all optional):
      - method=GET|POST|PUT|PATCH|DELETE   filter by one method
      - only_get=true                      shorthand for method=GET
      - counts_only=true                   return only counts/summary (no list)
      - include_internal=true              include Flask internals like /static
      - blueprint=rbac,employees,...       comma list to include only these blueprints
      - prefix=/api/                       include only rules that start with this prefix
      - exclude=/static,/favicon.ico       comma list of prefixes to skip

    Examples:
      /api/v1/rbac/system/routes?only_get=true
      /api/v1/rbac/system/routes?counts_only=true&prefix=/api/
      /api/v1/rbac/system/routes?blueprint=employees,attendance&prefix=/api/v1/
    """
    q = request.args
    method = (q.get("method") or "").strip().upper()
    if q.get("only_get", "").lower() == "true":
        method = "GET"
    counts_only     = q.get("counts_only", "").lower() == "true"
    include_internal= q.get("include_internal", "").lower() == "true"
    bp_filter       = set(s.strip() for s in (q.get("blueprint") or "").split(",") if s.strip())
    prefix          = (q.get("prefix") or "").strip()
    exclude_list    = [s.strip() for s in (q.get("exclude") or "").split(",") if s.strip()]

    items = []
    by_method = defaultdict(int)
    by_bp     = defaultdict(int)

    for rule in current_app.url_map.iter_rules():
        rule_str = str(rule)

        # skip internals unless asked
        if not include_internal and (rule.endpoint == "static" or rule_str.startswith("/static")):
            continue

        # prefix include filter
        if prefix and not rule_str.startswith(prefix):
            continue

        # excluded prefixes
        if any(rule_str.startswith(x) for x in exclude_list):
            continue

        methods = sorted(m for m in rule.methods if m not in ("HEAD", "OPTIONS"))
        if method and method not in methods:
            continue

        endpoint = rule.endpoint  # e.g. "rbac.rbac_list_roles"
        bp_name = endpoint.split(".", 1)[0] if "." in endpoint else None
        if bp_filter and (bp_name not in bp_filter):
            continue

        # counts
        for m in methods:
            by_method[m] += 1
        by_bp[bp_name or "root"] += 1

        items.append({
            "rule": rule_str,
            "methods": methods,
            "endpoint": endpoint,
            "blueprint": bp_name,
        })

    # sort for readability
    items.sort(key=lambda x: ((x["blueprint"] or ""), x["rule"]))

    summary = {
        "total_routes": len(items),
        "by_method": dict(sorted(by_method.items())),
        "by_blueprint": dict(sorted(by_bp.items())),
        "filtered_by": {
            "method": method or None,
            "blueprints": sorted(list(bp_filter)) if bp_filter else None,
            "prefix": prefix or None,
            "exclude": exclude_list or None,
            "include_internal": include_internal
        }
    }
    return _ok({"summary": summary} if counts_only else {"summary": summary, "routes": items})


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
        "attendance.read","leave.read","rbac.manage","users.password.reset"
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
    grant(r_hr, perms["users.password.reset"])

    grant(r_admin, perms["rbac.manage"])
    grant(r_admin, perms["users.password.reset"])

    db.session.commit()
    return _ok({
        "roles": [_role_row(x) for x in (r_employee, r_manager, r_hr, r_admin)],
        "perms": sorted(perms.keys()),
        "note":  "idempotent—safe to call many times"
    })
