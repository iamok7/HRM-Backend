from datetime import datetime, date
from hrms_api.extensions import db

class LeaveType(db.Model):
    __tablename__ = "leave_types"
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    code = db.Column(db.String(20), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    is_paid = db.Column(db.Boolean, nullable=False, default=True)
    is_comp_off = db.Column(db.Boolean, nullable=False, default=False)
    allow_half_day = db.Column(db.Boolean, nullable=False, default=True)
    requires_document = db.Column(db.Boolean, nullable=False, default=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, onupdate=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("company_id", "code", name="uq_leave_type_company_code"),
    )

class EmployeeLeaveBalance(db.Model):
    __tablename__ = "employee_leave_balances"
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True)
    leave_type_id = db.Column(db.Integer, db.ForeignKey("leave_types.id", ondelete="CASCADE"), nullable=False, index=True)
    year = db.Column(db.Integer, nullable=False)
    opening_balance = db.Column(db.Numeric(5,2), nullable=False, default=0)
    accrued = db.Column(db.Numeric(5,2), nullable=False, default=0)
    used = db.Column(db.Numeric(5,2), nullable=False, default=0)
    adjusted = db.Column(db.Numeric(5,2), nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, onupdate=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("employee_id", "leave_type_id", "year", name="uq_emp_leave_bal_year"),
    )

    @property
    def available(self):
        return float(self.opening_balance) + float(self.accrued) + float(self.adjusted) - float(self.used)

    leave_type = db.relationship("LeaveType")

class LeaveRequest(db.Model):
    __tablename__ = "leave_requests"
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False, index=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True)
    leave_type_id = db.Column(db.Integer, db.ForeignKey("leave_types.id", ondelete="RESTRICT"), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    is_half_day = db.Column(db.Boolean, default=False)
    total_days = db.Column(db.Numeric(5,2), nullable=False)
    reason = db.Column(db.Text)
    status = db.Column(db.String(20), nullable=False, default="pending")  # pending|approved|rejected|cancelled
    
    applied_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    approved_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    approved_at = db.Column(db.DateTime)
    rejection_reason = db.Column(db.Text)
    
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, onupdate=datetime.utcnow)

    # Relationships
    employee = db.relationship("Employee", backref="leave_requests")
    leave_type = db.relationship("LeaveType")
    applied_by = db.relationship("User", foreign_keys=[applied_by_user_id])
    approved_by = db.relationship("User", foreign_keys=[approved_by_user_id])

class LeaveApprovalAction(db.Model):
    __tablename__ = "leave_approval_actions"
    id = db.Column(db.Integer, primary_key=True)
    leave_request_id = db.Column(db.Integer, db.ForeignKey("leave_requests.id", ondelete="CASCADE"), nullable=False, index=True)
    action = db.Column(db.String(20), nullable=False)  # applied|approved|rejected|cancelled
    comment = db.Column(db.Text)
    acted_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    acted_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    acted_by = db.relationship("User")

class CompOffCredit(db.Model):
    __tablename__ = "comp_off_credits"
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False)
    date_earned = db.Column(db.Date, nullable=False)
    hours_or_days = db.Column(db.Numeric(5,2), nullable=False, default=1.0)
    reason = db.Column(db.Text)
    status = db.Column(db.String(20), nullable=False, default="available")  # available|used|expired|cancelled
    linked_leave_request_id = db.Column(db.Integer, db.ForeignKey("leave_requests.id"), nullable=True)
    
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, onupdate=datetime.utcnow)

    employee = db.relationship("Employee", backref="comp_off_credits")

class LeavePolicy(db.Model):
    __tablename__ = "leave_policies"

    id = db.Column(db.Integer, primary_key=True)

    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False)
    leave_type_id = db.Column(db.Integer, db.ForeignKey("leave_types.id"), nullable=False)

    # Optional scoping – v1 we support grade-level overrides on top of company default
    grade_id = db.Column(db.Integer, db.ForeignKey("grades.id"), nullable=True)
    # (If grade_id is null => company-wide default for that leave type)

    year = db.Column(db.Integer, nullable=False)

    # Core HR controls
    entitlement_per_year = db.Column(db.Numeric(5, 2), nullable=False)  # e.g. 7.00 CL, 6.00 SL
    carry_forward_max = db.Column(db.Numeric(5, 2), nullable=True)      # optional – can be null
    allow_negative = db.Column(db.Boolean, default=False)
    max_negative_balance = db.Column(db.Numeric(5, 2), nullable=True)   # e.g. -2.00 days

    accrual_pattern = db.Column(db.String(20), nullable=False, default="annual_fixed")
    # allowed values (v1):
    # "annual_fixed" – full entitlement at start of year
    # (we store field now; we can add "monthly_prorata" later)

    is_active = db.Column(db.Boolean, default=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    __table_args__ = (
        db.UniqueConstraint(
            "company_id", "leave_type_id", "grade_id", "year",
            name="uq_leave_policy_company_type_grade_year"
        ),
    )

    # Relationships
    company = db.relationship("Company")
    leave_type = db.relationship("LeaveType")
    grade = db.relationship("Grade")
