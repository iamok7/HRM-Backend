from datetime import datetime
from hrms_api.extensions import db

class PayCycle(db.Model):
    __tablename__ = "pay_cycles"

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False)
    period_anchor_day = db.Column(db.Integer, nullable=False)  # 1..28 ⇒ month roll window
    payday_rule = db.Column(db.JSON, nullable=False, default={})  # {"type":"FIXED_DAY","day":5}
    timezone = db.Column(db.String(64), default="Asia/Kolkata")
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    company = db.relationship("Company", lazy="joined")
