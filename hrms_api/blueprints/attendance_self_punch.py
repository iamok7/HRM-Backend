# apps/backend/hrms_api/blueprints/attendance_self_punch.py
from datetime import datetime, timedelta
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity, get_jwt  # <-- add get_jwt
from sqlalchemy.exc import SQLAlchemyError

from hrms_api.extensions import db
from hrms_api.models.employee import Employee
from hrms_api.models.attendance_punch import AttendancePunch

# If you kept a soft import of User earlier, it's fine to keep. We won't rely on it.
try:
    from hrms_api.models.user import User as _UserModel
except Exception:
    _UserModel = None


# Keep the same path your Postman uses: /api/v1/attendance/punches/self
bp = Blueprint("attendance_self_punch", __name__, url_prefix="/api/v1/attendance/punches")

def _ok(data=None, status=200):
    return jsonify({"success": True, "data": data}), status

def _fail(msg, status=400):
    return jsonify({"success": False, "error": {"message": msg}}), status

def _resolve_employee_id(identity) -> int | None:
    """
    Priority:
    1) explicit ?employeeId= / body.employeeId (best for testing/kiosks)
    2) JWT claims: employee_id / employeeId / emp_id / empId (if you add this at login)
    3) Optional: User.employee_id property (if your User model exposes it)
    """
    # 1) explicit param/body
    emp_id = request.args.get("employeeId", type=int) or (request.json or {}).get("employeeId")
    if emp_id:
        return int(emp_id)

    # 2) JWT claims
    try:
        claims = get_jwt() or {}
        for key in ("employee_id", "employeeId", "emp_id", "empId"):
            if key in claims and claims[key]:
                try:
                    return int(claims[key])
                except Exception:
                    pass
    except Exception:
        pass

    # 3) Optional: User.employee_id property (if present)
    try:
        if _UserModel is not None and hasattr(_UserModel, "query"):
            u = _UserModel.query.get(identity)
            if u and getattr(u, "employee_id", None):
                return int(u.employee_id)
    except Exception:
        pass

    return None


def _last_punch(emp_id: int, hours: int = 24):
    since = datetime.now() - timedelta(hours=hours)
    return (AttendancePunch.query
            .filter(AttendancePunch.employee_id == emp_id,
                    AttendancePunch.ts >= since)
            .order_by(AttendancePunch.ts.desc())
            .first())

@bp.post("/self")
@jwt_required()
def self_punch():
    """
    POST /api/v1/attendance/punches/self
    Body: { "kind": "in" | "out", "ts": "YYYY-MM-DD HH:MM[:SS]" (optional), "employeeId" (optional) }
    """
    identity = get_jwt_identity()
    emp_id = _resolve_employee_id(identity)
    if not emp_id:
        return _fail("Could not resolve employeeId. Pass employeeId or include employee_id in JWT claims.", 422)

    emp = Employee.query.get(emp_id)
    if not emp:
        return _fail("Employee not found", 404)

    payload = request.get_json(silent=True) or {}
    kind = (payload.get("kind") or "").strip().lower()
    if kind not in ("in", "out"):
        return _fail("kind must be 'in' or 'out'", 422)

    ts_str = payload.get("ts")
    if ts_str:
        try:
            ts = datetime.fromisoformat(ts_str.replace("T", " "))
        except Exception:
            return _fail("Invalid ts. Use 'YYYY-MM-DD HH:MM[:SS]'", 422)
    else:
        ts = datetime.now()

    since = datetime.now() - timedelta(hours=24)
    prev = (AttendancePunch.query
            .filter(AttendancePunch.employee_id == emp_id,
                    AttendancePunch.ts >= since)
            .order_by(AttendancePunch.ts.desc())
            .first())

    if prev and prev.kind == kind:
        return _fail(f"Last punch was already '{kind}' at {prev.ts}. Please punch the opposite first.", 409)

    p = AttendancePunch(employee_id=emp_id, ts=ts, kind=kind)
    if hasattr(AttendancePunch, "source"):
        p.source = "self"

    try:
        db.session.add(p)
        db.session.commit()
    except SQLAlchemyError as e:
        db.session.rollback()
        return _fail(f"DB error: {str(e)}", 500)

    return _ok({
        "id": p.id,
        "employee_id": p.employee_id,
        "kind": p.kind,
        "ts": p.ts.isoformat(sep=" "),
        "source": getattr(p, "source", None)
    }, status=201)
