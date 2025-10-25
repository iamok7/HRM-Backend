from datetime import datetime
from hrms_api.extensions import db


from .components import SalaryComponent 
class PayRun(db.Model):
    __tablename__ = "pay_runs"

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False)
    cycle_id = db.Column(db.Integer, db.ForeignKey("pay_cycles.id"))
    period_start = db.Column(db.Date, nullable=False)
    period_end = db.Column(db.Date, nullable=False)
    status = db.Column(db.Enum("draft", "locked", "posted", name="payrun_status_enum"), default="draft")
    retro_of_id = db.Column(db.Integer, db.ForeignKey("pay_runs.id"))

    created_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    company = db.relationship("Company", lazy="joined")
    cycle = db.relationship("PayCycle", lazy="joined")
    retro_of = db.relationship("PayRun", remote_side=[id], lazy="joined")


class PayRunItem(db.Model):
    __tablename__ = "pay_run_items"

    id = db.Column(db.Integer, primary_key=True)
    pay_run_id = db.Column(db.Integer, db.ForeignKey("pay_runs.id"), nullable=False, index=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False, index=True)

    gross = db.Column(db.Numeric(14, 2), default=0)
    earnings = db.Column(db.Numeric(14, 2), default=0)
    deductions = db.Column(db.Numeric(14, 2), default=0)
    net = db.Column(db.Numeric(14, 2), default=0)

    pt = db.Column(db.Numeric(14, 2), default=0)
    pf = db.Column(db.Numeric(14, 2), default=0)
    esi = db.Column(db.Numeric(14, 2), default=0)
    tds = db.Column(db.Numeric(14, 2), default=0)
    lwf = db.Column(db.Numeric(14, 2), default=0)

    remarks = db.Column(db.String(255))
    calc_meta = db.Column(db.JSON)  # summary of inputs used

    pay_run = db.relationship("PayRun", lazy="joined")
    employee = db.relationship("Employee", lazy="joined")


class PayRunItemLine(db.Model):
    __tablename__ = "pay_run_item_lines"

    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("pay_run_items.id"),
                        nullable=False, index=True)
    component_id = db.Column(db.Integer, db.ForeignKey("salary_components.id"),
                             nullable=False, index=True)

    amount = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    is_statutory = db.Column(db.Boolean, default=False)
    calc_trace_json = db.Column(db.JSON)

    item = db.relationship("PayRunItem", lazy="joined")
    component = db.relationship(SalaryComponent, lazy="joined") 
