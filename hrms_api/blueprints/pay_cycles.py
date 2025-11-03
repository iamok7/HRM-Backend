from __future__ import annotations
from flask import Blueprint, request, jsonify
from datetime import date
from hrms_api.extensions import db
from hrms_api.common.auth import requires_perms
from hrms_api.models.payroll.cycle import PayCycle

bp = Blueprint("pay_cycles", __name__, url_prefix="/api/v1/pay-cycles")

# -------- helpers ----------
def _ok(data=None, status=200, **meta):
    payload = {"success": True, "data": data}
    if meta:
        payload["meta"] = meta
    return jsonify(payload), status

def _fail(message, status=400, code=None, extra=None):
    payload = {"success": False, "error": {"message": message}}
    if code:
        payload["error"]["code"] = code
    if extra:
        payload["error"]["extra"] = extra
    return jsonify(payload), status

def _page_limit():
    try:
        page = max(int(request.args.get("page", 1)), 1)
        size = min(max(int(request.args.get("size", 20)), 1), 100)
    except Exception:
        page, size = 1, 20
    return page, size

def _row(x: PayCycle):
    return {
        "id": x.id,
        "company_id": x.company_id,
        "period_anchor_day": x.period_anchor_day,  # 1..28
        "payday_rule": x.payday_rule,              # JSON (dict) in your model
        "timezone": x.timezone,
        "active": x.active,
        "effective_from": x.effective_from.isoformat() if getattr(x, "effective_from", None) else None,
        "effective_to": x.effective_to.isoformat() if getattr(x, "effective_to", None) else None,
        "priority": getattr(x, "priority", None),
        "created_at": x.created_at.isoformat() if getattr(x, "created_at", None) else None,
    }

# -------- routes ----------
@bp.post("")
@requires_perms("payroll.cycle.write")
def create_cycle():
    j = request.get_json(silent=True) or {}
    company_id = j.get("company_id")
    pad = j.get("period_anchor_day")
    payday_rule = j.get("payday_rule")
    tz = j.get("timezone") or "Asia/Kolkata"
    active = bool(j.get("active", True))
    eff_from = j.get("effective_from")
    eff_to = j.get("effective_to")
    priority = j.get("priority")

    if not company_id or pad is None:
        return _fail("company_id and period_anchor_day are required", 422)

    try:
        pad = int(pad)
    except Exception:
        return _fail("period_anchor_day must be an integer", 422)

    if not (1 <= pad <= 28):
        return _fail("period_anchor_day must be between 1 and 28", 422)

    # Optional: light validation for payday_rule JSON shape
    if payday_rule is not None and not isinstance(payday_rule, (dict, list)):
        return _fail("payday_rule must be an object or array (JSON)", 422)

    # Optional: parse effective dates and priority
    eff_from_d = None
    eff_to_d = None
    if eff_from:
        try:
            eff_from_d = date.fromisoformat(str(eff_from))
        except Exception:
            return _fail("effective_from must be YYYY-MM-DD", 422)
    if eff_to:
        try:
            eff_to_d = date.fromisoformat(str(eff_to))
        except Exception:
            return _fail("effective_to must be YYYY-MM-DD", 422)
        if eff_from_d and eff_to_d < eff_from_d:
            return _fail("effective_to must be >= effective_from", 422)
    prio = None
    if priority is not None:
        try:
            prio = int(priority)
        except Exception:
            return _fail("priority must be integer", 422)

    x = PayCycle(
        company_id=company_id,
        period_anchor_day=pad,
        payday_rule=payday_rule,
        timezone=tz,
        active=active,
        effective_from=eff_from_d,
        effective_to=eff_to_d,
        priority=prio if prio is not None else 100,
    )
    db.session.add(x)
    db.session.commit()
    return _ok(_row(x), 201)

@bp.get("")
@requires_perms("payroll.cycle.read")
def list_cycles():
    q = PayCycle.query
    company_id = request.args.get("company_id")
    if company_id:
        try:
            q = q.filter(PayCycle.company_id == int(company_id))
        except Exception:
            return _fail("company_id must be integer", 422)

    if "active" in request.args:
        want = request.args.get("active").lower() in ("1", "true", "yes")
        q = q.filter(PayCycle.active == want)

    # Order: priority asc, effective_from desc, id desc if fields present
    try:
        q = q.order_by(PayCycle.priority.asc(), PayCycle.effective_from.desc().nullslast(), PayCycle.id.desc())
    except Exception:
        q = q.order_by(PayCycle.id.desc())
    page, size = _page_limit()
    total = q.count()
    rows = q.offset((page - 1) * size).limit(size).all()
    return _ok([_row(x) for x in rows], meta={"page": page, "size": size, "total": total})

@bp.get("/<int:cycle_id>")
@requires_perms("payroll.cycle.read")
def get_cycle(cycle_id: int):
    x = PayCycle.query.get_or_404(cycle_id)
    return _ok(_row(x))

@bp.patch("/<int:cycle_id>")
@requires_perms("payroll.cycle.write")
def patch_cycle(cycle_id: int):
    x = PayCycle.query.get_or_404(cycle_id)
    j = request.get_json(silent=True) or {}

    if "period_anchor_day" in j:
        try:
            pad = int(j.get("period_anchor_day"))
        except Exception:
            return _fail("period_anchor_day must be an integer", 422)
        if not (1 <= pad <= 28):
            return _fail("period_anchor_day must be between 1 and 28", 422)
        x.period_anchor_day = pad

    if "payday_rule" in j:
        pr = j.get("payday_rule")
        if pr is not None and not isinstance(pr, (dict, list)):
            return _fail("payday_rule must be an object or array (JSON)", 422)
        x.payday_rule = pr

    if "timezone" in j:
        tz = (j.get("timezone") or "").strip() or "Asia/Kolkata"
        x.timezone = tz

    if "active" in j:
        x.active = bool(j.get("active"))

    # Optional fields
    if "effective_from" in j:
        v = j.get("effective_from")
        if v:
            try:
                x.effective_from = date.fromisoformat(str(v))
            except Exception:
                return _fail("effective_from must be YYYY-MM-DD", 422)
        else:
            x.effective_from = None
    if "effective_to" in j:
        v = j.get("effective_to")
        if v:
            try:
                x.effective_to = date.fromisoformat(str(v))
            except Exception:
                return _fail("effective_to must be YYYY-MM-DD", 422)
        else:
            x.effective_to = None
    if x.effective_from and x.effective_to and x.effective_to < x.effective_from:
        return _fail("effective_to must be >= effective_from", 422)
    if "priority" in j:
        try:
            x.priority = int(j.get("priority")) if j.get("priority") is not None else x.priority
        except Exception:
            return _fail("priority must be integer", 422)

    db.session.commit()
    return _ok(_row(x))
