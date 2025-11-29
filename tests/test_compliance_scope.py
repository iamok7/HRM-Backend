import os
from datetime import date

import pytest

from hrms_api import create_app
from hrms_api.extensions import db
from hrms_api.models.payroll.stat_config import StatConfig
from hrms_api.services.compliance_scope import resolve_configs


@pytest.fixture(scope="function")
def app():
    os.environ["DATABASE_URL"] = "sqlite:///:memory:"
    app = create_app()
    with app.app_context():
        db.create_all()
        yield app


@pytest.fixture(scope="function")
def session(app):
    with app.app_context():
        yield db.session


def _add(session, **kw):
    sc = StatConfig(**kw)
    session.add(sc)
    return sc


def test_resolve_ordering_mixed_scopes(session):
    on = date(2024, 6, 1)

    # global
    g = _add(session,
             type="PF",
             scope_company_id=None,
             scope_state=None,
             priority=50,
             effective_from=on,
             value_json={},
             key="TEST_PF")

    # company-only
    co = _add(session,
              type="PF",
              scope_company_id=1,
              scope_state=None,
              priority=20,
              effective_from=on,
              value_json={},
              key="TEST_PF")

    # state-only
    so = _add(session,
              type="PF",
              scope_company_id=None,
              scope_state="MH",
              priority=10,
              effective_from=on,
              value_json={},
              key="TEST_PF")

    # company+state
    cs = _add(session,
              type="PF",
              scope_company_id=1,
              scope_state="MH",
              priority=5,
              effective_from=on,
              value_json={},
              key="TEST_PF")

    session.commit()

    out = resolve_configs("PF", company_id=1, state="MH", on_date=on)
    assert [x.id for x in out] == [cs.id, so.id, co.id, g.id]


def test_priority_tie_breaks_by_effective_from(session):
    on = date(2024, 6, 1)

    a = _add(session, type="ESI", scope_company_id=2, scope_state="KA", priority=10,
             effective_from=date(2024, 1, 1), value_json={}, key="TEST_ESI")
    b = _add(session, type="ESI", scope_company_id=2, scope_state="KA", priority=10,
             effective_from=date(2024, 5, 1), value_json={}, key="TEST_ESI")
    session.commit()

    out = resolve_configs("ESI", company_id=2, state="KA", on_date=on)
    # same tier + same priority -> most recent effective_from first
    assert [x.id for x in out][:2] == [b.id, a.id]

