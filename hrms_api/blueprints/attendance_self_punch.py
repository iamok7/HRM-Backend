# hrms_api/blueprints/attendance_self_punch.py
from __future__ import annotations
from datetime import datetime, date, timedelta
from typing import Optional, Any

from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from sqlalchemy import and_, asc, desc

from hrms_api.extensions import db
from hrms_api.models.employee import Employee
from hrms_api.models.attendance_punch import AttendancePunch

# Optional permissions: if not present, endpoints still work with JWT only.
try:
    from hrms_api.common.auth import requires_perms
except Exception:
    def requires_perms(_):  # noop fallback
        def _wrap(fn): return fn
        return _wrap

bp = Blueprint("attendance_self_punch", __name__, url_prefix="/api/v1/attendance/self-punches")

# ---- settings (tune as needed) ----
MAX_SELF_PUNCHES_PER_DAY = 6        # prevent spam
SELF_PUNCH_BACKDATE_DAYS = 3        # how far back a user can self-punch

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
_DT_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M",
)

def _parse_ts(s: str | None):
    if not s: return None
    s = s.strip()
    try:
        return datetime.fromisoformat(s.replace(" ", "T"))
    except Exception:
        pass
    for fmt in _DT_FORMATS:
        try: return datetime.strptime(s, fmt)
        except Exception: pass
    return None

def _normalize_kind(v: Any) -> Optional[str]:
    if v is None: return None
    s = str(v).strip().lower()
    if s in ("in", "out"): return s
    return None

def _get_employee_id_from_jwt() -> Optional[int]:
    """
    Try common identity shapes:
      - int employee_id directly
      - {"employee_id": 123, ...}
      - {"user_id": 7, ...} with Employee.user_id relation (fallback)
    """
    ident = get_jwt_identity()
    # direct int
    if isinstance(ident, int):
        return ident
    # dict-like
    if isinstance(ident, dict):
        if ident.get("employee_id"): 
            try: return int(ident["employee_id"])
            except Exception: pass
        if ident.get("emp_id"): 
            try: return int(ident["emp_id"])
            except Exception: pass
        # optional fallback via user_id -> employee
        uid = ident.get("user_id") or ident.get("id")
        if uid:
            try:
                # if your Employee has user_id FK:
                emp = Employee.query.filter_by(user_id=int(uid)).first()
                if emp: return emp.id
            except Exception:
                pass
    return None

def _recompute_after(emp_id: int, day: date):
    try:
        from hrms_api.services.attendance_engine import recompute_daily
    except Exception:
        recompute_daily = None
    if recompute_daily:
        try:
            recompute_daily(emp_id, day)
        except Exception:
            pass

def _row(p: AttendancePunch):
    return {
        "id": p.id,
        "employee_id": p.employee_id,
        "ts": p.ts.isoformat() if p.ts else None,
        "kind": p.kind,
        "source": getattr(p, "source", None),
        "note": getattr(p, "note", None),
        "created_at": getattr(p, "created_at", None).isoformat() if getattr(p, "created_at", None) else None,
        "updated_at": getattr(p, "updated_at", None).isoformat() if getattr(p, "updated_at", None) else None,
    }

# ---------- routes ----------

@bp.get("")
@jwt_required()
@requires_perms("attendance.self.read")
def list_my_punches():
    """
    GET /api/v1/attendance/self-punches?from=&to=&kind=&page=&size=
    - from/to: YYYY-MM-DD (inclusive day window)
    - kind: in|out
    - page/size: pagination
    """
    emp_id = _get_employee_id_from_jwt()
    if not emp_id:
        return _fail("Employee context not found on token", 401)
    if not Employee.query.get(emp_id):
        return _fail("Employee not found", 404)

    dfrom = request.args.get("from") or request.args.get("date_from")
    dto   = request.args.get("to")   or request.args.get("date_to")
    df = datetime.strptime(dfrom, "%Y-%m-%d").date() if dfrom else None
    dt = datetime.strptime(dto, "%Y-%m-%d").date()   if dto   else None

    k = _normalize_kind(request.args.get("kind"))

    try:
        page = max(int(request.args.get("page", 1)), 1)
    except Exception:
        page = 1
    raw = request.args.get("size", request.args.get("limit", 20))
    try:
        size = max(min(int(raw), 100), 1)
    except Exception:
        size = 20

    q = AttendancePunch.query.filter(AttendancePunch.employee_id == emp_id)
    if df:
        q = q.filter(AttendancePunch.ts >= datetime.combine(df, datetime.min.time()))
    if dt:
        q = q.filter(AttendancePunch.ts <= datetime.combine(dt, datetime.max.time()))
    if k:
        q = q.filter(AttendancePunch.kind == k)

    q = q.order_by(desc(AttendancePunch.ts))
    total = q.count()
    items = q.offset((page - 1) * size).limit(size).all()
    return _ok([_row(i) for i in items], page=page, size=size, total=total)

@bp.post("")
@jwt_required()
@requires_perms("attendance.self.create")
def create_my_punch():
    """
    Body:
    {
      "ts": "YYYY-MM-DD HH:MM[:SS]" or ISO,
      "kind": "in" | "out",
      "note": "optional reason"
    }
    Rules:
      - Daily cap (MAX_SELF_PUNCHES_PER_DAY)
      - Backdate only up to SELF_PUNCH_BACKDATE_DAYS
      - Duplicate protection on (employee_id, ts, kind)
    """
    emp_id = _get_employee_id_from_jwt()
    if not emp_id:
        return _fail("Employee context not found on token", 401)
    if not Employee.query.get(emp_id):
        return _fail("Employee not found", 404)

    d = request.get_json(silent=True, force=True) or {}
    ts = _parse_ts(d.get("ts"))
    kind = _normalize_kind(d.get("kind"))
    note = (d.get("note") or "").strip() or None

    if not (ts and kind):
        return _fail("ts and kind are required", 422)

    # backdate & future sanity
    today = date.today()
    min_day = today - timedelta(days=SELF_PUNCH_BACKDATE_DAYS)
    if ts.date() < min_day:
        return _fail(f"Backdate window exceeded (max {SELF_PUNCH_BACKDATE_DAYS} days)", 422)
    # allow future a little (e.g., up to now only)
    if ts > datetime.now() + timedelta(minutes=5):
        return _fail("Future timestamp not allowed", 422)

    # daily cap
    day_start = datetime.combine(ts.date(), datetime.min.time())
    day_end   = datetime.combine(ts.date(), datetime.max.time())
    day_count = AttendancePunch.query.filter(
        AttendancePunch.employee_id == emp_id,
        AttendancePunch.ts >= day_start,
        AttendancePunch.ts <= day_end
    ).count()
    if day_count >= MAX_SELF_PUNCHES_PER_DAY:
        return _fail("Daily self-punch limit reached", 409)

    # duplicate
    dup = AttendancePunch.query.filter(
        and_(
            AttendancePunch.employee_id == emp_id,
            AttendancePunch.ts == ts,
            AttendancePunch.kind == kind,
        )
    ).first()
    if dup:
        return _fail("Duplicate punch", 409)

    p = AttendancePunch(employee_id=emp_id, ts=ts, kind=kind)
    if hasattr(AttendancePunch, "source"):
        p.source = "self"
    if hasattr(AttendancePunch, "note"):
        p.note = note

    db.session.add(p)
    db.session.commit()

    _recompute_after(emp_id, ts.date())
    return _ok(_row(p), 201)
