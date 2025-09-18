from datetime import datetime, date, timedelta
from hrms_api.extensions import db

class EmployeeShiftAssignment(db.Model):
    __tablename__ = "employee_shift_assignments"

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True)
    shift_id    = db.Column(db.Integer, db.ForeignKey("shifts.id",    ondelete="RESTRICT"), nullable=False, index=True)

    start_date  = db.Column(db.Date, nullable=False, index=True)
    end_date    = db.Column(db.Date, nullable=True, index=True)  # null = open-ended

    created_at  = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.Index("ix_emp_shift_range", "employee_id", "start_date", "end_date"),
    )
