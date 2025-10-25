from datetime import datetime, date
from hrms_api.extensions import db

class EmployeePayProfile(db.Model):
    __tablename__ = "employee_pay_profile"

    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), primary_key=True)

    # effective-dated profile (composite key)
    effective_from = db.Column(db.Date, primary_key=True)
    effective_to = db.Column(db.Date)

    # core config
    pay_type = db.Column(db.Enum("monthly_fixed", "daily_wage", name="pay_type_enum"), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey("trade_categories.id"))

    # overrides
    per_day_override = db.Column(db.Numeric(12, 2))
    ot_rate_override = db.Column(db.Numeric(12, 2))
    monthly_gross = db.Column(db.Numeric(12, 2))
    allowed_paid_leaves = db.Column(db.Integer)

    # statutory flags (per-employee enable/disable)
    pf_enabled = db.Column(db.Boolean, default=True)
    esi_enabled = db.Column(db.Boolean, default=False)
    pt_enabled = db.Column(db.Boolean, default=True)
    lwf_enabled = db.Column(db.Boolean, default=True)

    # income tax regime (lite v1)
    regime = db.Column(db.Enum("old", "new", name="tax_regime_enum"), default="new")

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    employee = db.relationship("Employee", lazy="joined")
    category = db.relationship("TradeCategory", lazy="joined")
