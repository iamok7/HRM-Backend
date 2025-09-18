from datetime import datetime
from hrms_api.extensions import db

class AttendancePunch(db.Model):
    __tablename__ = "attendance_punches"

    id          = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id", ondelete="CASCADE"),
                            nullable=False, index=True)
    ts          = db.Column(db.DateTime, nullable=False, index=True)  # store in server local/UTC consistently
    kind        = db.Column(db.String(10), nullable=False)            # "in" | "out"
    source      = db.Column(db.String(20), nullable=False, default="api")  # "api" | "device" | "manual"
    note        = db.Column(db.String(200))
    created_at  = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.CheckConstraint("kind in ('in','out')", name="ck_punch_kind"),
        db.Index("ix_punch_emp_ts", "employee_id", "ts"),
    )
