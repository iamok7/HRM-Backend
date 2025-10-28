from __future__ import annotations
from datetime import date, datetime
from decimal import Decimal
from typing import Dict, Any, Iterable, Tuple, Optional, List

from flask import Blueprint, request, jsonify
from sqlalchemy.sql.sqltypes import Integer, String, Text, Date, Enum as _Enum
from sqlalchemy.sql.sqltypes import Numeric as _Numeric, JSON as _JSON
from sqlalchemy import Enum as SAEnum
from hrms_api.extensions import db
from hrms_api.common.auth import requires_perms

# NOTE: adapt these imports only if your model module paths differ
from hrms_api.models.payroll.pay_run import PayRun, PayRunItem
from hrms_api.models.payroll.pay_profile import EmployeePayProfile
from hrms_api.models.payroll.policy import PayPolicy
from hrms_api.models.payroll.cycle import PayCycle
from hrms_api.models.payroll.trade import TradeCategory
from hrms_api.models.employee import Employee

bp = Blueprint("pay_runs", __name__, url_prefix="/api/v1/pay-runs")

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
    if extra is not None:
        payload["error"]["extra"] = extra
    return jsonify(payload), status

def _page_limit():
    try:
        page = max(int(request.args.get("page", 1)), 1)
        size = min(max(int(request.args.get("size", 20)), 1), 200)
    except Exception:
        page, size = 1, 20
    return page, size

def _d(s) -> Optional[date]:
    if not s:
        return None
    try:
        return date.fromisoformat(str(s))
    except Exception:
        return None

def _dec(x) -> Optional[Decimal]:
    if x is None or x == "":
        return None
    try:
        return Decimal(str(x))
    except Exception:
        return None

# ---- model introspection (map client fields -> real columns) ----
def _cols(model):
    return list(model.__table__.columns.items())

def _pick(model, keywords: List[str], prefer_type=None, fallback_type=None) -> Optional[str]:
    cols = _cols(model)
    # keyword + preferred type
    for n, c in cols:
        if any(k in n.lower() for k in keywords):
            if prefer_type is None or isinstance(c.type, prefer_type):
                return n
    # keyword only
    for n, c in cols:
        if any(k in n.lower() for k in keywords):
            return n
    # type-only fallback
    if prefer_type:
        for n, c in cols:
            if isinstance(c.type, prefer_type):
                return n
    if fallback_type:
        for n, c in cols:
            if isinstance(c.type, fallback_type):
                return n
    return None

def _is_enum(col_type):
    return isinstance(col_type, _Enum)

def _pick_note_field(model) -> Optional[str]:
    """Return a genuine text column for notes/description. Never return 'status' (enum/string)."""
    for name, col in _cols(model):
        low = name.lower()
        if any(k in low for k in ["note", "narration", "remarks", "remark", "description", "descr"]):
            if isinstance(col.type, (String, Text)) and not _is_enum(col.type):
                return name
    return None

# Detect real column names on PayRun
RUN_COMPANY_F   = _pick(PayRun, ["company_id", "company"], prefer_type=Integer) or "company_id"
RUN_CYCLE_F     = _pick(PayRun, ["pay_cycle_id", "cycle_id", "paycycle", "pay_cycle"], prefer_type=Integer)  # may be None
RUN_START_F     = _pick(PayRun, ["period_start", "start", "from"], prefer_type=Date) or "period_start"
RUN_END_F       = _pick(PayRun, ["period_end", "end", "to"], prefer_type=Date) or "period_end"
RUN_STATUS_F    = _pick(PayRun, ["status", "state"])  # for reading/patching; we won't set on create
RUN_NOTE_F      = _pick_note_field(PayRun)            # may be None (your table likely has no note)
RUN_CREATED_AT  = "created_at" if hasattr(PayRun, "created_at") else None
RUN_UPDATED_AT  = "updated_at" if hasattr(PayRun, "updated_at") else None
RUN_CALCED_AT   = "calculated_at" if hasattr(PayRun, "calculated_at") else None
RUN_APPROVED_AT = "approved_at" if hasattr(PayRun, "approved_at") else None
RUN_LOCKED_AT   = "locked_at" if hasattr(PayRun, "locked_at") else None
RUN_TOTALS_F    = "totals" if hasattr(PayRun, "totals") else None

# Optional display name for the cycle on PayRun
RUN_CYCLE_NAME_F = _pick(PayRun, ["pay_cycle_name", "cycle_name", "run_name"], prefer_type=String)

def _set_status(obj, field_name: str, value: str):
    """
    Safely set ENUM-backed status columns.
    Tries value / upper / lower against allowed enums; falls back to first allowed label.
    """
    col = getattr(type(obj), field_name).property.columns[0]
    if isinstance(col.type, SAEnum):
        allowed = list(getattr(col.type, "enums", [])) or []
        if value in allowed:
            setattr(obj, field_name, value); return
        if value.upper() in allowed:
            setattr(obj, field_name, value.upper()); return
        if value.lower() in allowed:
            setattr(obj, field_name, value.lower()); return
        if allowed:
            setattr(obj, field_name, allowed[0]); return
    setattr(obj, field_name, value)

# ------------- PayRunItem field detection -------------
def _pick_item(fields: list[str], prefer_type=None, fallback_type=None) -> Optional[str]:
    return _pick(PayRunItem, fields, prefer_type=prefer_type, fallback_type=fallback_type)

# FK + identity
ITEM_RUN_ID_F   = _pick_item(["pay_run_id", "run_id"], prefer_type=Integer) or "pay_run_id"
ITEM_EMP_ID_F   = _pick_item(["employee_id", "emp_id"], prefer_type=Integer) or "employee_id"

# time / quantities (optional)
ITEM_DAYS_F     = _pick_item(["days_worked", "days", "present_days"], prefer_type=_Numeric)
ITEM_LOP_F      = _pick_item(["lop_days", "loss_of_pay_days", "absent_days"], prefer_type=_Numeric)
ITEM_OT_HRS_F   = _pick_item(["ot_hours", "overtime_hours"], prefer_type=_Numeric)

# money (at least GROSS/NET will exist; if NET missing we’ll use GROSS)
ITEM_GROSS_F    = _pick_item(["gross", "gross_pay", "earnings_total"], prefer_type=_Numeric) or "gross"
ITEM_NET_F      = _pick_item(["net", "net_pay", "payable"], prefer_type=_Numeric)  # may be None

# json breakdown / remarks (optional)
ITEM_COMPS_F    = _pick_item(["components", "component_json", "breakup", "breakdown"], fallback_type=_JSON)
ITEM_REMARKS_F  = _pick_item(["remarks", "note", "narration", "comment"], fallback_type=Text)

# ------------- helper: build safe kwargs for PayRunItem -------------
def _build_item_kwargs(run_id: int, calc: Dict[str, Any]) -> Dict[str, Any]:
    kw: Dict[str, Any] = {}
    kw[ITEM_RUN_ID_F] = run_id
    kw[ITEM_EMP_ID_F] = calc["employee_id"]
    if ITEM_DAYS_F is not None:
        kw[ITEM_DAYS_F] = calc.get("days_worked", 0)
    if ITEM_LOP_F is not None:
        kw[ITEM_LOP_F] = calc.get("lop_days", 0)
    if ITEM_OT_HRS_F is not None:
        kw[ITEM_OT_HRS_F] = calc.get("ot_hours", 0)
    kw[ITEM_GROSS_F] = calc.get("gross", 0)
    if ITEM_NET_F:
        kw[ITEM_NET_F] = calc.get("net", kw[ITEM_GROSS_F])
    if ITEM_COMPS_F is not None:
        kw[ITEM_COMPS_F] = calc.get("components", [])
    if ITEM_REMARKS_F is not None:
        kw[ITEM_REMARKS_F] = calc.get("remarks")
    return kw

def _get(obj, field: Optional[str]):
    return getattr(obj, field) if (obj is not None and field and hasattr(obj, field)) else None

# ---------- row serializers ----------
def _row_run(r: PayRun) -> Dict[str, Any]:
    return {
        "id": r.id,
        "company_id": _get(r, RUN_COMPANY_F),
        "pay_cycle_id": _get(r, RUN_CYCLE_F),
        "pay_cycle_name": _get(r, RUN_CYCLE_NAME_F),
        "period_start": _get(r, RUN_START_F).isoformat() if _get(r, RUN_START_F) else None,
        "period_end": _get(r, RUN_END_F).isoformat() if _get(r, RUN_END_F) else None,
        "status": _get(r, RUN_STATUS_F),
        "note": _get(r, RUN_NOTE_F),
        "created_at": _get(r, RUN_CREATED_AT).isoformat() if _get(r, RUN_CREATED_AT) else None,
        "calculated_at": _get(r, RUN_CALCED_AT).isoformat() if _get(r, RUN_CALCED_AT) else None,
        "approved_at": _get(r, RUN_APPROVED_AT).isoformat() if _get(r, RUN_APPROVED_AT) else None,
        "locked_at": _get(r, RUN_LOCKED_AT).isoformat() if _get(r, RUN_LOCKED_AT) else None,
        "totals": _get(r, RUN_TOTALS_F),
    }

def _row_item(x: PayRunItem) -> Dict[str, Any]:
    return {
        "id": x.id,
        "pay_run_id": getattr(x, ITEM_RUN_ID_F, None),
        "employee_id": getattr(x, ITEM_EMP_ID_F, None),
        "days_worked": float(getattr(x, ITEM_DAYS_F, 0) or 0) if ITEM_DAYS_F else 0.0,
        "lop_days": float(getattr(x, ITEM_LOP_F, 0) or 0) if ITEM_LOP_F else 0.0,
        "ot_hours": float(getattr(x, ITEM_OT_HRS_F, 0) or 0) if ITEM_OT_HRS_F else 0.0,
        "gross": float(getattr(x, ITEM_GROSS_F, 0) or 0),
        "net": float(getattr(x, ITEM_NET_F, getattr(x, ITEM_GROSS_F, 0)) or 0) if ITEM_NET_F else float(getattr(x, ITEM_GROSS_F, 0) or 0),
        "components": getattr(x, ITEM_COMPS_F, None) if ITEM_COMPS_F else None,
        "remarks": getattr(x, ITEM_REMARKS_F, None) if ITEM_REMARKS_F else None,
    }

# ---------- ensure pay cycle helper ----------
def _ensure_cycle_for_company(company_id: int) -> Optional[PayCycle]:
    """Return an existing PayCycle for the company or create a sensible default.
    Default: anchor day 1, timezone Asia/Kolkata, payday 5th, active True.
    """
    try:
        company_id = int(company_id)
    except Exception:
        return None
    q = PayCycle.query.filter(PayCycle.company_id == company_id)
    cur = q.filter(PayCycle.active.is_(True)).order_by(PayCycle.id.desc()).first() or \
          q.order_by(PayCycle.id.desc()).first()
    if cur:
        return cur
    try:
        c = PayCycle(
            company_id=company_id,
            period_anchor_day=1,
            payday_rule={"type": "FIXED_DAY", "day": 5},
            timezone="Asia/Kolkata",
            active=True,
        )
        db.session.add(c)
        db.session.commit()
        return c
    except Exception:
        db.session.rollback()
        return None

def _ensure_policy_for_company(company_id: int, on_date: date) -> Optional[PayPolicy]:
    """Return active PayPolicy for date or create a default one.
    Defaults align with model defaults to keep behavior predictable.
    """
    try:
        company_id = int(company_id)
    except Exception:
        return None
    # Reuse if one already covers on_date
    cur = _pick_policy(company_id, on_date)
    if cur:
        return cur
    # Create default policy effective from start of the month
    try:
        eff_from = date(on_date.year, on_date.month, 1)
    except Exception:
        eff_from = on_date
    try:
        p = PayPolicy(
            company_id=company_id,
            holiday_paid=True,
            weekly_off_paid=True,
            monthly_fixed_paid_leaves=2,
            daily_paid_leave_allowed=False,
            ot_factor_default=Decimal("2.00"),
            min_wage_check=True,
            effective_from=eff_from,
            effective_to=None,
        )
        db.session.add(p)
        db.session.commit()
        return p
    except Exception:
        db.session.rollback()
        return None
def _ensure_status(r: PayRun, allowed: Iterable[str]):
    st = (_get(r, RUN_STATUS_F) or "draft")
    if st not in allowed:
        raise ValueError(f"Run in status '{st}' cannot perform this action (allowed: {', '.join(allowed)})")

# ---------- routes ----------
@bp.post("")
@requires_perms("payroll.run.write")
def create_run():
    """
    Create a pay run in DRAFT.
    We DO NOT set 'status' here; let the DB default (ENUM) handle it.
    """
    j = request.get_json(silent=True) or {}
    company_id = j.get("company_id")
    cycle_id   = j.get("pay_cycle_id")
    cycle_name = (j.get("pay_cycle_name") or "").strip() or None
    pstart     = _d(j.get("period_start"))
    pend       = _d(j.get("period_end"))
    note_in    = (j.get("note") or "").strip() or None

    if not (company_id and pstart and pend):
        return _fail("company_id, period_start, period_end are required", 422)
    if pend < pstart:
        return _fail("period_end must be >= period_start", 422)
    # Resolve cycle automatically if ID not given
    if RUN_CYCLE_F and not cycle_id:
        q = PayCycle.query.filter(PayCycle.company_id == int(company_id))
        auto = (q.filter(PayCycle.active.is_(True)).order_by(PayCycle.id.desc()).first()
                or q.order_by(PayCycle.id.desc()).first())
        if auto is None:
            auto = _ensure_cycle_for_company(company_id)
        if auto is None:
            return _fail("No pay cycle found for company to auto-select", 422)
        cycle_id = auto.id
    if RUN_CYCLE_F and cycle_id:
        cyc = PayCycle.query.get(cycle_id)
        if cyc is None:
            return _fail("pay_cycle_id not found", 404)
        # Safety: ensure the chosen cycle belongs to the same company
        try:
            if int(company_id) != int(getattr(cyc, "company_id", 0)):
                return _fail("pay_cycle_id belongs to a different company", 422)
        except Exception:
            return _fail("invalid company_id or pay_cycle_id", 422)

    kwargs: Dict[str, Any] = {}
    kwargs[RUN_COMPANY_F] = int(company_id)
    if RUN_CYCLE_F:
        kwargs[RUN_CYCLE_F] = int(cycle_id)
    if RUN_CYCLE_NAME_F and cycle_name:
        kwargs[RUN_CYCLE_NAME_F] = cycle_name
    kwargs[RUN_START_F] = pstart
    kwargs[RUN_END_F]   = pend
    if RUN_NOTE_F and note_in:
        kwargs[RUN_NOTE_F] = note_in
    if RUN_CREATED_AT:
        kwargs.setdefault(RUN_CREATED_AT, datetime.utcnow())

    r = PayRun(**kwargs)
    db.session.add(r)
    db.session.commit()

    meta = {}
    if note_in and not RUN_NOTE_F:
        meta["warning"] = "note/description column not found on PayRun; 'note' was ignored"
    return _ok(_row_run(r), 201, **meta)

@bp.get("")
@requires_perms("payroll.run.read")
def list_runs():
    q = PayRun.query
    if request.args.get("company_id"):
        try:
            q = q.filter(getattr(PayRun, RUN_COMPANY_F) == int(request.args["company_id"]))
        except Exception:
            return _fail("company_id must be integer", 422)
    if request.args.get("pay_cycle_id") and RUN_CYCLE_F:
        try:
            q = q.filter(getattr(PayRun, RUN_CYCLE_F) == int(request.args["pay_cycle_id"]))
        except Exception:
            return _fail("pay_cycle_id must be integer", 422)
    if request.args.get("status") and RUN_STATUS_F:
        q = q.filter(getattr(PayRun, RUN_STATUS_F) == request.args["status"])
    if request.args.get("from"):
        d = _d(request.args["from"])
        if not d: return _fail("from must be YYYY-MM-DD", 422)
        q = q.filter(getattr(PayRun, RUN_START_F) >= d)
    if request.args.get("to"):
        d = _d(request.args["to"])
        if not d: return _fail("to must be YYYY-MM-DD", 422)
        q = q.filter(getattr(PayRun, RUN_END_F) <= d)

    if RUN_CREATED_AT:
        q = q.order_by(getattr(PayRun, RUN_CREATED_AT).desc())
    else:
        q = q.order_by(PayRun.id.desc())

    page, size = _page_limit()
    total = q.count()
    rows = q.offset((page-1)*size).limit(size).all()
    return _ok([_row_run(x) for x in rows], meta={"page": page, "size": size, "total": total})

@bp.get("/<int:run_id>")
@requires_perms("payroll.run.read")
def get_run(run_id: int):
    r = PayRun.query.get_or_404(run_id)
    return _ok(_row_run(r))

@bp.delete("/<int:run_id>")
@requires_perms("payroll.run.write")
def delete_run(run_id: int):
    r = PayRun.query.get_or_404(run_id)
    try:
        _ensure_status(r, ("draft",))
    except ValueError as e:
        return _fail(str(e), 409)
    PayRunItem.query.filter_by(**{ITEM_RUN_ID_F: r.id}).delete()
    db.session.delete(r)
    db.session.commit()
    return _ok({"deleted": run_id})

@bp.patch("/<int:run_id>")
@requires_perms("payroll.run.write")
def patch_run(run_id: int):
    r = PayRun.query.get_or_404(run_id)
    j = request.get_json(silent=True) or {}
    try:
        _ensure_status(r, ("draft",))  # only draft is editable
    except ValueError as e:
        return _fail(str(e), 409)

    if "period_start" in j or "period_end" in j:
        pstart = _d(j.get("period_start")) if "period_start" in j else _get(r, RUN_START_F)
        pend   = _d(j.get("period_end"))   if "period_end"   in j else _get(r, RUN_END_F)
        if not (pstart and pend): return _fail("invalid dates", 422)
        if pend < pstart: return _fail("period_end must be >= period_start", 422)
        setattr(r, RUN_START_F, pstart)
        setattr(r, RUN_END_F, pend)

    if "note" in j and RUN_NOTE_F:
        setattr(r, RUN_NOTE_F, (j.get("note") or "").strip() or None)
    if RUN_CYCLE_NAME_F and "pay_cycle_name" in j:
        setattr(r, RUN_CYCLE_NAME_F, (j.get("pay_cycle_name") or "").strip() or None)

    if RUN_UPDATED_AT:
        setattr(r, RUN_UPDATED_AT, datetime.utcnow())

    db.session.commit()
    return _ok(_row_run(r))

# ---------- calculation (MVP) ----------
def _attendance_rollup(company_id: int, period_start: date, period_end: date) -> Dict[int, Dict[str, Any]]:
    # Stub – plug real rollup here if needed
    return {}

def _attendance_rollup_fetch(company_id: int, period_start: date, period_end: date) -> Dict[int, Dict[str, Any]]:
    """Fetch attendance rollup for the run window using the attendance_rollup engine.
    Returns { employee_id: { days_worked, lop_days, ot_hours, holidays, weekly_off } }.
    """
    try:
        from hrms_api.blueprints.attendance_rollup import compute_rollup
    except Exception:
        return {}
    try:
        return compute_rollup(company_id, period_start, period_end, None)
    except Exception:
        return {}

def _pick_policy(company_id: int, on_date: date) -> Optional[PayPolicy]:
    q = (PayPolicy.query
         .filter(PayPolicy.company_id == company_id)
         .filter(PayPolicy.effective_from <= on_date)
         .filter(db.or_(PayPolicy.effective_to.is_(None), PayPolicy.effective_to >= on_date))
         .order_by(PayPolicy.effective_from.desc()))
    return q.first()

def _active_profile(employee_id: int, on_date: date) -> Optional[EmployeePayProfile]:
    q = (EmployeePayProfile.query
         .filter(EmployeePayProfile.employee_id == employee_id)
         .filter(EmployeePayProfile.effective_from <= on_date)
         .filter(db.or_(EmployeePayProfile.effective_to.is_(None), EmployeePayProfile.effective_to >= on_date))
         .order_by(EmployeePayProfile.effective_from.desc()))
    return q.first()

def _trade_rate(profile: EmployeePayProfile, on_date: date) -> Tuple[Optional[Decimal], Optional[Decimal]]:
    # Support multiple schema variants: per_day_rate/ot_rate or per_day_override/ot_rate_override
    if (getattr(profile, "per_day_rate", None) is not None) or (getattr(profile, "ot_rate", None) is not None):
        return (
            _dec(getattr(profile, "per_day_rate", None)),
            _dec(getattr(profile, "ot_rate", None)),
        )
    if (getattr(profile, "per_day_override", None) is not None) or (getattr(profile, "ot_rate_override", None) is not None):
        return (
            _dec(getattr(profile, "per_day_override", None)),
            _dec(getattr(profile, "ot_rate_override", None)),
        )
    code  = getattr(profile, "trade_code", None)
    cat_id= getattr(profile, "category_id", None)
    q = TradeCategory.query
    if code: q = q.filter(TradeCategory.code == code)
    elif cat_id: q = q.filter(TradeCategory.id == cat_id)
    else: return (None, None)
    q = q.filter(TradeCategory.effective_from <= on_date)\
         .filter(db.or_(TradeCategory.effective_to.is_(None), TradeCategory.effective_to >= on_date))\
         .order_by(TradeCategory.effective_from.desc())
    t = q.first()
    if not t: return (None, None)
    return (_dec(t.per_day_rate), _dec(t.ot_rate))

def _calc_employee(emp: Employee, policy: PayPolicy, prof: EmployeePayProfile, snap: Dict[str, Any]) -> Dict[str, Any]:
    pay_type = getattr(prof, "pay_type", "monthly_fixed")
    days_worked = Decimal(str(snap.get("days_worked", 0) or 0))
    lop_days = Decimal(str(snap.get("lop_days", 0) or 0))
    ot_hours = Decimal(str(snap.get("ot_hours", 0) or 0))

    gross = Decimal("0")
    comps = []

    if pay_type == "daily_wage":
        per_day, ot_rate = _trade_rate(prof, snap["on_date"])
        per_day = per_day or Decimal("0")
        ot_rate = ot_rate or Decimal("0")
        basic = per_day * days_worked
        ot_amt = ot_rate * ot_hours
        gross = basic + ot_amt
        comps.append({"code": "BASIC", "amount": float(basic)})
        if ot_amt > 0:
            comps.append({"code": "OT", "amount": float(ot_amt)})
    else:  # monthly_fixed default
        # Accept either base_monthly or monthly_gross
        base = _dec(getattr(prof, "base_monthly", None))
        if base is None:
            base = _dec(getattr(prof, "monthly_gross", None))
        base = base or Decimal("0")
        basic = base - (base / Decimal("30")) * lop_days
        if basic < 0: basic = Decimal("0")
        gross = basic
        comps.append({"code": "BASIC", "amount": float(basic)})

    net = gross
    return {
        "employee_id": emp.id,
        "days_worked": float(days_worked),
        "lop_days": float(lop_days),
        "ot_hours": float(ot_hours),
        "gross": float(gross),
        "net": float(net),
        "components": comps,
        "remarks": None,
    }

@bp.post("/<int:run_id>/calculate")
@requires_perms("payroll.run.write")
def calculate_run(run_id: int):
    r = PayRun.query.get_or_404(run_id)
    try:
        _ensure_status(r, ("draft", "calculated"))  # allow re-calc
    except ValueError as e:
        return _fail(str(e), 409)

    snap_date = _get(r, RUN_END_F) or _get(r, RUN_START_F)

    policy = _pick_policy(_get(r, RUN_COMPANY_F), snap_date)
    if not policy:
        policy = _ensure_policy_for_company(_get(r, RUN_COMPANY_F), snap_date)
    if not policy:
        return _fail("No pay policy effective for company on the period end date", 422)

    # attendance snapshot (stubbed)
    roll = _attendance_rollup_fetch(_get(r, RUN_COMPANY_F), _get(r, RUN_START_F), _get(r, RUN_END_F))

    # pick latest profile per employee effective on snap_date
    profs = (EmployeePayProfile.query
             .filter(EmployeePayProfile.effective_from <= snap_date)
             .filter(db.or_(EmployeePayProfile.effective_to.is_(None),
                            EmployeePayProfile.effective_to >= snap_date))
             .all())
    emp_ids = list({p.employee_id for p in profs})
    if not emp_ids:
        # Bootstrap minimal monthly profiles for active employees of the company (Render/empty DB convenience)
        try:
            comp_id = _get(r, RUN_COMPANY_F)
            # active during window: doj <= end and (dol is null or dol >= start), status active if column exists
            q_emp = Employee.query.filter(Employee.company_id == comp_id)
            if hasattr(Employee, "status"):
                q_emp = q_emp.filter(Employee.status == "active")
            if hasattr(Employee, "doj"):
                q_emp = q_emp.filter(db.or_(Employee.doj.is_(None), Employee.doj <= (_get(r, RUN_END_F) or snap_date)))
            if hasattr(Employee, "dol"):
                q_emp = q_emp.filter(db.or_(Employee.dol.is_(None), Employee.dol >= (_get(r, RUN_START_F) or snap_date)))
            emps_boot = q_emp.all()
            if emps_boot:
                eff_from = date(snap_date.year, snap_date.month, 1)
                for e in emps_boot:
                    db.session.add(EmployeePayProfile(
                        employee_id=e.id,
                        pay_type="monthly_fixed",
                        effective_from=eff_from,
                        effective_to=None,
                    ))
                db.session.commit()
                profs = (EmployeePayProfile.query
                         .filter(EmployeePayProfile.effective_from <= snap_date)
                         .filter(db.or_(EmployeePayProfile.effective_to.is_(None), EmployeePayProfile.effective_to >= snap_date))
                         .all())
                emp_ids = list({p.employee_id for p in profs})
        except Exception:
            db.session.rollback()
            emp_ids = []
    if not emp_ids:
        return _fail("No active employee pay profiles found for this period", 422)

    emps = {e.id: e for e in Employee.query.filter(Employee.id.in_(emp_ids)).all()}
    latest_prof = {}
    for p in profs:
        cur = latest_prof.get(p.employee_id)
        if (cur is None) or (p.effective_from > cur.effective_from):
            latest_prof[p.employee_id] = p

    # wipe & rebuild items
    PayRunItem.query.filter_by(**{ITEM_RUN_ID_F: r.id}).delete()

    total_gross = Decimal("0"); total_net = Decimal("0"); count = 0
    for emp_id, prof in latest_prof.items():
        emp = emps.get(emp_id)
        if not emp:
            continue
        snap = {"on_date": snap_date, **(roll.get(emp_id) or {"days_worked": 0, "lop_days": 0, "ot_hours": 0})}
        calc = _calc_employee(emp, policy, prof, snap)
        db.session.add(PayRunItem(**_build_item_kwargs(r.id, calc)))
        total_gross += Decimal(str(calc["gross"]))
        total_net += Decimal(str(calc["net"]))
        count += 1

    # ENUM-safe status + timestamps + totals
    if RUN_STATUS_F:
        _set_status(r, RUN_STATUS_F, "calculated")
    if RUN_CALCED_AT:
        setattr(r, RUN_CALCED_AT, datetime.utcnow())
    if RUN_TOTALS_F:
        setattr(r, RUN_TOTALS_F, {"count": count, "gross": float(total_gross), "net": float(total_net)})

    db.session.commit()
    return _ok({"run": _row_run(r), "items": count})

@bp.get("/<int:run_id>/items")
@requires_perms("payroll.run.read")
def list_run_items(run_id: int):
    """List PayRunItem rows for the given run.
    Supports optional "employee_id" filter and pagination via page/size.
    """
    r = PayRun.query.get_or_404(run_id)
    q = PayRunItem.query.filter_by(**{ITEM_RUN_ID_F: r.id})

    emp_q = (request.args.get("employee_id") or "").strip()
    if emp_q:
        try:
            q = q.filter(getattr(PayRunItem, ITEM_EMP_ID_F) == int(emp_q))
        except Exception:
            return _fail("employee_id must be integer", 422)

    page, size = _page_limit()
    total = q.count()
    rows = q.order_by(PayRunItem.id.asc()).offset((page-1)*size).limit(size).all()
    return _ok([_row_item(x) for x in rows], meta={"page": page, "size": size, "total": total})

@bp.post("/<int:run_id>/approve")
@requires_perms("payroll.run.write")
def approve_run(run_id: int):
    r = PayRun.query.get_or_404(run_id)
    try:
        _ensure_status(r, ("calculated",))
    except ValueError as e:
        return _fail(str(e), 409)

    if RUN_STATUS_F:
        _set_status(r, RUN_STATUS_F, "approved")
    if RUN_APPROVED_AT:
        setattr(r, RUN_APPROVED_AT, datetime.utcnow())

    db.session.commit()
    return _ok(_row_run(r))

@bp.post("/<int:run_id>/lock")
@requires_perms("payroll.run.write")
def lock_run(run_id: int):
    r = PayRun.query.get_or_404(run_id)
    try:
        _ensure_status(r, ("approved",))
    except ValueError as e:
        return _fail(str(e), 409)

    if RUN_STATUS_F:
        _set_status(r, RUN_STATUS_F, "locked")
    if RUN_LOCKED_AT:
        setattr(r, RUN_LOCKED_AT, datetime.utcnow())

    db.session.commit()
    return _ok(_row_run(r))

@bp.post("/<int:run_id>/unlock")
@requires_perms("payroll.run.write")
def unlock_run(run_id: int):
    """Reopen a locked run back to 'approved' to allow further actions.
    Only allowed from status 'locked'. Clears locked_at if present.
    """
    r = PayRun.query.get_or_404(run_id)
    try:
        _ensure_status(r, ("locked",))
    except ValueError as e:
        return _fail(str(e), 409)

    if RUN_STATUS_F:
        _set_status(r, RUN_STATUS_F, "approved")
    if RUN_LOCKED_AT:
        setattr(r, RUN_LOCKED_AT, None)

    db.session.commit()
    return _ok(_row_run(r))
