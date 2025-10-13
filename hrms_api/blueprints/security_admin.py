# hrms_api/blueprints/security_admin.py
from __future__ import annotations

from typing import List, Optional

from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required
from sqlalchemy import select, func, or_, asc, desc
from sqlalchemy.orm import selectinload

from ..extensions import db
from ..models.user import User
from ..models.security import Role, Permission, UserRole, RolePermission
from ..rbac import require_perm

bp = Blueprint("security_admin", __name__, url_prefix="/api/v1/security")

# -------------------- helpers --------------------

def _ok(data=None, status=200, **meta):
    payload = {"success": True, "data": data}
    if meta:
        payload["meta"] = meta
    return jsonify(payload), status

def _fail(msg, status=400, **detail):
    err = {"message": msg}
    if detail:
        err["detail"] = detail
    return jsonify({"success": False, "error": err}), status

def _to_bool(x, default=None):
    if x is None:
        return default
    if isinstance(x, bool):
        return x
    s = str(x).strip().lower()
    if s in ("1", "true", "yes", "y"): return True
    if s in ("0", "false", "no", "n"):  return False
    return default

def _first_existing_col(model, names: List[str]):
    for n in names:
        col = getattr(model, n, None)
        if col is not None:
            return col
    return None

_NAME_CANDIDATES = ["name", "full_name", "display_name", "username", "email", "id"]
_ACTIVE_CANDIDATES = ["is_active", "active", "enabled"]

def _name_col():
    col = _first_existing_col(User, _NAME_CANDIDATES)
    return col or User.id

def _active_col() -> Optional[object]:
    return _first_existing_col(User, _ACTIVE_CANDIDATES)

def _page():
    try:
        return max(int(request.args.get("page", 1)), 1)
    except Exception:
        return 1

def _size():
    try:
        return max(min(int(request.args.get("size", 20)), 200), 1)
    except Exception:
        return 20

def _order_by_from_query():
    sort = (request.args.get("sort") or "").strip()
    order = asc
    if sort.startswith("-"):
        order = desc
        sort = sort[1:]
    if not sort:
        return order(_name_col())
    col = getattr(User, sort, None) or _name_col()
    return order(col)

def _search_condition(q: str):
    q = (q or "").strip()
    if not q:
        return None
    fields = [n for n in ["name","full_name","display_name","username","email"] if hasattr(User, n)]
    conds = [getattr(User, n).ilike(f"%{q}%") for n in fields]
    return or_(*conds) if conds else None

def _display_name(u: User):
    for n in _NAME_CANDIDATES:
        if hasattr(u, n):
            val = getattr(u, n)
            if val:
                return val
    return u.id

def _is_active_value(u: User):
    if hasattr(u, "is_active"): return u.is_active
    if hasattr(u, "active"):    return u.active
    if hasattr(u, "enabled"):   return u.enabled
    return True

def _collect_perm_codes_from_role(r: Role) -> set[str]:
    """
    Handles both shapes:
      - r.permissions -> List[Permission]
      - r.permissions -> List[RolePermission] (with .permission/.perm or only perm_id)
    """
    codes: set[str] = set()
    if hasattr(r, "permissions") and r.permissions:
        for obj in r.permissions:
            # Case A: Permission row
            if hasattr(obj, "code"):
                codes.add(obj.code)
                continue
            # Case B: RolePermission link row
            rp = obj  # type: RolePermission
            perm_obj = getattr(rp, "permission", None) or getattr(rp, "perm", None)
            if perm_obj and hasattr(perm_obj, "code"):
                codes.add(perm_obj.code)
                continue
            perm_id = getattr(rp, "perm_id", None) or getattr(rp, "permission_id", None)
            if perm_id:
                p = db.session.get(Permission, perm_id)
                if p and p.code:
                    codes.add(p.code)
    else:
        # Fallback: fetch RolePermission links
        links = db.session.scalars(select(RolePermission).where(RolePermission.role_id == r.id)).all()
        for lk in links:
            perm_obj = getattr(lk, "permission", None) or getattr(lk, "perm", None)
            if perm_obj and hasattr(perm_obj, "code"):
                codes.add(perm_obj.code)
                continue
            pid = getattr(lk, "perm_id", None) or getattr(lk, "permission_id", None)
            if pid:
                p = db.session.get(Permission, pid)
                if p and p.code:
                    codes.add(p.code)
    return codes

def _user_row(u: User, with_roles=False, with_perms=False):
    row = {
        "id": u.id,
        "name": _display_name(u),
        "email": getattr(u, "email", None),
        "is_active": _is_active_value(u),
    }
    if with_roles:
        roles = []
        if hasattr(u, "roles") and u.roles:
            for r in u.roles:
                roles.append(getattr(r, "code", None) or getattr(r, "name", None))
        row["roles"] = sorted([r for r in roles if r])
    if with_perms:
        perm_codes = set()
        if hasattr(u, "roles") and u.roles:
            for r in u.roles:
                perm_codes |= _collect_perm_codes_from_role(r)
        row["permissions"] = sorted(perm_codes)
    return row

# -------------------- READ endpoints --------------------

@bp.get("/staff")
@jwt_required()
@require_perm("rbac.manage")
def list_staff():
    roles_csv = (request.args.get("roles") or "").strip()
    roles_filter = [r.strip() for r in roles_csv.split(",") if r.strip()]
    q = request.args.get("q") or ""
    is_active = _to_bool(request.args.get("is_active"), default=None)
    page, size = _page(), _size()

    u, r, ur = User, Role, UserRole
    stmt = select(u).join(ur, ur.user_id == u.id).join(r, r.id == ur.role_id)

    if roles_filter:
        stmt = stmt.where(r.code.in_(roles_filter))
    search = _search_condition(q)
    if search is not None:
        stmt = stmt.where(search)
    active_col = _active_col()
    if is_active is not None and active_col is not None:
        stmt = stmt.where(active_col.is_(is_active))

    stmt = stmt.order_by(_order_by_from_query()).distinct()

    total_stmt = select(func.count(func.distinct(u.id))).join(ur, ur.user_id == u.id).join(r, r.id == ur.role_id)
    if roles_filter:
        total_stmt = total_stmt.where(r.code.in_(roles_filter))
    if search is not None:
        total_stmt = total_stmt.where(search)
    if is_active is not None and active_col is not None:
        total_stmt = total_stmt.where(active_col.is_(is_active))
    total = db.session.scalar(total_stmt) or 0

    items = (
        db.session.execute(
            stmt.options(selectinload(u.roles))  # no .unique() needed
                .limit(size)
                .offset((page - 1) * size)
        )
        .scalars()
        .all()
    )
    data = [_user_row(x, with_roles=True, with_perms=False) for x in items]
    return _ok(data, meta={"page": page, "size": size, "total": total})

@bp.get("/users-permissions")
@jwt_required()
@require_perm("rbac.manage")
def users_with_roles_and_permissions():
    q = request.args.get("q") or ""
    is_active = _to_bool(request.args.get("is_active"), default=None)
    page, size = _page(), _size()

    u = User
    stmt = select(u)

    search = _search_condition(q)
    if search is not None:
        stmt = stmt.where(search)
    active_col = _active_col()
    if is_active is not None and active_col is not None:
        stmt = stmt.where(active_col.is_(is_active))

    stmt = stmt.order_by(_order_by_from_query())

    total_stmt = select(func.count(u.id))
    if search is not None:
        total_stmt = total_stmt.where(search)
    if is_active is not None and active_col is not None:
        total_stmt = total_stmt.where(active_col.is_(is_active))
    total = db.session.scalar(total_stmt) or 0

    items = (
        db.session.execute(
            stmt.options(
                # If Role.permissions is Permission collection, this loads those.
                # If it's RolePermission link rows, we still avoid duplicates and fetch links cheaply.
                selectinload(u.roles).selectinload(Role.permissions)
            )
            .limit(size)
            .offset((page - 1) * size)
        )
        .scalars()
        .all()
    )

    data = [_user_row(x, with_roles=True, with_perms=True) for x in items]
    return _ok(data, meta={"page": page, "size": size, "total": total})

# -------------------- reference lists --------------------

@bp.get("/roles")
@jwt_required()
@require_perm("rbac.manage")
def list_roles():
    roles = db.session.scalars(select(Role).order_by(Role.code)).all()
    return _ok([{"id": r.id, "code": r.code, "name": getattr(r, "name", None)} for r in roles])

@bp.get("/permissions")
@jwt_required()
@require_perm("rbac.manage")
def list_permissions():
    perms = db.session.scalars(select(Permission).order_by(Permission.code)).all()
    return _ok([{"id": p.id, "code": p.code, "name": getattr(p, "name", None)} for p in perms])

# -------------------- grant / revoke --------------------

@bp.post("/grant-role")
@jwt_required()
@require_perm("rbac.manage")
def grant_role():
    data = request.get_json(silent=True) or {}
    user_email = data.get("email")
    role_code  = data.get("role")
    if not user_email or not role_code:
        return _fail("Provide 'email' and 'role'")
    user = db.session.scalar(select(User).where(User.email == user_email))
    role = db.session.scalar(select(Role).where(Role.code == role_code))
    if not user or not role:
        return _fail("User or role not found", 404)
    exists = db.session.scalar(select(UserRole).where(UserRole.user_id == user.id, UserRole.role_id == role.id))
    if not exists:
        db.session.add(UserRole(user_id=user.id, role_id=role.id))
        db.session.commit()
    return _ok({"granted": True, "email": user_email, "role": role_code})

@bp.post("/revoke-role")
@jwt_required()
@require_perm("rbac.manage")
def revoke_role():
    data = request.get_json(silent=True) or {}
    user_email = data.get("email")
    role_code  = data.get("role")
    if not user_email or not role_code:
        return _fail("Provide 'email' and 'role'")
    user = db.session.scalar(select(User).where(User.email == user_email))
    role = db.session.scalar(select(Role).where(Role.code == role_code))
    if not user or not role:
        return _fail("User or role not found", 404)
    link = db.session.scalar(select(UserRole).where(UserRole.user_id == user.id, UserRole.role_id == role.id))
    if link:
        db.session.delete(link)
        db.session.commit()
    return _ok({"revoked": True, "email": user_email, "role": role_code})

@bp.post("/grant-perm")
@jwt_required()
@require_perm("rbac.manage")
def grant_perm():
    data = request.get_json(silent=True) or {}
    role_code = data.get("role")
    perm_code = data.get("perm")
    if not role_code or not perm_code:
        return _fail("Provide 'role' and 'perm'")
    role = db.session.scalar(select(Role).where(Role.code == role_code))
    perm = db.session.scalar(select(Permission).where(Permission.code == perm_code))
    if not role or not perm:
        return _fail("Role or permission not found", 404)
    exists = db.session.scalar(select(RolePermission).where(RolePermission.role_id == role.id, RolePermission.perm_id == perm.id))
    if not exists:
        db.session.add(RolePermission(role_id=role.id, perm_id=perm.id))
        db.session.commit()
    return _ok({"granted": True, "role": role_code, "perm": perm_code})

@bp.post("/revoke-perm")
@jwt_required()
@require_perm("rbac.manage")
def revoke_perm():
    data = request.get_json(silent=True) or {}
    role_code = data.get("role")
    perm_code = data.get("perm")
    if not role_code or not perm_code:
        return _fail("Provide 'role' and 'perm'")
    role = db.session.scalar(select(Role).where(Role.code == role_code))
    perm = db.session.scalar(select(Permission).where(Permission.code == perm_code))
    if not role or not perm:
        return _fail("Role or permission not found", 404)
    link = db.session.scalar(select(RolePermission).where(RolePermission.role_id == role.id, RolePermission.perm_id == perm.id))
    if link:
        db.session.delete(link)
        db.session.commit()
    return _ok({"revoked": True, "role": role_code, "perm": perm_code})
