from datetime import datetime
from hrms_api.extensions import db

class EmployeeAddress(db.Model):
    __tablename__ = "employee_addresses"

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(
        db.Integer, db.ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # 'current' or 'permanent'
    type = db.Column(db.String(20), nullable=False)

    line1 = db.Column(db.String(120), nullable=False)
    line2 = db.Column(db.String(120), nullable=True)
    city = db.Column(db.String(60), nullable=False)
    state = db.Column(db.String(60), nullable=True)
    pincode = db.Column(db.String(20), nullable=True)
    country = db.Column(db.String(60), nullable=True, default="India")

    is_primary = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("employee_id", "type", name="uq_empaddr_emp_type"),
        db.Index("ix_empaddr_primary", "employee_id", "is_primary"),
    )
