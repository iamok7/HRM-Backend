# apps/backend/hrms_api/models/security.py
from hrms_api.extensions import db

# ----------------------------
# Existing tables (UNCHANGED)
# ----------------------------
class Role(db.Model):
    __tablename__ = "roles"
    id   = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(50), unique=True, nullable=False)  # e.g., "admin", "hr"

    # relationships (do not affect schema)
    users = db.relationship(
        "UserRole",
        back_populates="role",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    permissions = db.relationship(
        "RolePermission",
        back_populates="role",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return f"<Role id={self.id} code={self.code!r}>"

class UserRole(db.Model):
    __tablename__ = "user_roles"
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role_id = db.Column(
        db.Integer,
        db.ForeignKey("roles.id", ondelete="CASCADE"),
        primary_key=True,
    )

    # relationships (do not affect schema)
    role = db.relationship("Role", back_populates="users")
    user = db.relationship(
        "User",
        backref=db.backref(
            "user_roles",
            cascade="all, delete-orphan",
            passive_deletes=True,
        ),
    )

    def __repr__(self) -> str:
        return f"<UserRole user_id={self.user_id} role_id={self.role_id}>"

# ----------------------------
# New RBAC tables (ADDITIVE)
# ----------------------------
class Permission(db.Model):
    __tablename__ = "permissions"
    id   = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(120), unique=True, nullable=False)  # e.g., "attendance.missed.approve"
    name = db.Column(db.String(150), nullable=True)  # optional human label

    roles = db.relationship(
        "RolePermission",
        back_populates="permission",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return f"<Permission id={self.id} code={self.code!r}>"

class RolePermission(db.Model):
    __tablename__ = "role_permissions"
    # composite PK keeps (role_id, permission_id) unique without extra index
    role_id = db.Column(
        db.Integer,
        db.ForeignKey("roles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    permission_id = db.Column(
        db.Integer,
        db.ForeignKey("permissions.id", ondelete="CASCADE"),
        primary_key=True,
    )

    role = db.relationship("Role", back_populates="permissions")
    permission = db.relationship("Permission", back_populates="roles")

    def __repr__(self) -> str:
        return f"<RolePermission role_id={self.role_id} permission_id={self.permission_id}>"

# ----------------------------
# Convenience helper
# ----------------------------
def user_permission_codes(user_id: int) -> set[str]:
    """
    Return a set of permission codes granted to the given user via roles.
    No schema impact; useful for quick checks/UI gating.
    """
    q = (
        db.session.query(Permission.code)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .join(Role, Role.id == RolePermission.role_id)
        .join(UserRole, UserRole.role_id == Role.id)
        .filter(UserRole.user_id == user_id)
    )
    return {row[0] for row in q.all()}
