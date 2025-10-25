from __future__ import annotations
from datetime import date, timedelta
from decimal import Decimal
from flask import Blueprint, request, jsonify

from hrms_api.extensions import db
from hrms_api.common.auth import requires_perms
from hrms_api.models.payroll.policy import PayPolicy

bp = Blueprint("pay_policies", __name__, url_prefix="/api/v1/pay-policies")

# ---------- helpers ----------
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

def _d(s):
    if not s:
        return None
    try:
        return date.fromisoformat(str(s))
    except Exception:
        return None

def _dec(x):
    if x is None or x == "":
        return None
    try:
        return Decimal(str(x))
    except Exception:
        return None

def _bool(x):
    if isinstance(x, bool):
        return x
    if x is None:
        return None
    return str(x).lower() in ("1", "true", "yes", "y")

def _row(p: PayPolicy):
    def fnum(v):
        try:
            return float(v) if v is not None else None
        except Exception:
            return None
    return {
        "id": p.id,
        "company_id": p.company_id,

        # core flags / values (match your model)
        "holiday_paid": p.holiday_paid,
        "weekly_off_paid": p.weekly_off_paid,
        "monthly_fixed_paid_leaves": p.monthly_fixed_paid_leaves,
        "daily_paid_leave_allowed": p.daily_paid_leave_allowed,

        "ot_factor_default": fnum(p.ot_factor_default),
        "min_wage_check": p.min_wage_check,

        # effective dating
        "effective_from": p.effective_from.isoformat() if p.effective_from else None,
        "effective_to": p.effective_to.isoformat() if p.effective_to else None,

        "is_active": p.is_active,
        "created_at": p.created_at.isoformat() if getattr(p, "created_at", None) else None,
    }

def _overlap_exists(company_id: int, eff_from: date, eff_to: date | None, exclude_id: int | None = None) -> bool:
    q = PayPolicy.query.filter(
        PayPolicy.company_id == company_id,
        PayPolicy.is_active == True,
    )
    if exclude_id:
        q = q.filter(PayPolicy.id != exclude_id)
    this_to = eff_to or date.max
    for other in q.all():
        o_from = other.effective_from or date.min
        o_to = other.effective_to or date.max
        if eff_from <= o_to and o_from <= this_to:
            return True
    return False

# ---------- routes ----------
@bp.post("")
@requires_perms("payroll.policy.write")
def create_policy():
    j = request.get_json(silent=True) or {}
    company_id = j.get("company_id")
    if not company_id:
        return _fail("company_id is required", 422)

    # parse / validate fields
    holiday_paid = _bool(j.get("holiday_paid"))
    weekly_off_paid = _bool(j.get("weekly_off_paid"))
    daily_paid_leave_allowed = _bool(j.get("daily_paid_leave_allowed"))
    min_wage_check = _bool(j.get("min_wage_check"))

    monthly_fixed_paid_leaves = j.get("monthly_fixed_paid_leaves")
    try:
        if monthly_fixed_paid_leaves is not None:
            monthly_fixed_paid_leaves = int(monthly_fixed_paid_leaves)
            if monthly_fixed_paid_leaves < 0:
                return _fail("monthly_fixed_paid_leaves cannot be negative", 422)
    except Exception:
        return _fail("monthly_fixed_paid_leaves must be integer", 422)

    ot_factor_default = _dec(j.get("ot_factor_default"))
    if ot_factor_default is not None and ot_factor_default <= 0:
        return _fail("ot_factor_default must be > 0", 422)

    eff_from = _d(j.get("effective_from"))
    if not eff_from:
        return _fail("effective_from (YYYY-MM-DD) is required", 422)
    eff_to = _d(j.get("effective_to"))
    if eff_to and eff_to < eff_from:
        return _fail("effective_to must be >= effective_from", 422)

    # overlap guard (per company)
    if _overlap_exists(int(company_id), eff_from, eff_to):
        return _fail("Overlapping effective period for this company", 409)

    p = PayPolicy(
        company_id=int(company_id),
        holiday_paid=bool(holiday_paid),
        weekly_off_paid=bool(weekly_off_paid),
        monthly_fixed_paid_leaves=monthly_fixed_paid_leaves,
        daily_paid_leave_allowed=bool(daily_paid_leave_allowed),
        ot_factor_default=ot_factor_default,
        min_wage_check=bool(min_wage_check),
        effective_from=eff_from,
        effective_to=eff_to,
        is_active=bool(j.get("is_active", True)),
    )
    db.session.add(p)
    db.session.commit()
    return _ok(_row(p), 201)

@bp.get("")
@requires_perms("payroll.policy.read")
def list_policies():
    q = PayPolicy.query
    if request.args.get("company_id"):
        try:
            q = q.filter(PayPolicy.company_id == int(request.args["company_id"]))
        except Exception:
            return _fail("company_id must be integer", 422)

    # snapshot filter
    active_on = _d(request.args.get("active_on"))
    if active_on:
        q = q.filter(
            PayPolicy.effective_from <= active_on,
            db.or_(PayPolicy.effective_to.is_(None), PayPolicy.effective_to >= active_on),
        )

    if "is_active" in request.args:
        want = request.args.get("is_active").lower() in ("1", "true", "yes")
        q = q.filter(PayPolicy.is_active == want)

    q = q.order_by(PayPolicy.company_id.asc(), PayPolicy.effective_from.desc(), PayPolicy.id.desc())
    page, size = _page_limit()
    total = q.count()
    rows = q.offset((page - 1) * size).limit(size).all()
    return _ok([_row(x) for x in rows], meta={"page": page, "size": size, "total": total})

@bp.get("/<int:policy_id>")
@requires_perms("payroll.policy.read")
def get_policy(policy_id: int):
    p = PayPolicy.query.get_or_404(policy_id)
    return _ok(_row(p))

@bp.patch("/<int:policy_id>")
@requires_perms("payroll.policy.write")
def patch_policy(policy_id: int):
    p = PayPolicy.query.get_or_404(policy_id)
    j = request.get_json(silent=True) or {}

    # Only non-period fields here; for date/rule changes use /version endpoint ideally
    if "holiday_paid" in j:            p.holiday_paid = bool(_bool(j.get("holiday_paid")))
    if "weekly_off_paid" in j:         p.weekly_off_paid = bool(_bool(j.get("weekly_off_paid")))
    if "daily_paid_leave_allowed" in j:p.daily_paid_leave_allowed = bool(_bool(j.get("daily_paid_leave_allowed")))
    if "min_wage_check" in j:          p.min_wage_check = bool(_bool(j.get("min_wage_check")))
    if "ot_factor_default" in j:
        v = _dec(j.get("ot_factor_default"))
        if v is not None and v <= 0:
            return _fail("ot_factor_default must be > 0", 422)
        p.ot_factor_default = v
    if "monthly_fixed_paid_leaves" in j:
        try:
            v = int(j.get("monthly_fixed_paid_leaves"))
            if v < 0: return _fail("monthly_fixed_paid_leaves cannot be negative", 422)
            p.monthly_fixed_paid_leaves = v
        except Exception:
            return _fail("monthly_fixed_paid_leaves must be integer", 422)
    if "is_active" in j:
        p.is_active = bool(_bool(j.get("is_active")))

    # If client insists on changing dates via PATCH, do a strict check
    if "effective_from" in j or "effective_to" in j:
        new_from = _d(j.get("effective_from")) or p.effective_from
        new_to = _d(j.get("effective_to")) if "effective_to" in j else p.effective_to
        if new_to and new_to < new_from:
            return _fail("effective_to must be >= effective_from", 422)
        if _overlap_exists(p.company_id, new_from, new_to, exclude_id=p.id):
            return _fail("Overlapping effective period for this company", 409)
        p.effective_from, p.effective_to = new_from, new_to

    db.session.commit()
    return _ok(_row(p))

@bp.put("/<int:policy_id>/version")
@requires_perms("payroll.policy.write")
def new_policy_version(policy_id: int):
    """
    Close current version (effective_to = new_from - 1 day) and create a new one with changes.
    Body:
    {
      "effective_from": "YYYY-MM-DD",
      "changes": {
        "holiday_paid": true,
        "weekly_off_paid": false,
        "ot_factor_default": 1.5,
        ...
      }
    }
    """
    old = PayPolicy.query.get_or_404(policy_id)
    j = request.get_json(silent=True) or {}
    eff_from = _d(j.get("effective_from"))
    if not eff_from:
        return _fail("effective_from is required (YYYY-MM-DD)", 422)
    if old.effective_from and eff_from <= old.effective_from:
        return _fail("effective_from must be after the current version's effective_from", 422)

    # Close old on the day before new eff_from
    old.effective_to = eff_from - timedelta(days=1)

    ch = j.get("changes") or {}
    # validate numeric
    ot = _dec(ch.get("ot_factor_default")) if "ot_factor_default" in ch else old.ot_factor_default
    if ot is not None and ot <= 0:
        return _fail("ot_factor_default must be > 0", 422)

    mfl = ch.get("monthly_fixed_paid_leaves", old.monthly_fixed_paid_leaves)
    if mfl is not None:
        try:
            mfl = int(mfl)
            if mfl < 0: return _fail("monthly_fixed_paid_leaves cannot be negative", 422)
        except Exception:
            return _fail("monthly_fixed_paid_leaves must be integer", 422)

    new = PayPolicy(
        company_id=old.company_id,
        holiday_paid=bool(_bool(ch.get("holiday_paid")) if "holiday_paid" in ch else old.holiday_paid),
        weekly_off_paid=bool(_bool(ch.get("weekly_off_paid")) if "weekly_off_paid" in ch else old.weekly_off_paid),
        monthly_fixed_paid_leaves=mfl,
        daily_paid_leave_allowed=bool(_bool(ch.get("daily_paid_leave_allowed")) if "daily_paid_leave_allowed" in ch else old.daily_paid_leave_allowed),
        ot_factor_default=ot,
        min_wage_check=bool(_bool(ch.get("min_wage_check")) if "min_wage_check" in ch else old.min_wage_check),
        effective_from=eff_from,
        effective_to=None,
        is_active=bool(_bool(ch.get("is_active")) if "is_active" in ch else True),
    )

    if _overlap_exists(new.company_id, new.effective_from, new.effective_to, exclude_id=old.id):
        return _fail("Overlapping effective period for this company", 409)

    db.session.add(old)
    db.session.add(new)
    db.session.commit()
    return _ok({"previous": _row(old), "current": _row(new)}, 201)
