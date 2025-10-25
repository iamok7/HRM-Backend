from datetime import datetime, date
from hrms_api.extensions import db

class SalaryComponent(db.Model):
    __tablename__ = "salary_components"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(50), unique=True, nullable=False)  # BASIC, HRA, PF_EMP, ...
    name = db.Column(db.String(120), nullable=False)
    type = db.Column(db.Enum("earning", "deduction", name="component_type_enum"), nullable=False)

    # flags controlling statutory bases
    pf_wage_flag = db.Column(db.Boolean, default=False)
    esi_wage_flag = db.Column(db.Boolean, default=False)
    pt_applicable_flag = db.Column(db.Boolean, default=False)
    lwf_applicable_flag = db.Column(db.Boolean, default=False)

    taxability = db.Column(db.String(50))  # taxable, exempt, partial...
    formula_expr = db.Column(db.Text)      # expression for engine (nullable)
    priority = db.Column(db.Integer, default=100)

    effective_from = db.Column(db.Date, nullable=False, default=date.today)
    effective_to = db.Column(db.Date)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class EmployeeSalary(db.Model):
    __tablename__ = "employee_salary"

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False, index=True)
    component_id = db.Column(db.Integer, db.ForeignKey("salary_components.id"), nullable=False)
    amount = db.Column(db.Numeric(12, 2))
    formula_override = db.Column(db.Text)

    effective_from = db.Column(db.Date, nullable=False, default=date.today)
    effective_to = db.Column(db.Date)

    employee = db.relationship("Employee", lazy="joined")
    component = db.relationship("SalaryComponent", lazy="joined")

    __table_args__ = (
        db.UniqueConstraint("employee_id", "component_id", "effective_from", name="uq_emp_comp_from"),
    )
