from __future__ import annotations
from datetime import datetime, timedelta, date
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required
from hrms_api.common.auth import requires_roles
from hrms_api.extensions import db
from hrms_api.models.attendance_assignment import EmployeeShiftAssignment
from hrms_api.models.employee import Employee
from hrms_api.models.attendance import Shift

from hrms_api.common.auth import requires_perms

bp = Blueprint("attendance_assignments", __name__, url_prefix="/api/v1/attendance/shift-assignments")

# ---------- envelopes ----------
def _ok(data=None, status=200, **meta):
    payload = {"success": True, "data": data}
    if meta: payload["meta"] = meta
    return jsonify(payload), status

def _fail(msg, status=400, code=None, detail=None):
    err = {"message": msg}
    if code: err["code"] = code
    if detail: err["detail"] = detail
    return jsonify({"success": False, "error": err}), status

# ---------- helpers ----------
def _d(s):
    if not s: return None
    for f in ("%Y-%m-%d", "%d-%m-%Y"):
        try: return datetime.strptime(s, f).date()
        except Exception: pass
    return None

def _as_int(val, field):
    if val in (None, "", "null"): return None
    try:
        return int(val)
    except Exception:
        raise ValueError(f"{field} must be integer")

def _row(a: EmployeeShiftAssignment):
    return {
        "id": a.id,
        "employee_id": a.employee_id,
        "shift_id": a.shift_id,
        "start_date": a.start_date.isoformat(),
        "end_date": a.end_date.isoformat() if a.end_date else None,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }

def _overlaps(eid: int, s1: date, e1: date | None, exclude_id: int | None = None):
    q = EmployeeShiftAssignment.query.filter(EmployeeShiftAssignment.employee_id == eid)
    if exclude_id:
        q = q.filter(EmployeeShiftAssignment.id != exclude_id)
    # overlap if (existing.end is null or >= s1) AND (e1 is null or existing.start <= e1)
    q = q.filter(db.or_(EmployeeShiftAssignment.end_date == None, EmployeeShiftAssignment.end_date >= s1))  # noqa: E711
    if e1:
        q = q.filter(EmployeeShiftAssignment.start_date <= e1)
    return q.first()

# ---------- routes ----------

@bp.get("")
@jwt_required()
def list_assignments():
    """
    GET /api/v1/attendance/shift-assignments?employeeId=&from=&to=&active_on=&page=&limit=
    - employeeId | employee_id : filter by employee
    - from / to                : overlap window
    - active_on                : show assignments active on a specific date (YYYY-MM-DD)
    - page, limit (or size)    : pagination
    """
    try:
        eid = _as_int(request.args.get("employeeId") or request.args.get("employee_id"), "employeeId")
    except ValueError as ex:
        return _fail(str(ex), 422)

    dfrom = _d(request.args.get("from")) if request.args.get("from") else None
    dto   = _d(request.args.get("to")) if request.args.get("to") else None
    active_on = _d(request.args.get("active_on")) if request.args.get("active_on") else None

    # pagination (limit/size alias)
    page  = max(int(request.args.get("page", 1) or 1), 1)
    raw   = request.args.get("limit", request.args.get("size", 20))
    try:
        limit = min(max(int(raw), 1), 100)
    except Exception:
        limit = 20

    q = EmployeeShiftAssignment.query
    if eid:
        q = q.filter(EmployeeShiftAssignment.employee_id == eid)

    # overlap window
    if dfrom:
        q = q.filter(db.or_(EmployeeShiftAssignment.end_date == None, EmployeeShiftAssignment.end_date >= dfrom))  # noqa: E711
    if dto:
        q = q.filter(EmployeeShiftAssignment.start_date <= dto)

    # active_on (specific day)
    if active_on:
        q = q.filter(
            EmployeeShiftAssignment.start_date <= active_on,
            db.or_(EmployeeShiftAssignment.end_date == None, EmployeeShiftAssignment.end_date >= active_on)  # noqa: E711
        )

    total = q.count()
    items = (q.order_by(EmployeeShiftAssignment.employee_id.asc(),
                        EmployeeShiftAssignment.start_date.desc())
               .offset((page - 1) * limit).limit(limit).all())
    return _ok([_row(i) for i in items], page=page, limit=limit, total=total)

@bp.post("")
@jwt_required()
@requires_perms("attendance.assign.create")
@requires_roles("admin")
def create_assignment():
    d = request.get_json(silent=True, force=True) or {}
    try:
        eid = _as_int(d.get("employee_id"), "employee_id")
        sid = _as_int(d.get("shift_id"), "shift_id")
    except ValueError as ex:
        return _fail(str(ex), 422)

    sdt = _d(d.get("start_date")) if d.get("start_date") else None
    edt = _d(d.get("end_date")) if d.get("end_date") else None
    auto_close = bool(d.get("auto_close", True))

    if not (eid and sid and sdt):
        return _fail("employee_id, shift_id, start_date are required", 422)
    if edt and edt < sdt:
        return _fail("end_date cannot be before start_date", 422)

    if not Employee.query.get(eid): return _fail("Employee not found", 404)
    if not Shift.query.get(sid):    return _fail("Shift not found", 404)

    # overlap handling
    ov = _overlaps(eid, sdt, edt)
    if ov:
        if auto_close and (ov.start_date <= sdt) and (ov.end_date is None or ov.end_date >= sdt):
            # close the previous open/overlapping assignment right before new start
            new_end = sdt - timedelta(days=1)
            if ov.end_date is None or ov.end_date >= sdt:
                ov.end_date = new_end
                db.session.add(ov)
        else:
            return _fail("Overlapping assignment exists", 409)

    a = EmployeeShiftAssignment(employee_id=eid, shift_id=sid, start_date=sdt, end_date=edt)
    db.session.add(a); db.session.commit()
    return _ok(_row(a), status=201)

@bp.put("/<int:aid>")
@jwt_required()
@requires_perms("attendance.assign.create")
@requires_roles("admin")
def update_assignment(aid):
    a = EmployeeShiftAssignment.query.get(aid)
    if not a: return _fail("Assignment not found", 404)
    d = request.get_json(silent=True, force=True) or {}

    # optional shift change
    if "shift_id" in d:
        try:
            sid = _as_int(d.get("shift_id"), "shift_id")
        except ValueError as ex:
            return _fail(str(ex), 422)
        if not Shift.query.get(sid): return _fail("Shift not found", 404)
        a.shift_id = sid

    # date updates
    sdt = a.start_date
    edt = a.end_date
    if "start_date" in d and d["start_date"]:
        sdt = _d(d["start_date"]) or a.start_date
    if "end_date" in d:
        edt = _d(d["end_date"]) if d["end_date"] else None
    if edt and edt < sdt:
        return _fail("end_date cannot be before start_date", 422)

    # overlap check (exclude self)
    ov = _overlaps(a.employee_id, sdt, edt, exclude_id=a.id)
    if ov:
        return _fail("Overlapping assignment exists", 409)

    a.start_date, a.end_date = sdt, edt
    db.session.commit()
    return _ok(_row(a))

@bp.delete("/<int:aid>")
@jwt_required()
@requires_roles("admin")
def delete_assignment(aid):
    a = EmployeeShiftAssignment.query.get(aid)
    if not a: return _fail("Assignment not found", 404)
    db.session.delete(a); db.session.commit()
    return _ok({"id": aid, "deleted": True})
