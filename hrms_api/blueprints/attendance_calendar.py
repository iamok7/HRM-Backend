from datetime import date, timedelta
import calendar as pycal
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required
from sqlalchemy import or_
from hrms_api.extensions import db
from hrms_api.models.employee import Employee
from hrms_api.models.attendance import Holiday, WeeklyOffRule, Shift
from hrms_api.models.attendance_assignment import EmployeeShiftAssignment

# Leave overlay (for calendar view)
from hrms_api.models.leave import LeaveRequest, LeaveType

bp = Blueprint("attendance_calendar", __name__, url_prefix="/api/v1/attendance")

# ---- helpers ---------------------------------------------------------------
def _ok(data=None, status=200, **meta):
    payload = {"success": True, "data": data}
    if meta: payload["meta"] = meta
    return jsonify(payload), status

def _fail(msg, status=400):
    return jsonify({"success": False, "error": {"message": msg}}), status

def _ym():
    y = request.args.get("year", type=int)
    m = request.args.get("month", type=int)
    if not y or not m or not (1 <= m <= 12): return None, None
    return y, m

def _month_bounds(year: int, month: int):
    last = pycal.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last)

def _week_in_month(d: date) -> int:
    # 1..5
    return (d.day - 1) // 7 + 1

def _daterange(d1: date, d2: date):
    cur = d1
    one = timedelta(days=1)
    while cur <= d2:
        yield cur
        cur += one

def _approved_leave_map(employee_id: int, first_day: date, last_day: date):
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
            cur += timedelta(days=1)
    return out

# ---- GET /calendar ---------------------------------------------------------
@bp.get("/calendar")
@jwt_required()
def employee_calendar():
    """
    Build a month calendar for an employee by merging:
    - Shift assignments (with shift timings)
    - Weekly off rules (company/location/global)
    - Holidays (company + location or global)
    - (new) Approved Leave overlay (non-breaking)
    """
    emp_id = request.args.get("employeeId", type=int)
    if not emp_id: return _fail("employeeId is required", 422)
    year, month = _ym()
    if not year:  return _fail("year and month are required", 422)

    start, end = _month_bounds(year, month)
    emp = Employee.query.get(emp_id)
    if not emp: return _fail("Employee not found", 404)

    # --- Holidays (company + (location or global))
    hols = Holiday.query.filter(
        Holiday.company_id == emp.company_id,
        Holiday.date >= start, Holiday.date <= end,
        or_(Holiday.location_id == emp.location_id, Holiday.location_id.is_(None))
    ).all()
    # Prefer location-specific over global for same date
    holidays_by_date = {}
    for h in hols:
        key = h.date
        if key not in holidays_by_date or holidays_by_date[key].location_id is None:
            holidays_by_date[key] = h

    # --- Weekly Off rules (company + (location or global))
    wos = WeeklyOffRule.query.filter(
        WeeklyOffRule.company_id == emp.company_id,
        or_(WeeklyOffRule.location_id == emp.location_id, WeeklyOffRule.location_id.is_(None))
    ).all()
    # bucket by weekday
    rules_by_weekday = {i: [] for i in range(7)}
    for r in wos:
        weeks = set()
        if getattr(r, "is_alternate", False) and getattr(r, "week_numbers", None):
            try:
                weeks = {int(x.strip()) for x in r.week_numbers.split(",") if x.strip()}
            except Exception:
                weeks = set()
        rules_by_weekday[r.weekday].append((bool(getattr(r, "is_alternate", False)), weeks))

    # --- Shift assignments overlapping the month (joined with shifts)
    assigns = (
        db.session.query(EmployeeShiftAssignment, Shift)
        .join(Shift, EmployeeShiftAssignment.shift_id == Shift.id)
        .filter(
            EmployeeShiftAssignment.employee_id == emp_id,
            or_(EmployeeShiftAssignment.end_date == None, EmployeeShiftAssignment.end_date >= start),  # noqa: E711
            EmployeeShiftAssignment.start_date <= end
        )
        .all()
    )
    # store as ranges
    ranges = []
    for a, s in assigns:
        r_start = max(a.start_date, start)
        r_end = min(a.end_date if a.end_date else end, end)
        ranges.append({
            "start": r_start, "end": r_end,
            "shift": {
                "id": s.id, "code": s.code, "name": s.name,
                "start_time": s.start_time.strftime("%H:%M:%S"),
                "end_time": s.end_time.strftime("%H:%M:%S"),
                "break_minutes": s.break_minutes,
                "grace_minutes": s.grace_minutes,
                "is_night": s.is_night,
            }
        })

    def shift_for_day(d: date):
        for r in ranges:
            if r["start"] <= d <= r["end"]:
                return r["shift"]
        return None

    # --- Leave overlay for the window
    leave_by_day = _approved_leave_map(emp_id, start, end)

    # Build days
    days = []
    totals = {"days": 0, "holidays": 0, "weekly_offs": 0, "working_days": 0, "no_shift": 0,
              "leave_full": 0, "leave_half": 0}

    for d in _daterange(start, end):
        wd = d.weekday()  # 0=Mon..6=Sun

        # weekly off check
        wo = False
        for is_alt, weeks in rules_by_weekday.get(wd, []):
            if not is_alt:
                wo = True
            else:
                if _week_in_month(d) in weeks:
                    wo = True
            if wo: break

        # holiday check
        h = holidays_by_date.get(d)
        is_holiday = bool(h)
        holiday_name = h.name if h else None

        # shift
        sh = shift_for_day(d)

        is_working = bool(sh) and not wo and not is_holiday

        row = {
            "date": d.isoformat(),
            "weekday": wd,                  # 0..6
            "is_weekly_off": wo,
            "is_holiday": is_holiday,
            "holiday_name": holiday_name,
            "shift": sh,                    # or null
            "is_working_day": is_working
        }

        # Leave overlay (non-breaking)
        lv = leave_by_day.get(d)
        if lv:
            row["leave"] = {
                "type_id": lv["type_id"],
                "type_code": lv["type_code"],
                "type_name": lv["type_name"],
                "part_day": lv["part_day"],
            }
            # classification for clients that need counts
            if lv["part_day"] in ("am", "pm", "half"):
                totals["leave_half"] += 1
            else:
                totals["leave_full"] += 1

        days.append(row)

        # totals
        totals["days"] += 1
        if is_holiday: totals["holidays"] += 1
        if wo: totals["weekly_offs"] += 1
        if is_working: totals["working_days"] += 1
        if not sh: totals["no_shift"] += 1

    return _ok({"employee_id": emp_id, "year": year, "month": month, "days": days, "totals": totals})
