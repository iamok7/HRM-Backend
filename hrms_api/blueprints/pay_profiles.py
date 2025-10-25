from __future__ import annotations
from datetime import date, timedelta
from decimal import Decimal
from flask import Blueprint, request, jsonify

from hrms_api.extensions import db
from hrms_api.common.auth import requires_perms
from hrms_api.models.payroll.pay_profile import EmployeePayProfile 

bp = Blueprint("pay_profiles", __name__, url_prefix="/api/v1/pay-profiles")

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

def _num_to_float(v):
    try:
        return float(v) if v is not None else None
    except Exception:
        return None

# tolerant getters/setters for schema variants: pf_enabled vs pf_applicable, etc.
_STAT_KEYS = [
    ("pf_enabled", "pf_applicable"),
    ("esi_enabled", "esi_applicable"),
    ("pt_enabled", "pt_applicable"),
    ("lwf_enabled", "lwf_applicable"),
]

def _get_stat(model, key_a, key_b):
    if hasattr(model, key_a):
        return getattr(model, key_a)
    if hasattr(model, key_b):
        return getattr(model, key_b)
    return None

def _set_stat(model, key_a, key_b, value):
    if hasattr(model, key_a):
        setattr(model, key_a, value)
    elif hasattr(model, key_b):
        setattr(model, key_b, value)

def _row(p: EmployeePayProfile):
    data = {
        "employee_id": p.employee_id,
        "pay_type": p.pay_type,  # monthly_fixed | daily_wage
        # daily wage linkage
        "category_id": getattr(p, "category_id", None),  # FK to TradeCategory if present
        "trade_code": getattr(p, "trade_code", None),    # if your model stores code instead
        "per_day_rate": _num_to_float(getattr(p, "per_day_rate", None)),
        "ot_rate": _num_to_float(getattr(p, "ot_rate", None)),
        # monthly base
        "base_monthly": _num_to_float(getattr(p, "base_monthly", None)),
        # incentives
        "incentive_pct": _num_to_float(getattr(p, "incentive_pct", None)),
        # regime (optional)
        "regime": getattr(p, "regime", None),
        # dates
        "effective_from": p.effective_from.isoformat() if p.effective_from else None,
        "effective_to": p.effective_to.isoformat() if p.effective_to else None,
        "is_active": getattr(p, "is_active", True),
        "created_at": getattr(p, "created_at", None).isoformat() if getattr(p, "created_at", None) else None,
    }
    # stat flags
    for a, b in _STAT_KEYS:
        data[a] = _get_stat(p, a, b)
    return data

def _validate_payload(j):
    pt = j.get("pay_type")
    if pt not in ("monthly_fixed", "daily_wage"):
        return "invalid pay_type (use 'monthly_fixed' or 'daily_wage')"

    if pt == "monthly_fixed":
        if j.get("category_id") or j.get("trade_code"):
            return "category_id/trade_code must be null for monthly_fixed"
        if _dec(j.get("base_monthly")) in (None,):
            return "base_monthly required for monthly_fixed"

    if pt == "daily_wage":
        if not (j.get("category_id") or j.get("trade_code")):
            return "category_id or trade_code required for daily_wage"
    return None

def _overlap_exists(employee_id, frm, to, exclude_id: tuple[int, date] | None = None) -> bool:
    """
    Enforces: for a given employee, active rows must not overlap.
    Works whether your PK is composite (employee_id + effective_from) or you have a surrogate id.
    """
    q = EmployeePayProfile.query.filter(EmployeePayProfile.employee_id == employee_id)
    this_to = to or date.max
    for other in q.all():
        # optional composite key exclusion: (employee_id, effective_from)
        if exclude_id and other.employee_id == exclude_id[0] and other.effective_from == exclude_id[1]:
            continue
        o_from = other.effective_from or date.min
        o_to = other.effective_to or date.max
        if frm <= o_to and o_from <= this_to:
            return True
    return False

# ---------- routes ----------
@bp.post("")
@requires_perms("payroll.profile.write")
def create_profile():
    j = request.get_json(silent=True) or {}
    employee_id = j.get("employee_id")
    eff_from = _d(j.get("effective_from"))
    eff_to = _d(j.get("effective_to"))

    if not employee_id or not eff_from:
        return _fail("employee_id and effective_from are required", 422)

    err = _validate_payload(j)
    if err:
        return _fail(err, 422)

    if eff_to and eff_to < eff_from:
        return _fail("effective_to must be >= effective_from", 422)

    if _overlap_exists(int(employee_id), eff_from, eff_to):
        return _fail("Overlapping effective period for this employee", 409)

    p = EmployeePayProfile(
        employee_id=int(employee_id),
        pay_type=j["pay_type"],
        # daily wage linkage (support either category_id or trade_code)
        category_id=j.get("category_id"),
        trade_code=j.get("trade_code"),
        per_day_rate=_dec(j.get("per_day_rate")),
        ot_rate=_dec(j.get("ot_rate")),
        # monthly base
        base_monthly=_dec(j.get("base_monthly")),
        incentive_pct=_dec(j.get("incentive_pct")),
        # optional regime
        regime=j.get("regime"),
        effective_from=eff_from,
        effective_to=eff_to,
        is_active=bool(j.get("is_active", True)),
    )
    # stat flags (accept either *_enabled or *_applicable in payload)
    for a, b in _STAT_KEYS:
        if a in j or b in j:
            _set_stat(p, a, b, bool(_bool(j.get(a) if a in j else j.get(b))))

    db.session.add(p)
    db.session.commit()
    return _ok(_row(p), 201)

@bp.get("")
@requires_perms("payroll.profile.read")
def list_profiles():
    q = EmployeePayProfile.query

    if request.args.get("employee_id"):
        try:
            q = q.filter(EmployeePayProfile.employee_id == int(request.args["employee_id"]))
        except Exception:
            return _fail("employee_id must be integer", 422)

    if request.args.get("pay_type"):
        q = q.filter(EmployeePayProfile.pay_type == request.args["pay_type"])

    active_on = _d(request.args.get("active_on"))
    if active_on:
        q = q.filter(
            EmployeePayProfile.effective_from <= active_on,
            db.or_(EmployeePayProfile.effective_to.is_(None), EmployeePayProfile.effective_to >= active_on),
        )

    if "is_active" in request.args:
        want = request.args.get("is_active").lower() in ("1", "true", "yes")
        q = q.filter(EmployeePayProfile.is_active == want)

    q = q.order_by(EmployeePayProfile.employee_id.asc(), EmployeePayProfile.effective_from.desc())
    page, size = _page_limit()
    total = q.count()
    rows = q.offset((page - 1) * size).limit(size).all()
    return _ok([_row(x) for x in rows], meta={"page": page, "size": size, "total": total})

@bp.get("/by-key/<int:employee_id>/<effective_from>")
@requires_perms("payroll.profile.read")
def get_profile_by_key(employee_id: int, effective_from: str):
    eff_from = _d(effective_from)
    if not eff_from:
        return _fail("effective_from must be YYYY-MM-DD", 422)
    p = (EmployeePayProfile.query
         .filter_by(employee_id=employee_id, effective_from=eff_from)
         .first())
    if not p:
        return _fail("Not found", 404)
    return _ok(_row(p))

@bp.patch("/by-key/<int:employee_id>/<effective_from>")
@requires_perms("payroll.profile.write")
def patch_profile_by_key(employee_id: int, effective_from: str):
    eff_from = _d(effective_from)
    if not eff_from:
        return _fail("effective_from must be YYYY-MM-DD", 422)
    p = (EmployeePayProfile.query
         .filter_by(employee_id=employee_id, effective_from=eff_from)
         .first())
    if not p:
        return _fail("Not found", 404)

    j = request.get_json(silent=True) or {}

    # Only non-period & flags; validation stays light here
    if "incentive_pct" in j:
        p.incentive_pct = _dec(j.get("incentive_pct"))

    if "pay_type" in j and j.get("pay_type") in ("monthly_fixed", "daily_wage"):
        p.pay_type = j.get("pay_type")

    # update daily/monthly fields if provided (no overlap checks here)
    for k in ("category_id", "trade_code"):
        if k in j:
            setattr(p, k, j.get(k))

    for k in ("per_day_rate", "ot_rate", "base_monthly"):
        if k in j:
            setattr(p, k, _dec(j.get(k)))

    if "regime" in j:
        p.regime = j.get("regime")

    if "is_active" in j:
        p.is_active = bool(_bool(j.get("is_active")))

    # stat flags
    for a, b in _STAT_KEYS:
        if a in j or b in j:
            _set_stat(p, a, b, bool(_bool(j.get(a) if a in j else j.get(b))))

    # If client insists on changing dates via PATCH: validate overlaps strictly
    if "effective_from" in j or "effective_to" in j:
        new_from = _d(j.get("effective_from")) or p.effective_from
        new_to = _d(j.get("effective_to")) if "effective_to" in j else p.effective_to
        if new_to and new_to < new_from:
            return _fail("effective_to must be >= effective_from", 422)
        if _overlap_exists(p.employee_id, new_from, new_to, exclude_id=(p.employee_id, p.effective_from)):
            return _fail("Overlapping effective period for this employee", 409)
        p.effective_from, p.effective_to = new_from, new_to

    db.session.commit()
    return _ok(_row(p))

@bp.put("/by-key/<int:employee_id>/<effective_from>/version")
@requires_perms("payroll.profile.write")
def new_profile_version(employee_id: int, effective_from: str):
    """
    Close the current version (effective_to = new_from - 1 day) and insert a new one.
    Body:
    {
      "effective_from": "YYYY-MM-DD",
      "changes": {
        "per_day_rate": 1050,
        "ot_rate": 170,
        "base_monthly": 32000,
        "category_id": 2,
        "trade_code": "FITTER",
        "pf_enabled": true,  // or pf_applicable: true
        ...
      }
    }
    """
    eff_from_key = _d(effective_from)
    if not eff_from_key:
        return _fail("URL effective_from must be YYYY-MM-DD", 422)

    old = (EmployeePayProfile.query
           .filter_by(employee_id=employee_id, effective_from=eff_from_key)
           .first())
    if not old:
        return _fail("Not found", 404)

    j = request.get_json(silent=True) or {}
    new_from = _d(j.get("effective_from"))
    if not new_from:
        return _fail("effective_from is required (YYYY-MM-DD)", 422)
    if new_from <= old.effective_from:
        return _fail("new effective_from must be after current version's effective_from", 422)

    ch = j.get("changes") or {}

    # Close old one day before new_from
    old.effective_to = new_from - timedelta(days=1)

    new = EmployeePayProfile(
        employee_id=old.employee_id,
        pay_type=ch.get("pay_type", old.pay_type),
        # daily/monthly linkages
        category_id=ch.get("category_id", getattr(old, "category_id", None)),
        trade_code=ch.get("trade_code", getattr(old, "trade_code", None)),
        per_day_rate=_dec(ch.get("per_day_rate")) if "per_day_rate" in ch else getattr(old, "per_day_rate", None),
        ot_rate=_dec(ch.get("ot_rate")) if "ot_rate" in ch else getattr(old, "ot_rate", None),
        base_monthly=_dec(ch.get("base_monthly")) if "base_monthly" in ch else getattr(old, "base_monthly", None),
        incentive_pct=_dec(ch.get("incentive_pct")) if "incentive_pct" in ch else getattr(old, "incentive_pct", None),
        regime=ch.get("regime", getattr(old, "regime", None)),
        effective_from=new_from,
        effective_to=None,
        is_active=bool(_bool(ch.get("is_active"))) if "is_active" in ch else True,
    )
    # stat flags
    for a, b in _STAT_KEYS:
        if (a in ch) or (b in ch):
            _set_stat(new, a, b, bool(_bool(ch.get(a) if a in ch else ch.get(b))))
        else:
            # inherit
            _set_stat(new, a, b, _get_stat(old, a, b))

    # overlap guard
    if _overlap_exists(new.employee_id, new.effective_from, new.effective_to, exclude_id=(old.employee_id, old.effective_from)):
        return _fail("Overlapping effective period for this employee", 409)

    db.session.add(old)
    db.session.add(new)
    db.session.commit()
    return _ok({"previous": _row(old), "current": _row(new)}, 201)
