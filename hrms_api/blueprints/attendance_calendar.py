# hrms_api/blueprints/attendance_calendar.py
from __future__ import annotations

from datetime import datetime, date, time, timedelta
import calendar as pycal
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required
from sqlalchemy import or_

from hrms_api.extensions import db
from hrms_api.models.employee import Employee
from hrms_api.models.attendance import Holiday, WeeklyOffRule, Shift
from hrms_api.models.attendance_assignment import EmployeeShiftAssignment
from hrms_api.models.attendance_punch import AttendancePunch

bp = Blueprint("attendance_calendar", __name__, url_prefix="/api/v1/attendance")

# ---------- envelopes ----------
def _ok(data=None, status=200, **meta):
    payload = {"success": True, "data": data}
    if meta: payload["meta"] = meta
    return jsonify(payload), status

def _fail(msg, status=400):
    return jsonify({"success": False, "error": {"message": msg}}), status

# ---------- tiny helpers ----------
def _as_int(val, field):
    if val in (None, "", "null"): return None
    try: return int(val)
    except Exception: raise ValueError(f"{field} must be integer")

def _ym():
    try:
        y = _as_int(request.args.get("year"), "year")
        m = _as_int(request.args.get("month"), "month")
    except ValueError:
        return None, None
    if not y or not m or not (1 <= m <= 12): return None, None
    return y, m

def _month_bounds(year: int, month: int):
    last = pycal.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last)

def _week_in_month(d: date) -> int:
    return (d.day - 1) // 7 + 1

def _tstr(t: time | None) -> str | None:
    return t.strftime("%H:%M") if t else None

# ---------- rules / holiday / shift ----------
def _rules_for(emp: Employee):
    rules = WeeklyOffRule.query.filter(
        WeeklyOffRule.company_id == emp.company_id,
        or_(WeeklyOffRule.location_id == emp.location_id, WeeklyOffRule.location_id.is_(None))
    ).all()
    bywd = {i: [] for i in range(7)}
    for r in rules:
        is_alt = bool(getattr(r, "is_alternate", False))
        weeks = set()
        wn = getattr(r, "week_numbers", None)
        if is_alt and wn:
            try:
                weeks = {int(x.strip()) for x in str(wn).split(",") if x.strip()}
            except Exception:
                weeks = set()
        bywd[int(getattr(r, "weekday", 0) or 0)].append((is_alt, weeks))
    return bywd

def _holiday_on(emp: Employee, d: date):
    hol = Holiday.query.filter(
        Holiday.company_id == emp.company_id,
        Holiday.date == d,
        Holiday.location_id == emp.location_id
    ).first()
    if not hol:
        hol = Holiday.query.filter(
            Holiday.company_id == emp.company_id,
            Holiday.date == d,
            Holiday.location_id.is_(None)
        ).first()
    return hol

def _shift_on(emp_id: int, d: date):
    asa = (
        db.session.query(EmployeeShiftAssignment, Shift)
        .join(Shift, EmployeeShiftAssignment.shift_id == Shift.id)
        .filter(
            EmployeeShiftAssignment.employee_id == emp_id,
            or_(EmployeeShiftAssignment.end_date.is_(None), EmployeeShiftAssignment.end_date >= d),
            EmployeeShiftAssignment.start_date <= d,
        )
        .order_by(EmployeeShiftAssignment.start_date.desc())
        .first()
    )
    if not asa:
        return None
    _, s = asa
    is_night = bool(getattr(s, "is_night", getattr(s, "is_night_shift", False)))
    return {
        "id": s.id,
        "code": getattr(s, "code", None),
        "name": getattr(s, "name", None),
        "is_night": is_night,
        "start_time": _tstr(getattr(s, "start_time", None)),
        "end_time": _tstr(getattr(s, "end_time", None)),
        "break_minutes": int(getattr(s, "break_minutes", 0) or 0),
        "grace_minutes": int(getattr(s, "grace_minutes", 0) or 0),
    }

# ---------- per-day compute (compact) ----------
def _compute_day(emp: Employee, day: date, rules_by_wd, include_punches: bool, include_shift: bool):
    hol = _holiday_on(emp, day)
    is_holiday = bool(hol)
    holiday_name = hol.name if hol else None

    wd = int(day.weekday())
    is_wo = False
    for is_alt, weeks in rules_by_wd.get(wd, []):
        if not is_alt:
            is_wo = True; break
        if _week_in_month(day) in weeks:
            is_wo = True; break

    sh = _shift_on(emp.id, day) if include_shift else None

    span_start = datetime.combine(day, time.min)
    span_end = datetime.combine(day, time.max)
    if sh and sh.get("is_night"):  # include next-day
        span_end = span_end + timedelta(days=1)

    punches_q = AttendancePunch.query.filter(
        AttendancePunch.employee_id == emp.id,
        AttendancePunch.ts >= span_start,
        AttendancePunch.ts <= span_end,
    ).order_by(AttendancePunch.ts.asc())

    punches = punches_q.all() if include_punches else []
    serial = [{
        "id": p.id,
        "ts": p.ts.isoformat(sep=" "),
        "kind": p.kind,
        "src": getattr(p, "source", None)
    } for p in punches] if include_punches else None

    # status (simple): Present if at least one IN and one OUT; Partial if either; else Absent.
    first_in = next((p.ts for p in punches if p.kind == "in"), None) if include_punches else None
    last_out = next((p.ts for p in reversed(punches) if p.kind == "out"), None) if include_punches else None

    status = "Absent"
    if include_punches:
        if first_in and last_out: status = "Present"
        elif first_in or last_out: status = "Partial"
    if is_wo: status = "WeeklyOff"
    if is_holiday: status = "Holiday"
    if (not sh) and (not is_wo and not is_holiday):
        status = "NoShift"

    return {
        "date": day.isoformat(),
        "weekday": wd,
        "status": status,
        "is_weekly_off": is_wo,
        "is_holiday": is_holiday,
        "holiday_name": holiday_name,
        "shift": sh if include_shift else None,
        "punches": serial,  # None unless include_punches=1
    }

# ---------- GET /calendar ----------
@bp.get("/calendar")
@jwt_required()
def calendar():
    """
    GET /api/v1/attendance/calendar?employeeId=&year=&month=&include_punches=0|1&include_shift=0|1
    """
    # employeeId
    try:
        emp_id = _as_int(request.args.get("employeeId"), "employeeId")
    except ValueError as ex:
        return _fail(str(ex), 422)
    if not emp_id:
        return _fail("employeeId is required", 422)

    year, month = _ym()
    if not year:
        return _fail("year and month are required", 422)

    include_punches = (str(request.args.get("include_punches", "0")).lower() in ("1", "true", "yes"))
    include_shift   = (str(request.args.get("include_shift", "1")).lower() in ("1", "true", "yes"))

    emp = Employee.query.get(emp_id)
    if not emp:
        return _fail("Employee not found", 404)

    rules = _rules_for(emp)
    start, end = _month_bounds(year, month)

    cur = start
    days = []
    totals = {"present": 0, "partial": 0, "absent": 0, "weekly_off": 0, "holiday": 0, "no_shift": 0}

    one = timedelta(days=1)
    while cur <= end:
        row = _compute_day(emp, cur, rules, include_punches, include_shift)
        days.append(row)

        st = row["status"]
        if st == "Present": totals["present"] += 1
        elif st == "Partial": totals["partial"] += 1
        elif st == "Absent": totals["absent"] += 1
        elif st == "WeeklyOff": totals["weekly_off"] += 1
        elif st == "Holiday": totals["holiday"] += 1
        elif st == "NoShift": totals["no_shift"] += 1

        cur += one

    return _ok({
        "employee_id": emp_id,
        "year": year,
        "month": month,
        "totals": totals,
        "days": days
    })
