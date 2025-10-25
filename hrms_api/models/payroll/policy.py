from datetime import datetime, date
from hrms_api.extensions import db

class PayPolicy(db.Model):
    __tablename__ = "pay_policies"

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False)

    holiday_paid = db.Column(db.Boolean, default=True)
    weekly_off_paid = db.Column(db.Boolean, default=True)
    monthly_fixed_paid_leaves = db.Column(db.Integer, default=2)
    daily_paid_leave_allowed = db.Column(db.Boolean, default=False)

    ot_factor_default = db.Column(db.Numeric(6, 2), default=2.00)  # 2x by default
    min_wage_check = db.Column(db.Boolean, default=True)

    effective_from = db.Column(db.Date, nullable=False, default=date.today)
    effective_to = db.Column(db.Date)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    company = db.relationship("Company", lazy="joined")

    __table_args__ = (
        db.Index("ix_pay_policies_company_active", "company_id", "effective_from", "effective_to"),
    )
