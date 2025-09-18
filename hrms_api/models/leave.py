from datetime import datetime, date
from hrms_api.extensions import db

class LeaveType(db.Model):
    __tablename__ = "leave_types"
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    code = db.Column(db.String(20), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    unit = db.Column(db.String(10), nullable=False, default="day")  # day | half | hour
    paid = db.Column(db.Boolean, nullable=False, default=True)
    accrual_per_month = db.Column(db.Numeric(5,2), nullable=False, default=0)  # e.g., 1.0 per month
    carry_forward_limit = db.Column(db.Numeric(6,2))
    negative_balance_allowed = db.Column(db.Boolean, nullable=False, default=False)
    requires_approval = db.Column(db.Boolean, nullable=False, default=True)
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("company_id", "code", name="uq_leave_type_company_code"),
    )

class LeaveBalance(db.Model):
    __tablename__ = "leave_balances"
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True)
    leave_type_id = db.Column(db.Integer, db.ForeignKey("leave_types.id", ondelete="CASCADE"), nullable=False, index=True)
    balance = db.Column(db.Numeric(6,2), nullable=False, default=0)
    ytd_accrued = db.Column(db.Numeric(6,2), nullable=False, default=0)
    ytd_taken = db.Column(db.Numeric(6,2), nullable=False, default=0)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("employee_id", "leave_type_id", name="uq_leave_balance_emp_type"),
    )

class LeaveRequest(db.Model):
    __tablename__ = "leave_requests"
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True)
    leave_type_id = db.Column(db.Integer, db.ForeignKey("leave_types.id", ondelete="RESTRICT"), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    part_day = db.Column(db.String(10))  # 'am'|'pm'|'half' or None
    days = db.Column(db.Numeric(5,2), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="draft")  # draft|pending|approved|rejected|cancelled
    reason = db.Column(db.String(500))
    approver_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    approved_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
