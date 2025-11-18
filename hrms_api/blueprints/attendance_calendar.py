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

# Optional RBAC – safe fallbacks if not wired
try:
    from hrms_api.common.auth import requires_perms
except Exception:  # pragma: no cover
    def requires_perms(_):
        def _wrap(fn): return fn
        return _wrap


bp = Blueprint("attendance_calendar", __name__, url_prefix="/api/v1/attendance")

# ---------- envelopes ----------
def _ok(data=None, status=200, **meta):
    payload = {"success": True, "data": data}
    if meta:
        payload["meta"] = meta
    return jsonify(payload), status


def _fail(msg, status=400, code=None, detail=None):
    err = {"message": msg}
    if code:
        err["code"] = code
    if detail:
        err["detail"] = detail
    return jsonify({"success": False, "error": err}), status


# ---------- tiny helpers ----------
def _as_int(val, field):
    if val in (None, "", "null"):
        return None
    try:
        return int(val)
    except Exception:
        raise ValueError(f"{field} must be integer")


def _ym():
    """
    Read ?year=&month= from query.
    Returns (year, month) or (None, None) if invalid.
    """
    try:
        y = _as_int(request.args.get("year"), "year")
        m = _as_int(request.args.get("month"), "month")
    except ValueError:
        return None, None
    if not y or not m or not (1 <= m <= 12):
        return None, None
    return y, m


def _month_bounds(year: int, month: int):
    last = pycal.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last)


def _week_in_month(d: date) -> int:
    return (d.day - 1) // 7 + 1


def _tstr(t: time | None) -> str | None:
    return t.strftime("%H:%M") if t else None


def _bool_param(name: str, default: bool) -> bool:
    raw = str(request.args.get(name, str(int(default)))).lower()
    return raw in ("1", "true", "yes")


def _get_emp_arg() -> str | None:
    """
    Support both:
      - employeeId
      - employee_id
    """
    return request.args.get("employeeId") or request.args.get("employee_id")


# ---------- rules / holiday / shift ----------
def _rules_for(emp: Employee):
    """
    Build weekly-off rules by weekday for the given employee (company + location).
    Only active rules are considered if is_active column exists.
    """
    q = WeeklyOffRule.query.filter(
        WeeklyOffRule.company_id == emp.company_id,
        or_(
            WeeklyOffRule.location_id == emp.location_id,
            WeeklyOffRule.location_id.is_(None),
        ),
    )
    if hasattr(WeeklyOffRule, "is_active"):
        q = q.filter(WeeklyOffRule.is_active.is_(True))

    rules = q.all()
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
    """
    Prefer location-specific holiday, else company-wide.
    Only active holidays are considered if is_active exists.
    """
    q = Holiday.query.filter(
        Holiday.company_id == emp.company_id,
        Holiday.date == d,
        Holiday.location_id == emp.location_id,
    )
    if hasattr(Holiday, "is_active"):
        q = q.filter(Holiday.is_active.is_(True))
    hol = q.first()
    if hol:
        return hol

    q2 = Holiday.query.filter(
        Holiday.company_id == emp.company_id,
        Holiday.date == d,
        Holiday.location_id.is_(None),
    )
    if hasattr(Holiday, "is_active"):
        q2 = q2.filter(Holiday.is_active.is_(True))
    return q2.first()


def _shift_on(emp_id: int, d: date):
    """
    Resolve the shift active for employee on given day (taking date ranges into account).
    Only active shifts considered if is_active exists.
    """
    q = (
        db.session.query(EmployeeShiftAssignment, Shift)
        .join(Shift, EmployeeShiftAssignment.shift_id == Shift.id)
        .filter(
            EmployeeShiftAssignment.employee_id == emp_id,
            or_(
                EmployeeShiftAssignment.end_date.is_(None),
                EmployeeShiftAssignment.end_date >= d,
            ),
            EmployeeShiftAssignment.start_date <= d,
        )
    )
    if hasattr(Shift, "is_active"):
        q = q.filter(Shift.is_active.is_(True))

    asa = q.order_by(EmployeeShiftAssignment.start_date.desc()).first()
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


def _norm_dir(raw: str | None) -> str | None:
    """
    Normalize punch direction to 'in' / 'out' if possible.
    Works for both:
      - AttendancePunch.direction
      - legacy kind column (if present)
    """
    if raw is None:
        return None
    if hasattr(AttendancePunch, "normalize_direction"):
        return AttendancePunch.normalize_direction(raw)
    s = str(raw).strip().lower()
    if s in ("1", "in", "i", "enter", "entry"):
        return "in"
    if s in ("0", "out", "o", "exit", "leave"):
        return "out"
    return s


# ---------- per-day compute ----------
def _compute_day(
    emp: Employee,
    day: date,
    rules_by_wd,
    include_punches: bool,
    include_shift: bool,
):
    hol = _holiday_on(emp, day)
    is_holiday = bool(hol)
    holiday_name = hol.name if hol else None

    wd = int(day.weekday())
    is_wo = False
    for is_alt, weeks in rules_by_wd.get(wd, []):
        if not is_alt:
            is_wo = True
            break
        if _week_in_month(day) in weeks:
            is_wo = True
            break

    sh = _shift_on(emp.id, day) if include_shift else None

    span_start = datetime.combine(day, time.min)
    span_end = datetime.combine(day, time.max)
    if sh and sh.get("is_night"):  # include next-day window for night shift
        span_end = span_end + timedelta(days=1)

    punches_q = AttendancePunch.query.filter(
        AttendancePunch.employee_id == emp.id,
        AttendancePunch.ts >= span_start,
        AttendancePunch.ts <= span_end,
    ).order_by(AttendancePunch.ts.asc())

    punches = punches_q.all() if include_punches else []

    # Build serial punches using model's to_dict when available
    serial = None
    first_in = None
    last_out = None

    if include_punches:
        serial = []
        # prepare (punch, normalized_direction) pairs
        pairs = []
        for p in punches:
            # base dict from model, if present
            if hasattr(p, "to_dict"):
                row = p.to_dict()
            else:
                row = {
                    "id": p.id,
                    "company_id": getattr(p, "company_id", None),
                    "employee_id": p.employee_id,
                    "ts": p.ts.isoformat(),
                }

            direction = row.get("direction") or getattr(p, "direction", None) or getattr(p, "kind", None)
            ndir = _norm_dir(direction)

            # aliases for old UI
            row["direction"] = ndir
            row["kind"] = ndir

            # method / source aliases
            method = row.get("method") or getattr(p, "method", None)
            source = row.get("source") or method or getattr(p, "source", None)
            row["method"] = method
            row["source"] = source

            # keep note if exists
            if "note" not in row and hasattr(p, "note"):
                row["note"] = p.note

            serial.append(row)
            pairs.append((p, ndir))

        # compute status based on directions
        first_in = next((p.ts for p, d in pairs if d == "in"), None)
        last_out = next((p.ts for p, d in reversed(pairs) if d == "out"), None)

    # status (simple): Present if at least one IN and one OUT; Partial if either; else Absent.
    status = "Absent"
    if include_punches:
        if first_in and last_out:
            status = "Present"
        elif first_in or last_out:
            status = "Partial"
    if is_wo:
        status = "WeeklyOff"
    if is_holiday:
        status = "Holiday"
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
@requires_perms("attendance.calendar.read")
def calendar():
    """
    GET /api/v1/attendance/calendar
      ?employeeId=10 | employee_id=10
      &year=2025
      &month=10
      &include_punches=0|1
      &include_shift=0|1

    Example response:
    {
      "success": true,
      "data": {
        "employee_id": 10,
        "year": 2025,
        "month": 10,
        "from": "2025-10-01",
        "to": "2025-10-31",
        "totals": {
          "present": 20,
          "partial": 2,
          "absent": 3,
          "weekly_off": 4,
          "holiday": 1,
          "no_shift": 1
        },
        "days": [
          {
            "date": "2025-10-01",
            "weekday": 2,
            "status": "Present",
            "is_weekly_off": false,
            "is_holiday": false,
            "holiday_name": null,
            "shift": {
              "id": 3,
              "code": "G1",
              "name": "General",
              "is_night": false,
              "start_time": "09:00",
              "end_time": "17:30",
              "break_minutes": 30,
              "grace_minutes": 10
            },
            "punches": [
              {
                "id": 101,
                "company_id": 1,
                "employee_id": 10,
                "ts": "2025-10-01T09:02:00",
                "direction": "in",
                "kind": "in",
                "method": "machine",
                "source": "machine",
                "note": null,
                "lat": 18.590001,
                "lon": 73.730001
              }
            ]
          }
        ]
      },
      "meta": {
        "range": {
          "from": "2025-10-01",
          "to": "2025-10-31"
        }
      }
    }
    """
    # employeeId / employee_id
    try:
        emp_raw = _get_emp_arg()
        emp_id = _as_int(emp_raw, "employeeId") if emp_raw is not None else None
    except ValueError as ex:
        return _fail(str(ex), 422)

    if not emp_id:
        return _fail("employeeId is required", 422)

    year, month = _ym()
    if not year:
        return _fail("year and month are required", 422)

    include_punches = _bool_param("include_punches", False)
    include_shift = _bool_param("include_shift", True)

    emp = Employee.query.get(emp_id)
    if not emp:
        return _fail("Employee not found", 404)

    rules = _rules_for(emp)
    start, end = _month_bounds(year, month)

    cur = start
    days = []
    totals = {
        "present": 0,
        "partial": 0,
        "absent": 0,
        "weekly_off": 0,
        "holiday": 0,
        "no_shift": 0,
    }

    one = timedelta(days=1)
    while cur <= end:
        row = _compute_day(emp, cur, rules, include_punches, include_shift)
        days.append(row)

        st = row["status"]
        if st == "Present":
            totals["present"] += 1
        elif st == "Partial":
            totals["partial"] += 1
        elif st == "Absent":
            totals["absent"] += 1
        elif st == "WeeklyOff":
            totals["weekly_off"] += 1
        elif st == "Holiday":
            totals["holiday"] += 1
        elif st == "NoShift":
            totals["no_shift"] += 1

        cur += one

    data = {
        "employee_id": emp_id,
        "year": year,
        "month": month,
        "from": start.isoformat(),
        "to": end.isoformat(),
        "totals": totals,
        "days": days,
    }
    meta = {"range": {"from": start.isoformat(), "to": end.isoformat()}}

    return _ok(data, status=200, **meta)
