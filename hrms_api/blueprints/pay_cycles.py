from __future__ import annotations
from flask import Blueprint, request, jsonify
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

    x = PayCycle(
        company_id=company_id,
        period_anchor_day=pad,
        payday_rule=payday_rule,
        timezone=tz,
        active=active,
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

    db.session.commit()
    return _ok(_row(x))
