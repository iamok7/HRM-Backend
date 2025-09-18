from functools import wraps
from flask_jwt_extended import jwt_required, get_jwt
from hrms_api.common.http import fail
from functools import wraps
from flask_jwt_extended import jwt_required, get_jwt_identity
from hrms_api.extensions import db
from hrms_api.models.user import User
from hrms_api.models.security import Role, Permission, UserRole, RolePermission


def requires_roles(*codes):
    """
    Use on write routes to require at least one role from `codes`.
    Adds @jwt_required() automatically.
    """
    def outer(fn):
        @jwt_required()
        @wraps(fn)
        def inner(*args, **kwargs):
            claims = get_jwt() or {}
            roles = claims.get("roles", [])
            if not any(c in roles for c in codes):
                return fail("Forbidden", status=403)
            return fn(*args, **kwargs)
        return inner
    return outer



def requires_perms(*perm_codes):
    """
    Require that the current user has ANY of the given permission codes.
    Example: @requires_perms('attendance.missed.approve')
    """
    def outer(fn):
        @wraps(fn)
        @jwt_required()
        def inner(*args, **kwargs):
            uid = get_jwt_identity()
            user = User.query.get(uid)
            if not user:
                return fail("Unauthorized", status=401)

            # Collect user's permissions via roles
            q = (db.session.query(Permission.code)
                 .join(RolePermission, RolePermission.permission_id == Permission.id)
                 .join(Role, Role.id == RolePermission.role_id)
                 .join(UserRole, UserRole.role_id == Role.id)
                 .filter(UserRole.user_id == user.id))
            user_perm_codes = set([row[0] for row in q.all()])

            if not any(pc in user_perm_codes for pc in perm_codes):
                return fail("Forbidden", status=403)

            return fn(*args, **kwargs)
        return inner
    return outer
