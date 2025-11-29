import os
from datetime import date

from hrms_api import create_app
from hrms_api.extensions import db
from hrms_api.models.master import Company
from hrms_api.models.payroll.cycle import PayCycle
from hrms_api.blueprints.pay_runs import _resolve_pay_cycle


def _mk_app():
    os.environ["DATABASE_URL"] = "sqlite:///:memory:"
    app = create_app()
    return app


def test_cycle_resolution_matches_and_priority():
    app = _mk_app()
    with app.app_context():
        db.create_all()
        c = Company(code="C1", name="C1")
        db.session.add(c); db.session.commit()
        # Two active cycles with overlapping windows; priority decides
        a = PayCycle(company_id=c.id, period_anchor_day=1, payday_rule={}, active=True,
                     effective_from=date(2025,1,1), effective_to=None, priority=20)
        b = PayCycle(company_id=c.id, period_anchor_day=1, payday_rule={}, active=True,
                     effective_from=date(2025,6,1), effective_to=None, priority=10)
        db.session.add_all([a, b]); db.session.commit()
        got = _resolve_pay_cycle(c.id, date(2025,9,1), date(2025,9,30))
        assert got.id == b.id


def test_cycle_resolution_latest_effective_from_tie():
    app = _mk_app()
    with app.app_context():
        db.create_all()
        c = Company(code="C2", name="C2")
        db.session.add(c); db.session.commit()
        # Same priority; pick latest effective_from
        a = PayCycle(company_id=c.id, period_anchor_day=1, payday_rule={}, active=True,
                     effective_from=date(2025,1,1), effective_to=None, priority=10)
        b = PayCycle(company_id=c.id, period_anchor_day=1, payday_rule={}, active=True,
                     effective_from=date(2025,7,1), effective_to=None, priority=10)
        db.session.add_all([a, b]); db.session.commit()
        got = _resolve_pay_cycle(c.id, date(2025,9,1), date(2025,9,30))
        assert got.id == b.id


def test_cycle_resolution_fallback_any_active():
    app = _mk_app()
    with app.app_context():
        db.create_all()
        c = Company(code="C3", name="C3")
        db.session.add(c); db.session.commit()
        # Active cycles but windows don't cover the period; fallback to any active by priority
        a = PayCycle(company_id=c.id, period_anchor_day=1, payday_rule={}, active=True,
                     effective_from=date(2024,1,1), effective_to=date(2024,12,31), priority=20)
        b = PayCycle(company_id=c.id, period_anchor_day=1, payday_rule={}, active=True,
                     effective_from=date(2025,1,1), effective_to=date(2025,3,31), priority=10)
        db.session.add_all([a, b]); db.session.commit()
        got = _resolve_pay_cycle(c.id, date(2025,9,1), date(2025,9,30))
        assert got.id == b.id


def test_cycle_resolution_no_active_returns_none():
    app = _mk_app()
    with app.app_context():
        db.create_all()
        c = Company(code="C4", name="C4")
        db.session.add(c); db.session.commit()
        # No active cycles
        PayCycle.query.delete(); db.session.commit()
        got = _resolve_pay_cycle(c.id, date(2025,9,1), date(2025,9,30))
        assert got is None

