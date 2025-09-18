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

# Leave overlay
from hrms_api.models.leave import LeaveRequest, LeaveType

bp = Blueprint("attendance_monthly", __name__, url_prefix="/api/v1/attendance")

# ---------- tiny helpers (match your blueprint style) -------------------------
def _ok(data=None, status=200, **meta):
    payload = {"success": True, "data": data}
    if meta:
        payload["meta"] = meta
    return jsonify(payload), status

def _fail(msg, status=400):
    return jsonify({"success": False, "error": {"message": msg}}), status

def _ym():
    y = request.args.get("year", type=int)
    m = request.args.get("month", type=int)
    if not y or not m or not (1 <= m <= 12):
        return None, None
    return y, m

def _month_bounds(year: int, month: int):
    last = pycal.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last)

def _week_in_month(d: date) -> int:
    # returns 1..5
    return (d.day - 1) // 7 + 1

def _tstr(t: time | None) -> str | None:
    return t.strftime("%H:%M") if t else None

def _approved_leave_map(employee_id: int, first_day: date, last_day: date):
    """
    Map of date -> {type_id, type_code, type_name, part_day}
    for APPROVED leave requests overlapping [first_day..last_day].
    """
    rows = (
        db.session.query(LeaveRequest, LeaveType)
        .join(LeaveType, LeaveType.id == LeaveRequest.leave_type_id)
        .filter(
            LeaveRequest.employee_id == employee_id,
            LeaveRequest.status == "approved",
            LeaveRequest.start_date <= last_day,
            LeaveRequest.end_date >= first_day,
        )
        .all()
    )
    out = {}
    for r, t in rows:
        cur = r.start_date
        while cur <= r.end_date:
            if first_day <= cur <= last_day:
                out[cur] = {
                    "type_id": r.leave_type_id,
                    "type_code": t.code,
                    "type_name": t.name,
                    "part_day": r.part_day or None,
                }
            cur = cur + timedelta(days=1)
    return out

# ---------- model helpers (JSON-safe) -----------------------------------------
def _rules_for(emp: Employee):
    """
    Weekly-off rules grouped by weekday -> list[(is_alternate, weeks:set)]
    """
    rules = WeeklyOffRule.query.filter(
        WeeklyOffRule.company_id == emp.company_id,
        or_(WeeklyOffRule.location_id == emp.location_id, WeeklyOffRule.location_id.is_(None))
    ).all()
    bywd = {i: [] for i in range(7)}
    for r in rules:
        weeks = set()
        if getattr(r, "is_alternate", False) and getattr(r, "week_numbers", None):
            try:
                weeks = {int(x.strip()) for x in r.week_numbers.split(",") if x.strip()}
            except Exception:
                weeks = set()
        bywd[r.weekday].append((bool(getattr(r, "is_alternate", False)), weeks))
    return bywd

def _holiday_on(emp: Employee, d: date):
    """
    Prefer location-specific holiday; else fallback to company-wide (location NULL).
    """
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
    """
    Most recent shift assignment that covers date d.
    Returns JSON-safe dict (times as HH:MM strings), or None.
    """
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
    return {
        "id": s.id,
        "code": s.code,
        "name": s.name,
        "is_night": bool(getattr(s, "is_night", False)),
        "start_time": _tstr(getattr(s, "start_time", None)),
        "end_time": _tstr(getattr(s, "end_time", None)),
        "break_minutes": int(getattr(s, "break_minutes", 0) or 0),
        "grace_minutes": int(getattr(s, "grace_minutes", 0) or 0),
    }

def _compute_day(emp: Employee, day: date, bywd):
    """
    Mirrors your daily-status logic but kept local so monthly stays JSON-safe.
    """
    # Holiday?
    hol = _holiday_on(emp, day)
    is_holiday = bool(hol)
    holiday_name = hol.name if hol else None

    # Weekly off?
    wd = day.weekday()
    is_wo = False
    for is_alt, weeks in bywd.get(wd, []):
        if not is_alt:
            is_wo = True
            break
        if _week_in_month(day) in weeks:
            is_wo = True
            break

    # Shift info (JSON-ready)
    sh = _shift_on(emp.id, day)
    # Span for punches
    span_start = datetime.combine(day, time.min)
    span_end = datetime.combine(day, time.max)
    if sh and sh["is_night"]:
        span_end = span_end + timedelta(days=1)

    punches = AttendancePunch.query.filter(
        AttendancePunch.employee_id == emp.id,
        AttendancePunch.ts >= span_start,
        AttendancePunch.ts <= span_end,
    ).order_by(AttendancePunch.ts.asc()).all()

    serial = [{"id": p.id, "ts": p.ts.isoformat(sep=" "), "kind": p.kind, "source": getattr(p, "source", None)} for p in punches]

    # derive first_in / last_out
    first_in = next((p.ts for p in punches if p.kind == "in"), None)
    last_out = next((p.ts for p in reversed(punches) if p.kind == "out"), None)

    # work minutes (pair-match simple)
    work_min = 0
    last_in_dt = None
    for p in punches:
        if p.kind == "in":
            last_in_dt = p.ts
        elif p.kind == "out" and last_in_dt:
            work_min += int((p.ts - last_in_dt).total_seconds() // 60)
            last_in_dt = None

    late_min = 0
    early_min = 0
    # compute late/early only when shift present
    if sh and first_in and sh["start_time"]:
        sched_in = datetime.combine(day, datetime.strptime(sh["start_time"], "%H:%M").time())
        late_min = max(0, int((first_in - sched_in).total_seconds() // 60) - (sh["grace_minutes"] or 0))
    if sh and last_out and sh["end_time"]:
        sched_out_day = day if not sh["is_night"] else (day + timedelta(days=1))
        sched_out = datetime.combine(sched_out_day, datetime.strptime(sh["end_time"], "%H:%M").time())
        early_min = max(0, int((sched_out - last_out).total_seconds() // 60))

    # status
    status = "Absent"
    remarks = []
    if first_in and last_out:
        status = "Present"
    elif first_in or last_out:
        status = "Partial"
    if is_wo:
        status = "WeeklyOff"; remarks.append("Weekly Off")
    if is_holiday:
        status = "Holiday"; remarks.append(holiday_name or "Holiday")
    if (not sh) and (not is_wo and not is_holiday):
        status = "NoShift"

    return {
        "employee_id": emp.id,
        "date": day.isoformat(),
        "weekday": wd,
        "is_weekly_off": is_wo,
        "is_holiday": is_holiday,
        "holiday_name": holiday_name,
        "shift": sh,  # JSON-safe dict or None
        "punches": serial,
        "first_in": first_in.isoformat(sep=" ") if first_in else None,
        "last_out": last_out.isoformat(sep=" ") if last_out else None,
        "work_minutes": work_min,
        "late_minutes": late_min,
        "early_minutes": early_min,
        "status": status,
        "remarks": ", ".join(remarks) if remarks else None,
    }

# ---------- GET /monthly-status ----------------------------------------------
@bp.get("/monthly-status")
@jwt_required()
def monthly_status():
    """
    GET /api/v1/attendance/monthly-status?employeeId=&year=&month=
    """
    emp_id = request.args.get("employeeId", type=int)
    if not emp_id:
        return _fail("employeeId is required", 422)
    year, month = _ym()
    if not year:
        return _fail("year and month are required", 422)

    start, end = _month_bounds(year, month)
    emp = Employee.query.get(emp_id)
    if not emp:
        return _fail("Employee not found", 404)

    bywd = _rules_for(emp)

    # Leave overlay map for the window
    leave_by_day = _approved_leave_map(emp_id, start, end)

    # iterate dates
    cur = start
    one = timedelta(days=1)
    days = []
    totals = {
        "present": 0, "partial": 0, "absent": 0,
        "weekly_off": 0, "holiday": 0, "no_shift": 0,
        "work_minutes": 0, "late_minutes": 0, "early_minutes": 0,
        # new counters (non-breaking)
        "leave_full": 0, "leave_half": 0
    }

    while cur <= end:
        row = _compute_day(emp, cur, bywd)

        # ---- Leave overlay (non-destructive) ----
        lv = leave_by_day.get(cur)
        if lv:
            row["leave"] = {
                "type_id": lv["type_id"],
                "type_code": lv["type_code"],
                "type_name": lv["type_name"],
                "part_day": lv["part_day"],
            }
            # If it's not Holiday/WeeklyOff, annotate/override status
            if row["status"] not in ("Holiday", "WeeklyOff"):
                has_punches = bool(row.get("punches"))
                part = (lv["part_day"] in ("am", "pm", "half"))
                if not has_punches and not part:
                    row["status"] = f"Leave({lv['type_code']})"
                    totals["leave_full"] += 1
                else:
                    # Half leave or leave with some punches
                    row["status_detail"] = f"HalfLeave({lv['type_code']}{' '+lv['part_day'] if lv['part_day'] else ''})"
                    totals["leave_half"] += 1

        days.append(row)

        # ---- Totals (legacy keys kept the same) ----
        st = row["status"]
        if st == "Present": totals["present"] += 1
        elif st == "Partial": totals["partial"] += 1
        elif st == "Absent": totals["absent"] += 1
        elif st == "WeeklyOff": totals["weekly_off"] += 1
        elif st == "Holiday": totals["holiday"] += 1
        elif st == "NoShift": totals["no_shift"] += 1

        totals["work_minutes"]  += int(row["work_minutes"] or 0)
        totals["late_minutes"]  += int(row["late_minutes"] or 0)
        totals["early_minutes"] += int(row["early_minutes"] or 0)
        cur += one

    return _ok({
        "employee_id": emp_id,
        "year": year, "month": month,
        "totals": totals,
        "days": days,
    })
