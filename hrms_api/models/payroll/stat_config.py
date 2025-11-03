from datetime import datetime, date
from hrms_api.extensions import db


class StatConfig(db.Model):
    __tablename__ = "stat_configs"

    id = db.Column(db.Integer, primary_key=True)

    # Deprecated (v1) fields kept for compatibility with existing endpoints and data:
    # - scope: string label ("company", "global", etc.)
    scope = db.Column(db.String(30), default="company")
    # - company_id/state/fy: older scoping scheme
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"))
    state = db.Column(db.String(10))
    fy = db.Column(db.String(9))
    # - key: older code-like identifier used by existing pay_compliance endpoints
    key = db.Column(db.String(80), nullable=False)
    # legacy value holds JSON payload
    value_json = db.Column(db.JSON, nullable=False)
    # legacy effective window + created_at
    effective_from = db.Column(db.Date, nullable=False, default=date.today)
    effective_to = db.Column(db.Date)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # New v2 fields (do not remove older ones above; keep back-compat):
    # - type: PF, ESI, PT, LWF
    type = db.Column(db.Enum("PF", "ESI", "PT", "LWF", name="statconfig_type"))
    # - Scoping: either by company, state, both, or global (both NULL)
    scope_company_id = db.Column(db.Integer, nullable=True)
    scope_state = db.Column(db.String(10), nullable=True)  # e.g., "MH"
    priority = db.Column(db.Integer, nullable=False, default=100)
    created_by = db.Column(db.Integer)
    closed_by = db.Column(db.Integer)
    closed_at = db.Column(db.DateTime)

    __table_args__ = (
        # Keep old index for legacy lookups
        db.Index("ix_statcfg_scope_key", "scope", "key"),
        # New composite index to accelerate scoped resolution
        db.Index(
            "ix_statcfg_resolve",
            "type",
            "scope_state",
            "scope_company_id",
            "effective_from",
            "effective_to",
            "priority",
        ),
        # Additional composite index to help overlap checks by scope/company/state/code + window
        db.Index(
            "ix_statcfg_active_window",
            "type", "scope", "company_id", "state", "key", "effective_from", "effective_to"
        ),
    )
