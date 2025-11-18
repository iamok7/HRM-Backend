# hrms_api/blueprints/attendance_monthly.py
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
from hrms_api.models.leave import LeaveRequest, LeaveType  # leave overlay

# Optional RBAC – safe fallbacks
try:
    from hrms_api.common.auth import requires_perms
except Exception:  # pragma: no cover
    def requires_perms(_):
        def _wrap(fn): return fn
        return _wrap


bp = Blueprint("attendance_monthly", __name__, url_prefix="/api/v1/attendance")


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
    # returns 1..5
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


def _norm_dir(raw: str | None) -> str | None:
    """
    Normalize punch direction to 'in' / 'out' if possible.
    Works for:
      - AttendancePunch.direction
      - legacy kind column (if present)
    """
    if raw is None:
        return None
    # prefer model helper if present
    if hasattr(AttendancePunch, "normalize_direction"):
        return AttendancePunch.normalize_direction(raw)
    s = str(raw).strip().lower()
    if s in ("1", "in", "i", "enter", "entry"):
        return "in"
    if s in ("0", "out", "o", "exit", "leave"):
        return "out"
    return s


# ---------- leave overlay ----------
def _approved_leave_map(employee_id: int, first_day: date, last_day: date):
    """
    Map: date -> {type_id, type_code, type_name, part_day}
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
        part = getattr(r, "part_day", None) or None
        cur = r.start_date
        while cur <= r.end_date:
            if first_day <= cur <= last_day:
                out[cur] = {
                    "type_id": r.leave_type_id,
                    "type_code": getattr(t, "code", None),
                    "type_name": getattr(t, "name", None),
                    "part_day": part,  # e.g., "am" | "pm" | "half" | None
                }
            cur = cur + timedelta(days=1)
    return out


# ---------- model helpers ----------
def _rules_for(emp: Employee):
    """
    Weekly-off rules grouped by weekday -> list[(is_alternate, weeks:set)]
    Only active rules considered if is_active exists.
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
    Prefer location-specific holiday; else fallback to company-wide (location NULL).
    Only active holidays considered if is_active exists.
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
    Most recent shift assignment that covers date d.
    Returns JSON-safe dict (times as HH:MM strings), or None.
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
    # tolerate different field names: is_night vs is_night_shift
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


# ---------- per-day compute ----------
def _compute_day(emp: Employee, day: date, bywd):
    """
    Compute daily summary for a given employee + date.
    Uses AttendancePunch.direction/method/geo fields (via to_dict()).
    """
    # Holiday?
    hol = _holiday_on(emp, day)
    is_holiday = bool(hol)
    holiday_name = hol.name if hol else None

    # Weekly off?
    wd = int(day.weekday())
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
    if sh and sh.get("is_night"):
        # If night shift, include till next day end to be safe
        span_end = span_end + timedelta(days=1)

    punches_q = AttendancePunch.query.filter(
        AttendancePunch.employee_id == emp.id,
        AttendancePunch.ts >= span_start,
        AttendancePunch.ts <= span_end,
    ).order_by(AttendancePunch.ts.asc())

    punches = punches_q.all()

    serial = []
    pairs = []  # (ts, normalized_direction)

    for p in punches:
        # Prefer model's to_dict so we get method/geo/etc.
        if hasattr(p, "to_dict"):
            row = p.to_dict()
        else:
            row = {
                "id": p.id,
                "company_id": getattr(p, "company_id", None),
                "employee_id": p.employee_id,
                "ts": p.ts.isoformat() if getattr(p, "ts", None) else None,
            }

        # direction / kind normalization
        direction = row.get("direction") or getattr(p, "direction", None) or getattr(p, "kind", None)
        ndir = _norm_dir(direction)
        row["direction"] = ndir
        row["kind"] = ndir  # alias for older UI

        # method / source alias
        method = row.get("method") or getattr(p, "method", None)
        source = row.get("source") or method or getattr(p, "source", None)
        row["method"] = method
        row["source"] = source

        # note
        if "note" not in row and hasattr(p, "note"):
            row["note"] = p.note

        serial.append(row)
        if p.ts and ndir:
            pairs.append((p.ts, ndir))

    # derive first_in / last_out using normalized direction
    first_in = next((ts for ts, d in pairs if d == "in"), None)
    last_out = next((ts for ts, d in reversed(pairs) if d == "out"), None)

    # work minutes (simple pair-match IN→OUT)
    work_min = 0
    last_in_dt = None
    for ts, d in pairs:
        if d == "in":
            last_in_dt = ts
        elif d == "out" and last_in_dt:
            try:
                work_min += int((ts - last_in_dt).total_seconds() // 60)
            finally:
                last_in_dt = None

    late_min = 0
    early_min = 0
    # compute late/early only when shift present
    if sh and first_in and sh.get("start_time"):
        try:
            sched_in = datetime.combine(day, datetime.strptime(sh["start_time"], "%H:%M").time())
            grace = int(sh.get("grace_minutes") or 0)
            late_min = max(0, int((first_in - sched_in).total_seconds() // 60) - grace)
        except Exception:
            late_min = 0

    if sh and last_out and sh.get("end_time"):
        try:
            sched_out_day = day if not sh.get("is_night") else (day + timedelta(days=1))
            sched_out = datetime.combine(sched_out_day, datetime.strptime(sh["end_time"], "%H:%M").time())
            early_min = max(0, int((sched_out - last_out).total_seconds() // 60))
        except Exception:
            early_min = 0

    # status
    status = "Absent"
    remarks = []
    if first_in and last_out:
        status = "Present"
    elif first_in or last_out:
        status = "Partial"
    if is_wo:
        status = "WeeklyOff"
        remarks.append("Weekly Off")
    if is_holiday:
        status = "Holiday"
        remarks.append(holiday_name or "Holiday")
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


# ---------- GET /monthly-status ----------
@bp.get("/monthly-status")
@jwt_required()
@requires_perms("attendance.monthly.read")
def monthly_status():
    """
    GET /api/v1/attendance/monthly-status
      ?employeeId=10 | employee_id=10
      &year=2025
      &month=10

    Response:
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
          "partial": 1,
          "absent": 3,
          "weekly_off": 4,
          "holiday": 1,
          "no_shift": 3,
          "work_minutes": 9800,
          "late_minutes": 120,
          "early_minutes": 45,
          "leave_full": 2,
          "leave_half": 1
        },
        "days": [
          {
            "employee_id": 10,
            "date": "2025-10-01",
            "weekday": 2,
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
                "lon": 73.730001,
                "accuracy_m": 12.5,
                "photo_url": null,
                "face_score": null
              }
            ],
            "first_in": "2025-10-01 09:02:00",
            "last_out": "2025-10-01 17:25:00",
            "work_minutes": 493,
            "late_minutes": 0,
            "early_minutes": 5,
            "status": "Present",
            "remarks": null,
            "leave": null
          }
        ]
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

    start, end = _month_bounds(year, month)
    emp = Employee.query.get(emp_id)
    if not emp:
        return _fail("Employee not found", 404)

    bywd = _rules_for(emp)
    leave_by_day = _approved_leave_map(emp_id, start, end)

    # iterate dates
    cur = start
    one = timedelta(days=1)
    days = []
    totals = {
        "present": 0,
        "partial": 0,
        "absent": 0,
        "weekly_off": 0,
        "holiday": 0,
        "no_shift": 0,
        "work_minutes": 0,
        "late_minutes": 0,
        "early_minutes": 0,
        "leave_full": 0,
        "leave_half": 0,
    }

    while cur <= end:
        row = _compute_day(emp, cur, bywd)

        # ---- Leave overlay (non-destructive) ----
        lv = leave_by_day.get(cur)
        if lv:
            row["leave"] = {
                "type_id": lv.get("type_id"),
                "type_code": lv.get("type_code"),
                "type_name": lv.get("type_name"),
                "part_day": lv.get("part_day"),
            }
            # If it's not Holiday/WeeklyOff, annotate/override status
            if row["status"] not in ("Holiday", "WeeklyOff"):
                has_punches = bool(row.get("punches"))
                part = lv.get("part_day") in ("am", "pm", "half")

                if not has_punches and not part:
                    # full-day leave
                    row["status"] = f"Leave({lv.get('type_code')})"
                    totals["leave_full"] += 1
                else:
                    # half-day + present/partial
                    row["status_detail"] = (
                        f"HalfLeave({lv.get('type_code')}"
                        f"{' ' + lv.get('part_day') if lv.get('part_day') else ''})"
                    )
                    totals["leave_half"] += 1

        days.append(row)

        # ---- Totals ----
        st = row.get("status")
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

        totals["work_minutes"] += int(row.get("work_minutes") or 0)
        totals["late_minutes"] += int(row.get("late_minutes") or 0)
        totals["early_minutes"] += int(row.get("early_minutes") or 0)

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
    return _ok(data)
