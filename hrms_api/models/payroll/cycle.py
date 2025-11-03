from datetime import datetime, date
from hrms_api.extensions import db

class PayCycle(db.Model):
    __tablename__ = "pay_cycles"

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False)
    period_anchor_day = db.Column(db.Integer, nullable=False)  # 1..28 ⇒ month roll window
    payday_rule = db.Column(db.JSON, nullable=False, default={})  # {"type":"FIXED_DAY","day":5}
    timezone = db.Column(db.String(64), default="Asia/Kolkata")
    active = db.Column(db.Boolean, default=True)
    # Optional versioning/priority for auto-resolution (nullable for back-compat)
    effective_from = db.Column(db.Date, nullable=True)
    effective_to = db.Column(db.Date, nullable=True)
    priority = db.Column(db.Integer, nullable=False, default=100)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    company = db.relationship("Company", lazy="joined")
    __table_args__ = (
        db.Index("ix_pay_cycles_resolve", "company_id", "active", "effective_from", "effective_to", "priority"),
    )
