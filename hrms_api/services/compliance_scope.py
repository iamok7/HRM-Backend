from __future__ import annotations
from datetime import date
from typing import List

from hrms_api.extensions import db
from hrms_api.models.payroll.stat_config import StatConfig


def resolve_configs(cfg_type: str, company_id: int | None, state: str | None, on_date: date) -> List[StatConfig]:
    """
    Return StatConfig records of a given type that are effective on `on_date`, ordered by resolution:
    1) company + state
    2) state-only
    3) company-only
    4) global (no scope)

    Within each tier, lower `priority` wins; tie-breaker is most-recent `effective_from`.

    Notes on value_json shapes (documented only; no validation is enforced here):
    - PF: {"emp_rate":0.12,"er_eps_rate":0.0833,"er_epf_rate":0.0367,"wage_cap":15000,"base_tag":"BASIC_DA","voluntary_max":0.12}
    - ESI: {"emp_rate":0.0075,"er_rate":0.0325,"threshold":21000,"entry_rule":"period_locking"}
    - PT (MH): {"state":"MH","slabs":[{"min":0,"max":7500,"amount":0},{"min":7501,"max":10000,"amount":175},{"min":10001,"max":9999999,"amount":200}],"double_month":null}
    - LWF: {"state":"MH","months":[6,12],"emp":12,"er":36}
    """

    # basic filter: type + effective range + not closed as of on_date (if closed_at is used)
    q = (
        StatConfig.query
        .filter(StatConfig.type == cfg_type)
        .filter(StatConfig.effective_from <= on_date)
        .filter((StatConfig.effective_to.is_(None)) | (StatConfig.effective_to >= on_date))
        .filter((StatConfig.closed_at.is_(None)) | (StatConfig.closed_at > on_date))
    )

    def _ordered(subq):
        return (
            subq.order_by(StatConfig.priority.asc(), StatConfig.effective_from.desc(), StatConfig.id.desc()).all()
        )

    # Tiers
    out: List[StatConfig] = []
    if company_id is not None and state:
        out.extend(_ordered(q.filter(StatConfig.scope_company_id == company_id, StatConfig.scope_state == state)))

    if state:
        out.extend(_ordered(q.filter(StatConfig.scope_company_id.is_(None), StatConfig.scope_state == state)))

    if company_id is not None:
        out.extend(_ordered(q.filter(StatConfig.scope_company_id == company_id, StatConfig.scope_state.is_(None))))

    out.extend(_ordered(q.filter(StatConfig.scope_company_id.is_(None), StatConfig.scope_state.is_(None))))

    return out

