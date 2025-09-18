from datetime import datetime, date, time, timedelta
import calendar as pycal
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required
from sqlalchemy import or_, desc
from hrms_api.common.auth import requires_roles
from hrms_api.extensions import db

from hrms_api.models.attendance_punch import AttendancePunch
from hrms_api.models.employee import Employee
from hrms_api.models.attendance import Holiday, WeeklyOffRule, Shift
from hrms_api.models.attendance_assignment import EmployeeShiftAssignment

bp = Blueprint("attendance_punches", __name__, url_prefix="/api/v1/attendance")

from hrms_api.common.auth import requires_perms

# ------------------- helpers -------------------
def _ok(data=None, status=200, **meta):
    payload = {"success": True, "data": data}
    if meta: payload["meta"] = meta
    return jsonify(payload), status

def _fail(msg, status=400):
    return jsonify({"success": False, "error": {"message": msg}}), status

def _parse_dt(s):
    if not s: return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
        try: return datetime.strptime(s, fmt)
        except Exception: pass
    return None

def _parse_d(s):
    if not s: return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y"):
        try: return datetime.strptime(s, fmt).date()
        except Exception: pass
    return None

def _serialize_punch(p):
    return {
        "id": p.id,
        "employee_id": p.employee_id,
        "ts": p.ts.isoformat(sep=" "),
        "kind": p.kind,
        "source": getattr(p, "source", None),
        "note": getattr(p, "note", None),
    }

# ------------------- list/create/delete punches -------------------
@bp.get("/punches")
@jwt_required()
def list_punches():
    emp_id = request.args.get("employeeId", type=int) or request.args.get("employee_id", type=int)
    if not emp_id:
        return _fail("employeeId is required", 422)

    page  = request.args.get("page", type=int, default=1)
    limit = request.args.get("limit", type=int, default=50)

    q = AttendancePunch.query.filter_by(employee_id=emp_id).order_by(desc(AttendancePunch.ts))
    items = q.limit(limit).offset((page - 1) * limit).all()
    total = q.count()

    return _ok({
        "items": [_serialize_punch(p) for p in items],
        "pagination": {"page": page, "limit": limit, "total": total}
    })

@bp.post("/punches")
@requires_perms("attendance.punch.create")
def create_punch():
    d = request.get_json(silent=True, force=True) or {}
    eid  = d.get("employee_id")
    kind = (d.get("kind") or "").lower()
    ts   = _parse_dt(d.get("ts")) or datetime.now()
    src  = (d.get("source") or "api").lower()
    note = d.get("note")

    if not (eid and kind in ("in","out")):
        return _fail("employee_id and kind ('in'|'out') are required", 422)
    if not Employee.query.get(eid):
        return _fail("Employee not found", 404)

    p = AttendancePunch(employee_id=eid, ts=ts, kind=kind, source=src, note=note)
    db.session.add(p); db.session.commit()
    return _ok(_serialize_punch(p), status=201)

@bp.delete("/punches/<int:pid>")
@requires_roles("admin")
def delete_punch(pid: int):
    p = AttendancePunch.query.get(pid)
    if not p: return _fail("Punch not found", 404)
    db.session.delete(p); db.session.commit()
    return _ok({"id": pid, "deleted": True})

# ------------------- daily status -------------------
def _week_in_month(d: date) -> int:
    return (d.day - 1)//7 + 1

def _rules_for(emp: Employee):
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

def _shift_on(emp_id: int, d: date):
    rec = (
        db.session.query(EmployeeShiftAssignment, Shift)
        .join(Shift, EmployeeShiftAssignment.shift_id == Shift.id)
        .filter(
            EmployeeShiftAssignment.employee_id == emp_id,
            or_(EmployeeShiftAssignment.end_date == None, EmployeeShiftAssignment.end_date >= d),  # noqa
            EmployeeShiftAssignment.start_date <= d
        ).order_by(EmployeeShiftAssignment.start_date.desc()).first()
    )
    if not rec: return None
    _, s = rec
    return {
        "id": s.id, "code": s.code, "name": s.name,
        "start_time": s.start_time, "end_time": s.end_time,
        "break_minutes": s.break_minutes, "grace_minutes": s.grace_minutes,
        "is_night": s.is_night
    }

@bp.get("/daily-status")
@jwt_required()
def daily_status():
    emp_id = request.args.get("employeeId", type=int)
    dstr   = request.args.get("date")
    if not (emp_id and dstr): return _fail("employeeId and date are required", 422)
    day = _parse_d(dstr)
    if not day: return _fail("date must be YYYY-MM-DD", 422)

    emp = Employee.query.get(emp_id)
    if not emp: return _fail("Employee not found", 404)

    # Holiday?
    hol = Holiday.query.filter(
        Holiday.company_id == emp.company_id,
        Holiday.date == day,
        or_(Holiday.location_id == emp.location_id, Holiday.location_id.is_(None))
    ).order_by(Holiday.location_id.desc().nullslast()).first()
    is_holiday = bool(hol)
    holiday_name = hol.name if hol else None

    # Weekly off?
    bywd = _rules_for(emp)
    wd = day.weekday()
    is_wo = False
    for is_alt, weeks in bywd.get(wd, []):
        if not is_alt: is_wo = True; break
        if _week_in_month(day) in weeks: is_wo = True; break

    # Shift for the day
    sh = _shift_on(emp_id, day)  # may be None

    # Logical punch span (handles night shift)
    span_start = datetime.combine(day, time.min)
    span_end   = datetime.combine(day, time.max)
    if sh and sh["is_night"]:
        span_end = span_end + timedelta(days=1)

    punches = AttendancePunch.query.filter(
        AttendancePunch.employee_id == emp_id,
        AttendancePunch.ts >= span_start,
        AttendancePunch.ts <= span_end
    ).order_by(AttendancePunch.ts.asc()).all()

    serial = [{"id":p.id, "ts":p.ts.isoformat(sep=' '), "kind":p.kind, "source":getattr(p, "source", None)} for p in punches]
    first_in = next((p.ts for p in punches if p.kind == "in"), None)
    last_out = next((p.ts for p in reversed(punches) if p.kind == "out"), None)

    shift_info = None
    late_min = early_min = work_min = None
    status = "Off"
    remarks = []

    if sh:
        shift_info = {
            "id": sh["id"], "code": sh["code"], "name": sh["name"],
            "start_time": sh["start_time"].strftime("%H:%M:%S"),
            "end_time": sh["end_time"].strftime("%H:%M:%S"),
            "break_minutes": sh["break_minutes"], "grace_minutes": sh["grace_minutes"],
            "is_night": sh["is_night"]
        }

        st_dt = datetime.combine(day, sh["start_time"])
        et_day = day if not sh["is_night"] else day + timedelta(days=1)
        et_dt = datetime.combine(et_day, sh["end_time"])

        if first_in and last_out:
            raw_min = int((last_out - first_in).total_seconds() // 60)
            work_min = max(raw_min - int(sh["break_minutes"] or 0), 0)
            if first_in > st_dt + timedelta(minutes=int(sh["grace_minutes"] or 0)):
                late_min = int((first_in - st_dt).total_seconds() // 60)
                remarks.append(f"Late by {late_min}m")
            if last_out < et_dt:
                early_min = int((et_dt - last_out).total_seconds() // 60)
                remarks.append(f"Early by {early_min}m")
            status = "Present"
        elif first_in or last_out:
            status = "Partial"
            remarks.append("Only one punch")
        else:
            status = "Absent"

    # Overrides
    if is_wo:
        status = "WeeklyOff"; remarks.append("Weekly Off")
    if is_holiday:
        status = "Holiday";   remarks.append(holiday_name or "Holiday")
    if not sh and not (is_wo or is_holiday):
        status = "NoShift"

    return _ok({
        "employee_id": emp_id,
        "date": day.isoformat(),
        "weekday": wd,
        "is_weekly_off": is_wo,
        "is_holiday": is_holiday,
        "holiday_name": holiday_name,
        "shift": shift_info,
        "punches": serial,
        "first_in": first_in.isoformat(sep=' ') if first_in else None,
        "last_out": last_out.isoformat(sep=' ') if last_out else None,
        "work_minutes": work_min,
        "late_minutes": late_min,
        "early_minutes": early_min,
        "status": status,
        "remarks": ", ".join(remarks) if remarks else None
    })
