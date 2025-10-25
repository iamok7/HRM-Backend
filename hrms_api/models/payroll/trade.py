# hrms_api/models/payroll/trade.py
from __future__ import annotations
from datetime import datetime, date
from decimal import Decimal
from hrms_api.extensions import db

class TradeCategory(db.Model):
    __tablename__ = "trade_categories"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(64), nullable=False, index=True)        # e.g. PAINTER
    name = db.Column(db.String(128), nullable=False)

    per_day_rate = db.Column(db.Numeric(10, 2))
    ot_rate      = db.Column(db.Numeric(10, 2))

    min_wage_zone  = db.Column(db.String(32))
    min_wage_skill = db.Column(db.String(32))

    effective_from = db.Column(db.Date, nullable=False, index=True)
    effective_to   = db.Column(db.Date, index=True)

    is_active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.Index("ix_trade_code_effective", "code", "effective_from", "effective_to"),
    )
