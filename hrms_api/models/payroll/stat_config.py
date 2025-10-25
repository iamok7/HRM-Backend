from datetime import datetime, date
from hrms_api.extensions import db

class StatConfig(db.Model):
    __tablename__ = "stat_configs"

    id = db.Column(db.Integer, primary_key=True)
    scope = db.Column(db.String(30), default="company")  # global/company/location/state
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"))
    state = db.Column(db.String(10))                     # e.g., MH
    fy = db.Column(db.String(9))                         # e.g., 2025-26

    key = db.Column(db.String(80), nullable=False)       # pf.wage_ceiling, pt.mh.slabs, ...
    value_json = db.Column(db.JSON, nullable=False)

    effective_from = db.Column(db.Date, nullable=False, default=date.today)
    effective_to = db.Column(db.Date)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.Index("ix_statcfg_scope_key", "scope", "key"),
    )
