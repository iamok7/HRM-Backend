from datetime import datetime
from hrms_api.extensions import db

class AttendanceRollup(db.Model):
    __tablename__ = "attendance_rollups"

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    
    year = db.Column(db.Integer, nullable=False)
    month = db.Column(db.Integer, nullable=False)
    
    present_days = db.Column(db.Float, default=0.0)
    absent_days = db.Column(db.Float, default=0.0)
    leave_days = db.Column(db.Float, default=0.0)
    weekly_off_days = db.Column(db.Float, default=0.0)
    holiday_days = db.Column(db.Float, default=0.0)
    lop_days = db.Column(db.Float, default=0.0)
    ot_hours = db.Column(db.Float, default=0.0)
    
    total_working_days = db.Column(db.Float, default=0.0)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("employee_id", "year", "month", name="uq_attendance_rollup_emp_month"),
        db.Index("ix_attendance_rollup_period", "company_id", "year", "month"),
    )
