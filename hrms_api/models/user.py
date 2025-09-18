from datetime import datetime
from hrms_api.extensions import db
from werkzeug.security import generate_password_hash, check_password_hash

class User(db.Model):
    __tablename__ = "users"

    id           = db.Column(db.Integer, primary_key=True)
    email        = db.Column(db.String(255), unique=True, index=True, nullable=False)
    password_hash= db.Column(db.String(255), nullable=False)
    full_name    = db.Column(db.String(255), nullable=False)
    status       = db.Column(db.String(20), default="active")
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)

    # --- helpers ---
    def set_password(self, raw: str):
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw: str) -> bool:
        return check_password_hash(self.password_hash, raw)

    # roles = db.relationship("Role", secondary="user_roles", lazy="joined")

    roles = db.relationship(
    "Role",
    secondary="user_roles",
    lazy="joined",
    viewonly=True,                     # make it read-only shortcut
    overlaps="user_roles,user,role,users"
)


    def role_codes(self):
        return [r.code for r in self.roles]

    # Optional convenience: resolve mapped employee id without adding columns.
    @property
    def employee_id(self):
        """
        Returns the first Employee.id linked via Employee.user_id == self.id, or None.
        Avoids circular import by importing inside the property.
        """
        try:
            from hrms_api.models.employee import Employee  # late import to avoid circulars
            emp = Employee.query.filter_by(user_id=self.id).first()
            return emp.id if emp else None
        except Exception:
            return None
