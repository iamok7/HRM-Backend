from datetime import datetime, date
from hrms_api.extensions import db

class Adjustment(db.Model):
    __tablename__ = "adjustments"

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False, index=True)
    period = db.Column(db.String(7), nullable=False)  # YYYY-MM (pay period tag)

    type = db.Column(db.Enum("incentive", "bonus", "recovery", "arrear", name="adjustment_type_enum"), nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    reason = db.Column(db.String(255))
    meta_json = db.Column(db.JSON)

    created_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    employee = db.relationship("Employee", lazy="joined")
