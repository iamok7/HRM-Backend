from datetime import date
import os

from hrms_api import create_app
from hrms_api.extensions import db
from hrms_api.models.master import Company
from hrms_api.models.employee import Employee
from hrms_api.models.payroll.pay_run import PayRun, PayRunItem
from hrms_api.models.payroll.stat_config import StatConfig
from hrms_api.blueprints.pay_compliance import _build_preview


def _mk_app():
    os.environ["DATABASE_URL"] = "sqlite:///:memory:"
    app = create_app()
    return app


def test_preview_math_sample():
    app = _mk_app()
    with app.app_context():
        db.create_all()

        c = Company(code="T1", name="Test Co")
        db.session.add(c); db.session.commit()

        e = Employee(company_id=c.id, code="E001", email="e1@test.local", first_name="Test", last_name="Emp")
        db.session.add(e); db.session.commit()

        run = PayRun(company_id=c.id, period_start=date(2025,9,1), period_end=date(2025,9,30), status="calculated")
        db.session.add(run); db.session.commit()

        it = PayRunItem(pay_run_id=run.id, employee_id=e.id, gross=20000)
        # Use JSON field as components payload (BASIC=15000)
        it.calc_meta = [{"code": "BASIC", "amount": 15000}]
        db.session.add(it); db.session.commit()

        # Seed v2 configs
        pf = StatConfig(type="PF", scope_company_id=c.id, scope_state="MH", priority=100,
                        effective_from=date(2025,4,1), value_json={
                            "emp_rate":0.12, "er_eps_rate":0.0833, "er_epf_rate":0.0367,
                            "wage_cap":15000, "base_tag":"BASIC"
                        }, key="TEST_PF")
        esi = StatConfig(type="ESI", scope_company_id=c.id, scope_state="MH", priority=100,
                         effective_from=date(2025,4,1), value_json={
                             "emp_rate":0.0075, "er_rate":0.0325, "threshold":21000, "entry_rule":"period_locking"
                         }, key="TEST_ESI")
        pt = StatConfig(type="PT", scope_company_id=c.id, scope_state="MH", priority=100,
                        effective_from=date(2025,4,1), value_json={
                            "state":"MH", "slabs":[
                                {"min":0,"max":7500,"amount":0},
                                {"min":7501,"max":10000,"amount":175},
                                {"min":10001,"max":9999999,"amount":200}
                            ], "double_month": None
                        }, key="TEST_PT")
        db.session.add_all([pf, esi, pt]); db.session.commit()

        prev = _build_preview(run.id, "MH")
        assert prev["can_apply"] is True
        emp = prev["employees"][0]
        # PF checks
        assert round(emp["pf"]["emp_12pct"], 2) == 1800.00
        assert round(emp["pf"]["er_eps_8_33pct"], 2) == 1249.50
        assert round(emp["pf"]["er_epf_3_67pct"], 2) == 550.50
        # ESI checks
        assert round(emp["esi"]["emp_amount"], 2) == 150.00
        assert round(emp["esi"]["er_amount"], 2) == 650.00
        # PT
        assert round(emp["pt"]["amount"], 2) == 200.00

        tots = prev["totals"]
        assert round(tots["emp_deductions"]["all"], 2) == 1800.00 + 150.00 + 200.00
        assert round(tots["employer_costs"]["all"], 2) == 650.00 + 1249.50 + 550.50

