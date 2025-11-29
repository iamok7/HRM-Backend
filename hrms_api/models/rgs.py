from hrms_api.extensions import db
import sqlalchemy as sa
from hrms_api.models.base import BaseModel

class RgsReport(BaseModel):
    __tablename__ = "rgs_reports"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(100), unique=True, nullable=False)
    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)

    category = db.Column(db.String(100), nullable=False)  # e.g. "attendance", "payroll"
    query_template = db.Column(db.Text, nullable=False)
    output_format = db.Column(db.String(20), nullable=False, default="xlsx")
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    
    # We'll rely on BaseModel for created_at/updated_at if it has them, 
    # but the spec explicitly asked for them. Let's see if BaseModel has them.
    # If BaseModel has them, we don't need to redefine, but to be safe and match spec exactly:
    created_at = db.Column(db.DateTime(timezone=True), server_default=sa.func.now())
    updated_at = db.Column(db.DateTime(timezone=True), onupdate=sa.func.now())

    parameters = db.relationship("RgsReportParameter", backref="report", cascade="all, delete-orphan", order_by="RgsReportParameter.order_index")

    __table_args__ = (
        db.Index("ix_rgs_reports_category", "category"),
    )

class RgsReportParameter(BaseModel):
    __tablename__ = "rgs_report_parameters"

    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.Integer, db.ForeignKey("rgs_reports.id", ondelete="CASCADE"), nullable=False)

    name = db.Column(db.String(100), nullable=False)
    label = db.Column(db.String(255), nullable=False)
    type = db.Column(db.String(50), nullable=False)    # "int", "string", "date", "enum", "bool"
    is_required = db.Column(db.Boolean, nullable=False, default=True)

    default_value = db.Column(db.String(255), nullable=True)
    enum_values = db.Column(db.JSON, nullable=True)

    order_index = db.Column(db.Integer, nullable=False, default=1)

class RgsRun(BaseModel):
    __tablename__ = "rgs_runs"

    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.Integer, db.ForeignKey("rgs_reports.id"), nullable=False)
    requested_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    status = db.Column(db.String(30), nullable=False, default="PENDING")
    params = db.Column(db.JSON, nullable=False)

    started_at = db.Column(db.DateTime(timezone=True), nullable=True)
    finished_at = db.Column(db.DateTime(timezone=True), nullable=True)

    error_message = db.Column(db.Text, nullable=True)

    report = db.relationship("RgsReport", backref="runs")
    requested_by = db.relationship("User", backref="report_runs")

    __table_args__ = (
        db.Index("ix_rgs_runs_report_status", "report_id", "status"),
        db.Index("ix_rgs_runs_user", "requested_by_user_id"),
    )

class RgsOutput(BaseModel):
    __tablename__ = "rgs_outputs"

    id = db.Column(db.Integer, primary_key=True)
    run_id = db.Column(db.Integer, db.ForeignKey("rgs_runs.id", ondelete="CASCADE"), nullable=False)

    storage_url = db.Column(db.Text, nullable=False)
    file_name = db.Column(db.String(255), nullable=False)
    mime_type = db.Column(db.String(100), nullable=False)
    size_bytes = db.Column(db.BigInteger, nullable=False)

    created_at = db.Column(db.DateTime(timezone=True), server_default=sa.func.now())

    run = db.relationship("RgsRun", backref="outputs")
