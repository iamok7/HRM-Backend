from datetime import datetime, date as _date
from flask import Blueprint, request, current_app
from flask_jwt_extended import get_jwt_identity, jwt_required
from hrms_api.common.http import ok, fail
from hrms_api.common.auth import requires_perms
from hrms_api.extensions import db
from hrms_api.models.user import User
from hrms_api.models.employee import Employee
from hrms_api.models.attendance_missed import MissedPunchRequest
from hrms_api.services.attendance_engine import upsert_manual_punch, recompute_daily

import logging
log = logging.getLogger(__name__)


bp = Blueprint("attendance_missed", __name__, url_prefix="/api/v1/attendance/missed-punch")

def _parse_date(s):
    return datetime.strptime(s, "%Y-%m-%d").date()

def _parse_time_or_none(s):
    if not s: return None
    return datetime.strptime(s, "%H:%M").time()

def _resolve_emp_id(uid: int, provided_id: int | None) -> int | None:
    """Return employee_id either from request body or by mapping user->employee when available."""
    if provided_id:
        return int(provided_id)
    # Only try lookup if the model actually has 'user_id'
    col = getattr(Employee, "user_id", None)
    if col is not None:
        emp = Employee.query.filter(col == uid).first()
        if emp:
            return emp.id
    return None

@bp.get("")
@jwt_required()
def list_requests():
    uid = get_jwt_identity()
    status = request.args.get("status")
    mine = request.args.get("mine") == "1"

    q = MissedPunchRequest.query
    if status:
        q = q.filter(MissedPunchRequest.status == status)

    if mine:
        emp_id = _resolve_emp_id(uid, None)
        if not emp_id:
            return ok([])  # or: return fail("Link employee or pass employee_id", 422)
        q = q.filter(MissedPunchRequest.employee_id == emp_id)

    rows = [{
        "id": r.id, "employee_id": r.employee_id, "date": r.req_date.isoformat(),
        "in_time": r.in_time.isoformat() if r.in_time else None,
        "out_time": r.out_time.isoformat() if r.out_time else None,
        "reason": r.reason, "status": r.status,
        "approved_by": r.approved_by,
        "approved_at": r.approved_at.isoformat() if r.approved_at else None,
        "approver_note": r.approver_note
    } for r in q.order_by(MissedPunchRequest.created_at.desc()).all()]
    return ok(rows)



@bp.post("")
@jwt_required()
def create_request():
    uid = get_jwt_identity()
    data = request.get_json(silent=True) or {}

    # --- parse date ---
    ds = (data.get("date") or "").strip()
    if not ds:
        return fail("Field 'date' is required (YYYY-MM-DD).", 422)
    try:
        req_date = datetime.strptime(ds, "%Y-%m-%d").date()
    except Exception:
        return fail("Invalid 'date' format. Use YYYY-MM-DD.", 422)

    # --- parse times ---
    def _t(v):
        if v in (None, "", "null"):
            return None
        try:
            return datetime.strptime(v, "%H:%M").time()
        except Exception:
            raise ValueError("Invalid time (use HH:MM)")

    try:
        in_time  = _t(data.get("in_time"))
        out_time = _t(data.get("out_time"))
    except ValueError as e:
        return fail(str(e), 422)

    # --- resolve employee_id (prefer explicit) ---
    emp_id = _resolve_emp_id(uid, data.get("employee_id"))
    if not emp_id:
        return fail("Employee profile not found. Pass 'employee_id' in body.", 422)

    r = MissedPunchRequest(
        employee_id=emp_id,
        req_date=req_date,
        in_time=in_time,
        out_time=out_time,
        reason=data.get("reason"),
    )
    db.session.add(r)
    db.session.commit()
    return ok({"id": r.id}, 201)


# APPROVE
@bp.put("/<int:rid>/approve")
@requires_perms("attendance.missed.approve")
def approve_request(rid: int):
    from datetime import datetime
    data = request.get_json(silent=True) or {}          # << force=False
    note = (data.get("note") or "") if isinstance(data, dict) else ""

    r = MissedPunchRequest.query.get(rid)
    if not r: return fail("Not found", 404)
    if r.status != "pending": return fail("Already processed", 409)

    r.status = "approved"
    r.approved_by = get_jwt_identity()
    r.approved_at = datetime.utcnow()
    r.approver_note = note
    db.session.commit()

    try:
        if r.in_time:
            upsert_manual_punch(r.employee_id, r.req_date, "IN", r.in_time, source="missed")
        if r.out_time:
            upsert_manual_punch(r.employee_id, r.req_date, "OUT", r.out_time, source="missed")
        recompute_daily(r.employee_id, r.req_date)
    except Exception as e:
        current_app.logger.exception("missed-punch hook failed: %s", e)
    # optional hooks...
    return ok({"status": "approved"})

# REJECT
@bp.put("/<int:rid>/reject")
@requires_perms("attendance.missed.approve")
def reject_request(rid: int):
    from datetime import datetime
    data = request.get_json(silent=True) or {}          # << force=False
    note = (data.get("note") or "") if isinstance(data, dict) else ""

    r = MissedPunchRequest.query.get(rid)
    if not r: return fail("Not found", 404)
    if r.status != "pending": return fail("Already processed", 409)

    r.status = "rejected"
    r.approved_by = get_jwt_identity()
    r.approved_at = datetime.utcnow()
    r.approver_note = note
    db.session.commit()
    return ok({"status": "rejected"})

