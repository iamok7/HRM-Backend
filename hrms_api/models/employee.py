from datetime import datetime, date
from hrms_api.extensions import db

class Employee(db.Model):
    __tablename__ = "employees"

    id = db.Column(db.Integer, primary_key=True)
    # business
    company_id     = db.Column(db.Integer, db.ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False)
    location_id    = db.Column(db.Integer, db.ForeignKey("locations.id", ondelete="RESTRICT"), nullable=True)
    department_id  = db.Column(db.Integer, db.ForeignKey("departments.id", ondelete="RESTRICT"), nullable=True)
    designation_id = db.Column(db.Integer, db.ForeignKey("designations.id", ondelete="RESTRICT"), nullable=True)
    grade_id       = db.Column(db.Integer, db.ForeignKey("grades.id", ondelete="RESTRICT"), nullable=True)
    cost_center_id = db.Column(db.Integer, db.ForeignKey("cost_centers.id", ondelete="RESTRICT"), nullable=True)
    manager_id     = db.Column(db.Integer, db.ForeignKey("employees.id", ondelete="SET NULL"), nullable=True)
    user_id        = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, unique=True)

    code  = db.Column(db.String(32), nullable=False)    # unique per company
    email = db.Column(db.String(255), unique=True, nullable=False)
    first_name = db.Column(db.String(80), nullable=False)
    last_name  = db.Column(db.String(80), nullable=True)
    phone      = db.Column(db.String(20), nullable=True)

    doj = db.Column(db.Date, nullable=True)   # date of joining
    dol = db.Column(db.Date, nullable=True)   # date of leaving (null if active)
    employment_type = db.Column(db.String(20), default="fulltime", nullable=False)  # fulltime/parttime/contract
    status = db.Column(db.String(16), default="active", nullable=False)             # active/inactive

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("company_id", "code", name="uq_employee_company_code"),
        db.Index("ix_emp_company_id", "company_id"),
        db.Index("ix_emp_dept_id", "department_id"),
        db.Index("ix_emp_location_id", "location_id"),
        db.Index("ix_emp_manager_id", "manager_id"),
    )

    # relationships (optional for name lookups)
    company     = db.relationship("Company", lazy="joined")
    location    = db.relationship("Location", lazy="joined")
    department  = db.relationship("Department", lazy="joined")
    designation = db.relationship("Designation", lazy="joined")
    grade       = db.relationship("Grade", lazy="joined")
    cost_center = db.relationship("CostCenter", lazy="joined")
    manager     = db.relationship("Employee", remote_side=[id], lazy="joined")
