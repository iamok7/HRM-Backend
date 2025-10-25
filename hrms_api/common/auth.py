# hrms_api/common/auth.py
from __future__ import annotations

from functools import wraps
from typing import Iterable, Set

from flask_jwt_extended import jwt_required, get_jwt, get_jwt_identity

from hrms_api.common.http import fail
from hrms_api.extensions import db
from hrms_api.models.user import User
from hrms_api.models.security import Role, Permission, UserRole, RolePermission


# ---------- helpers ----------

def _wildcard_match(user_perm: str, required: str) -> bool:
    """
    Match required permission against a user's permission with simple wildcards.
    Examples:
      user_perm: 'payroll.*'          matches required: 'payroll.trades.read'
      user_perm: 'payroll.trades.*'   matches required: 'payroll.trades.write'
      user_perm: 'payroll.trades.read' matches only exact
    """
    if user_perm == required:
        return True
    if user_perm.endswith(".*"):
        prefix = user_perm[:-2]
        return required.startswith(prefix)
    return False


def _has_any_perm(user_perms: Set[str], required_perms: Iterable[str]) -> bool:
    if not required_perms:
        return True
    if not user_perms:
        return False
    for req in required_perms:
        # exact or wildcard on user's side
        if any(_wildcard_match(up, req) for up in user_perms):
            return True
    return False


def _collect_perms_from_db(user_id: int) -> Set[str]:
    """
    Load *distinct* permission codes granted to the user via roles.
    """
    q = (
        db.session.query(Permission.code)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .join(Role, Role.id == RolePermission.role_id)
        .join(UserRole, UserRole.role_id == Role.id)
        .filter(UserRole.user_id == user_id)
        .distinct()
    )
    return {row[0] for row in q.all()}


def _collect_roles_from_db(user_id: int) -> Set[str]:
    q = (
        db.session.query(Role.code)
        .join(UserRole, UserRole.role_id == Role.id)
        .filter(UserRole.user_id == user_id)
        .distinct()
    )
    return {row[0] for row in q.all()}


# ---------- decorators ----------

def requires_roles(*codes: str):
    """
    Require that the current user has AT LEAST ONE of the given role codes.
    - Uses roles in JWT if present; falls back to DB.
    - 'admin' role always passes.
    """
    def outer(fn):
        @wraps(fn)
        @jwt_required()
        def inner(*args, **kwargs):
            claims = get_jwt() or {}
            jwt_roles = set(claims.get("roles") or [])
            if "admin" in jwt_roles:
                return fn(*args, **kwargs)

            uid = get_jwt_identity()
            if uid is None:
                return fail("Unauthorized", status=401)

            roles: Set[str]
            if jwt_roles:
                roles = jwt_roles
            else:
                # fallback DB
                user = User.query.get(uid)
                if not user:
                    return fail("Unauthorized", status=401)
                roles = _collect_roles_from_db(user.id)

            if "admin" in roles:
                return fn(*args, **kwargs)

            if not any(r in roles for r in codes):
                return fail("Forbidden", status=403)

            return fn(*args, **kwargs)
        return inner
    return outer


def requires_perms(*perm_codes: str):
    """
    Require that the current user has ANY of the given permission codes.

    Fast path: read 'perms' and 'roles' from JWT claims if present.
    Fallback:  query DB for permissions via role mappings.

    Supports simple wildcards granted to the user:
      - 'payroll.*' or 'payroll.trades.*'
    The required codes passed to the decorator should be explicit
    (e.g., 'payroll.trades.read', 'payroll.trades.write').
    """
    def outer(fn):
        @wraps(fn)
        @jwt_required()
        def inner(*args, **kwargs):
            # If nothing specified, allow (no-op)
            if not perm_codes:
                return fn(*args, **kwargs)

            claims = get_jwt() or {}
            jwt_roles = set(claims.get("roles") or [])
            if "admin" in jwt_roles:
                return fn(*args, **kwargs)

            uid = get_jwt_identity()
            if uid is None:
                return fail("Unauthorized", status=401)

            # Prefer JWT perms if present (issued at login)
            jwt_perms = set(claims.get("perms") or [])

            if jwt_perms:
                if _has_any_perm(jwt_perms, perm_codes):
                    return fn(*args, **kwargs)
                # If JWT perms are present but don't match, still try DB in case of stale token.
                # (Optional: comment out to enforce strict JWT-only)
            
            # DB fallback (fresh live read)
            user = User.query.get(uid)
            if not user:
                return fail("Unauthorized", status=401)

            db_roles = _collect_roles_from_db(user.id)
            if "admin" in db_roles:
                return fn(*args, **kwargs)

            db_perms = _collect_perms_from_db(user.id)
            if not _has_any_perm(db_perms, perm_codes):
                return fail("Forbidden", status=403)

            return fn(*args, **kwargs)
        return inner
    return outer
