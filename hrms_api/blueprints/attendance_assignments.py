from __future__ import annotations
from datetime import datetime, timedelta, date

from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required
from sqlalchemy import or_

from hrms_api.extensions import db
from hrms_api.models.attendance_assignment import EmployeeShiftAssignment
from hrms_api.models.employee import Employee
from hrms_api.models.attendance import Shift

# RBAC (keep hard imports, but guard with fallback in case of tests)
try:
    from hrms_api.common.auth import requires_roles, requires_perms
except Exception:  # pragma: no cover - safety fallback
    def requires_roles(_):
        def _wrap(fn): return fn
        return _wrap

    def requires_perms(_):
        def _wrap(fn): return fn
        return _wrap

bp = Blueprint(
    "attendance_assignments",
    __name__,
    url_prefix="/api/v1/attendance/shift-assignments",
)

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


# ---------- helpers ----------
def _parse_date_any(s: str | None):
    """
    Accepts:
      - 'YYYY-MM-DD'  (canonical)
      - 'DD-MM-YYYY'  (legacy support)
    """
    if not s:
        return None
    for f in ("%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, f).date()
        except Exception:
            pass
    return None


def _as_int(val, field):
    if val in (None, "", "null"):
        return None
    try:
        return int(val)
    except Exception:
        raise ValueError(f"{field} must be integer")


def _row(a: EmployeeShiftAssignment):
    return {
        "id": a.id,
        "employee_id": a.employee_id,
        "shift_id": a.shift_id,
        "start_date": a.start_date.isoformat() if a.start_date else None,
        "end_date": a.end_date.isoformat() if a.end_date else None,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


def _overlaps(
    eid: int,
    s1: date,
    e1: date | None,
    exclude_id: int | None = None,
) -> EmployeeShiftAssignment | None:
    """
    Check if there is any assignment for this employee that overlaps [s1, e1].

    Overlap rule:
      existing.start <= e1 (or infinite) AND
      existing.end   >= s1 (or infinite)
    """
    q = EmployeeShiftAssignment.query.filter(
        EmployeeShiftAssignment.employee_id == eid
    )
    if exclude_id:
        q = q.filter(EmployeeShiftAssignment.id != exclude_id)

    # existing end is NULL OR >= new start
    q = q.filter(
        or_(
            EmployeeShiftAssignment.end_date == None,  # noqa: E711
            EmployeeShiftAssignment.end_date >= s1,
        )
    )
    # new end: if provided, existing.start <= new_end
    if e1:
        q = q.filter(EmployeeShiftAssignment.start_date <= e1)

    return q.first()


def _get_employee_arg() -> str | None:
    """
    Read employee from query, supporting both:
      - employee_id
      - employeeId
    """
    return request.args.get("employee_id") or request.args.get("employeeId")


# ---------- routes ----------

@bp.get("")
@jwt_required()
def list_assignments():
    """
    GET /api/v1/attendance/shift-assignments
      ?employeeId=123 | employee_id=123
      &from=2025-10-01
      &to=2025-10-31
      &active_on=2025-10-15
      &page=1&limit=20   (or &size=20)

    Filters:
      - employeeId / employee_id : assignments for that employee only
      - from / to                : overlap with a date window
      - active_on                : assignments active on that specific date

    Response:
    {
      "success": true,
      "data": [
        {
          "id": 1,
          "employee_id": 10,
          "shift_id": 3,
          "start_date": "2025-10-01",
          "end_date": null,
          "created_at": "2025-10-12T09:30:00.000000"
        }
      ],
      "meta": {
        "page": 1,
        "size": 20,
        "total": 1
      }
    }
    """
    # employee filter
    try:
        eid_raw = _get_employee_arg()
        eid = _as_int(eid_raw, "employeeId") if eid_raw is not None else None
    except ValueError as ex:
        return _fail(str(ex), 422)

    dfrom = _parse_date_any(request.args.get("from")) if request.args.get("from") else None
    dto = _parse_date_any(request.args.get("to")) if request.args.get("to") else None
    active_on = _parse_date_any(request.args.get("active_on")) if request.args.get("active_on") else None

    # pagination (limit/size alias)
    try:
        page = max(int(request.args.get("page", 1) or 1), 1)
    except Exception:
        page = 1
    raw = request.args.get("limit", request.args.get("size", 20))
    try:
        size = min(max(int(raw), 1), 100)
    except Exception:
        size = 20

    q = EmployeeShiftAssignment.query
    if eid:
        q = q.filter(EmployeeShiftAssignment.employee_id == eid)

    # overlap window
    if dfrom:
        q = q.filter(
            or_(
                EmployeeShiftAssignment.end_date == None,  # noqa: E711
                EmployeeShiftAssignment.end_date >= dfrom,
            )
        )
    if dto:
        q = q.filter(EmployeeShiftAssignment.start_date <= dto)

    # active_on (specific day)
    if active_on:
        q = q.filter(
            EmployeeShiftAssignment.start_date <= active_on,
            or_(
                EmployeeShiftAssignment.end_date == None,  # noqa: E711
                EmployeeShiftAssignment.end_date >= active_on,
            ),
        )

    total = q.count()
    items = (
        q.order_by(
            EmployeeShiftAssignment.employee_id.asc(),
            EmployeeShiftAssignment.start_date.desc(),
        )
        .offset((page - 1) * size)
        .limit(size)
        .all()
    )
    return _ok(
        [_row(i) for i in items],
        page=page,
        size=size,
        total=total,
    )


@bp.post("")
@jwt_required()
@requires_perms("attendance.assign.create")
@requires_roles("admin")
def create_assignment():
    """
    POST /api/v1/attendance/shift-assignments

    Body:
    {
      "employeeId": 10,          // or "employee_id"
      "shift_id": 3,
      "start_date": "2025-10-01",
      "end_date": "2025-10-31",  // optional, null = open-ended
      "auto_close": true         // default: true
    }

    Rules:
      - employeeId, shift_id, start_date are required
      - end_date >= start_date (if provided)
      - If an overlapping open assignment exists and auto_close=true,
        we close the previous assignment at (start_date - 1 day).
      - Otherwise we reject with 409 Overlapping assignment.
    """
    d = request.get_json(silent=True, force=True) or {}

    # accept employeeId or employee_id
    raw_eid = d.get("employee_id", d.get("employeeId"))
    try:
        eid = _as_int(raw_eid, "employeeId/employee_id")
        sid = _as_int(d.get("shift_id"), "shift_id")
    except ValueError as ex:
        return _fail(str(ex), 422)

    sdt = _parse_date_any(d.get("start_date")) if d.get("start_date") else None
    edt = _parse_date_any(d.get("end_date")) if d.get("end_date") else None
    auto_close = bool(d.get("auto_close", True))

    if not (eid and sid and sdt):
        return _fail("employee_id, shift_id, start_date are required", 422)
    if edt and edt < sdt:
        return _fail("end_date cannot be before start_date", 422)

    if not Employee.query.get(eid):
        return _fail("Employee not found", 404)
    if not Shift.query.get(sid):
        return _fail("Shift not found", 404)

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

    a = EmployeeShiftAssignment(
        employee_id=eid,
        shift_id=sid,
        start_date=sdt,
        end_date=edt,
    )
    db.session.add(a)
    db.session.commit()

    # (optional future) recompute attendance for this date range if engine exists
    # we keep hook pattern same as punches/self-punch for later.

    return _ok(_row(a), status=201)


@bp.put("/<int:aid>")
@jwt_required()
@requires_perms("attendance.assign.create")
@requires_roles("admin")
def update_assignment(aid: int):
    """
    PUT /api/v1/attendance/shift-assignments/{id}

    Body (all optional, only provided fields will be updated):
    {
      "shift_id": 4,
      "start_date": "2025-10-05",
      "end_date": "2025-10-31"
    }

    Rules:
      - end_date >= start_date
      - cannot overlap with other assignments of the same employee
    """
    a = EmployeeShiftAssignment.query.get(aid)
    if not a:
        return _fail("Assignment not found", 404)
    d = request.get_json(silent=True, force=True) or {}

    # optional shift change
    if "shift_id" in d:
        try:
            sid = _as_int(d.get("shift_id"), "shift_id")
        except ValueError as ex:
            return _fail(str(ex), 422)
        if not Shift.query.get(sid):
            return _fail("Shift not found", 404)
        a.shift_id = sid

    # date updates
    sdt = a.start_date
    edt = a.end_date
    if "start_date" in d and d["start_date"]:
        parsed = _parse_date_any(d["start_date"])
        sdt = parsed or a.start_date
    if "end_date" in d:
        edt = _parse_date_any(d["end_date"]) if d["end_date"] else None

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
def delete_assignment(aid: int):
    """
    DELETE /api/v1/attendance/shift-assignments/{id}

    Response:
    {
      "success": true,
      "data": { "id": 12, "deleted": true }
    }
    """
    a = EmployeeShiftAssignment.query.get(aid)
    if not a:
        return _fail("Assignment not found", 404)
    db.session.delete(a)
    db.session.commit()
    return _ok({"id": aid, "deleted": True})
