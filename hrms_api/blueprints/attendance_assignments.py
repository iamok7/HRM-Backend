from datetime import datetime, timedelta
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required
from hrms_api.common.auth import requires_roles
from hrms_api.extensions import db
from hrms_api.models.attendance_assignment import EmployeeShiftAssignment
from hrms_api.models.employee import Employee
from hrms_api.models.attendance import Shift

from hrms_api.common.auth import requires_perms
from flask_jwt_extended import jwt_required

bp = Blueprint("attendance_assignments", __name__, url_prefix="/api/v1/attendance/shift-assignments")

def _ok(data=None, status=200, **meta):
    payload = {"success": True, "data": data}
    if meta: payload["meta"] = meta
    return jsonify(payload), status

def _fail(msg, status=400):
    return jsonify({"success": False, "error": {"message": msg}}), status

def _d(s):
    for f in ("%Y-%m-%d", "%d-%m-%Y"):
        try: return datetime.strptime(s, f).date()
        except: pass
    return None

def _row(a: EmployeeShiftAssignment, with_names=False):
    r = {
        "id": a.id, "employee_id": a.employee_id, "shift_id": a.shift_id,
        "start_date": a.start_date.isoformat(),
        "end_date": a.end_date.isoformat() if a.end_date else None,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }
    if with_names:
        # optional names via joinedload if you want; keeping simple here
        pass
    return r

def _overlaps(eid, s1, e1):
    q = EmployeeShiftAssignment.query.filter(EmployeeShiftAssignment.employee_id == eid)
    # overlap logic: (e.end is null or e.end >= s1) and (e.start <= e1 or e1 is null)
    q = q.filter(
        db.or_(EmployeeShiftAssignment.end_date == None, EmployeeShiftAssignment.end_date >= s1)  # noqa: E711
    )
    if e1:
        q = q.filter(EmployeeShiftAssignment.start_date <= e1)
    return q.first()

@bp.get("")
@jwt_required()
def list_assignments():
    eid = request.args.get("employeeId", type=int)
    dfrom = _d(request.args.get("from")) if request.args.get("from") else None
    dto   = _d(request.args.get("to")) if request.args.get("to") else None
    page  = max(request.args.get("page", default=1, type=int), 1)
    limit = min(max(request.args.get("limit", default=20, type=int), 1), 100)

    q = EmployeeShiftAssignment.query
    if eid: q = q.filter_by(employee_id=eid)
    if dfrom: q = q.filter(db.or_(EmployeeShiftAssignment.end_date == None, EmployeeShiftAssignment.end_date >= dfrom))  # noqa: E711
    if dto:   q = q.filter(EmployeeShiftAssignment.start_date <= dto)

    total = q.count()
    items = q.order_by(EmployeeShiftAssignment.employee_id.asc(), EmployeeShiftAssignment.start_date.desc())\
             .offset((page-1)*limit).limit(limit).all()
    return _ok([_row(i) for i in items], page=page, limit=limit, total=total)

@bp.post("")
@requires_perms("attendance.assign.create")
@requires_roles("admin")
def create_assignment():
    d = request.get_json(silent=True, force=True) or {}
    eid = d.get("employee_id")
    sid = d.get("shift_id")
    sdt = _d(d.get("start_date")) if d.get("start_date") else None
    edt = _d(d.get("end_date")) if d.get("end_date") else None
    auto_close = bool(d.get("auto_close", True))

    if not (eid and sid and sdt):
        return _fail("employee_id, shift_id, start_date are required", 422)
    if edt and edt < sdt:
        return _fail("end_date cannot be before start_date", 422)
    if not Employee.query.get(eid): return _fail("Employee not found", 404)
    if not Shift.query.get(sid):     return _fail("Shift not found", 404)

    # handle overlap
    ov = _overlaps(eid, sdt, edt)
    if ov:
        if auto_close and (ov.start_date <= sdt) and (ov.end_date is None or ov.end_date >= sdt):
            # close previous open range the day before new start
            if ov.end_date is None or ov.end_date >= sdt:
                ov.end_date = sdt - timedelta(days=1)
                db.session.add(ov)
        else:
            return _fail("Overlapping assignment exists", 409)

    a = EmployeeShiftAssignment(employee_id=eid, shift_id=sid, start_date=sdt, end_date=edt)
    db.session.add(a); db.session.commit()
    return _ok(_row(a), status=201)

@bp.put("/<int:aid>")
@requires_perms("attendance.assign.create")
@requires_roles("admin")
def update_assignment(aid):
    a = EmployeeShiftAssignment.query.get(aid)
    if not a: return _fail("Assignment not found", 404)
    d = request.get_json(silent=True, force=True) or {}

    if "shift_id" in d:
        if not Shift.query.get(d["shift_id"]): return _fail("Shift not found", 404)
        a.shift_id = int(d["shift_id"])

    sdt = a.start_date
    edt = a.end_date
    if "start_date" in d and d["start_date"]:
        sdt = _d(d["start_date"]) or a.start_date
    if "end_date" in d:
        edt = _d(d["end_date"]) if d["end_date"] else None
    if edt and edt < sdt:
        return _fail("end_date cannot be before start_date", 422)

    # overlap check (exclude self)
    ov = EmployeeShiftAssignment.query.filter(
        EmployeeShiftAssignment.employee_id == a.employee_id,
        EmployeeShiftAssignment.id != a.id,
        db.or_(EmployeeShiftAssignment.end_date == None, EmployeeShiftAssignment.end_date >= sdt),  # noqa: E711
    )
    if edt:
        ov = ov.filter(EmployeeShiftAssignment.start_date <= edt)
    if ov.first():
        return _fail("Overlapping assignment exists", 409)

    a.start_date, a.end_date = sdt, edt
    db.session.commit()
    return _ok(_row(a))

@bp.delete("/<int:aid>")
@requires_roles("admin")
def delete_assignment(aid):
    a = EmployeeShiftAssignment.query.get(aid)
    if not a: return _fail("Assignment not found", 404)
    db.session.delete(a); db.session.commit()
    return _ok({"id": aid, "deleted": True})
