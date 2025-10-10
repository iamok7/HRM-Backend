# hrms_api/blueprints/attendance_missed_punch.py
from __future__ import annotations
from datetime import datetime, timedelta, date
from typing import Optional, Any

from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from sqlalchemy import and_, asc, desc

from hrms_api.extensions import db
from hrms_api.models.employee import Employee
from hrms_api.models.attendance_punch import AttendancePunch

# ✅ Correct model path for your codebase
from hrms_api.models.attendance_missed import MissedPunchRequest

# ✅ Use your engine helpers
from hrms_api.services.attendance_engine import upsert_manual_punch, recompute_daily

# permissions (fallbacks keep endpoints usable if perms not seeded yet)
try:
    from hrms_api.common.auth import requires_perms, requires_roles
except Exception:
    def requires_perms(_):
        def _wrap(fn): return fn
        return _wrap
    def requires_roles(_):
        def _wrap(fn): return fn
        return _wrap

bp = Blueprint("attendance_missed_punch", __name__, url_prefix="/api/v1/attendance/missed-punch")

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
_DT_FORMATS = ("%Y-%m-%d %H:%M:%S","%Y-%m-%d %H:%M","%Y-%m-%dT%H:%M:%S","%Y-%m-%dT%H:%M")

def _parse_ts(s: str | None):
    if not s: return None
    s = s.strip()
    try: return datetime.fromisoformat(s.replace(" ", "T"))
    except Exception: pass
    for fmt in _DT_FORMATS:
        try: return datetime.strptime(s, fmt)
        except Exception: pass
    return None

def _normalize_kind(v: Any) -> Optional[str]:
    if v is None: return None
    s = str(v).strip().lower()
    return s if s in ("in","out") else None

def _as_int(val, field) -> Optional[int]:
    if val in (None, "", "null"): return None
    try: return int(val)
    except Exception: raise ValueError(f"{field} must be integer")

def _as_bool(val, field):
    if val is None: return None
    v = str(val).lower()
    if v in ("true","1","yes"):  return True
    if v in ("false","0","no"):  return False
    raise ValueError(f"{field} must be true/false")

def _page_size():
    try: page = max(int(request.args.get("page", 1)), 1)
    except Exception: page = 1
    raw = request.args.get("size", request.args.get("limit", 20))
    try: size = max(min(int(raw), 100), 1)
    except Exception: size = 20
    return page, size

def _row(m: MissedPunchRequest):
    return {
        "id": m.id,
        "employee_id": m.employee_id,
        "ts": m.ts.isoformat() if m.ts else None,
        "kind": m.kind,
        "note": getattr(m, "note", None),
        "status": m.status,  # pending|approved|rejected|cancelled
        "created_at": getattr(m, "created_at", None).isoformat() if getattr(m, "created_at", None) else None,
        # align with your schema field names
        "approved_by": getattr(m, "approved_by", None),
        "approved_at": getattr(m, "approved_at", None).isoformat() if getattr(m, "approved_at", None) else None,
        "approver_note": getattr(m, "approver_note", None),
    }

def _get_emp_from_jwt() -> Optional[int]:
    ident = get_jwt_identity()
    if isinstance(ident, int): return ident
    if isinstance(ident, dict):
        for k in ("employee_id", "emp_id"):
            if ident.get(k) is not None:
                try: return int(ident[k])
                except Exception: pass
        uid = ident.get("user_id") or ident.get("id")
        if uid:
            try:
                emp = Employee.query.filter_by(user_id=int(uid)).first()
                return emp.id if emp else None
            except Exception: pass
    return None

# ---------- routes ----------

@bp.get("")
@jwt_required()
@requires_perms("attendance.missed.read")
def list_requests():
    """
    GET /api/v1/attendance/missed-punch
      ?employee_id|employeeId
      &status=pending|approved|rejected|cancelled
      &from=YYYY-MM-DD&to=YYYY-MM-DD
      &q=note-text
      &self=1 (only my requests)
      &page=1&size=20&sort=-created_at
    """
    q = MissedPunchRequest.query

    me_only = str(request.args.get("self", "0")).lower() in ("1","true","yes")
    if me_only:
        my_emp = _get_emp_from_jwt()
        if not my_emp: return _fail("Employee context not found on token", 401)
        q = q.filter(MissedPunchRequest.employee_id == my_emp)
    else:
        try:
            eid = _as_int(request.args.get("employee_id") or request.args.get("employeeId"), "employee_id")
        except ValueError as ex:
            return _fail(str(ex), 422)
        if eid: q = q.filter(MissedPunchRequest.employee_id == eid)

    st = (request.args.get("status") or "").strip().lower()
    if st:
        if st not in ("pending","approved","rejected","cancelled"):
            return _fail("invalid status", 422)
        q = q.filter(MissedPunchRequest.status == st)

    dfrom = request.args.get("from"); dto = request.args.get("to")
    if dfrom:
        try: q = q.filter(MissedPunchRequest.ts >= datetime.strptime(dfrom, "%Y-%m-%d"))
        except Exception: return _fail("from must be YYYY-MM-DD", 422)
    if dto:
        try:
            end = datetime.strptime(dto, "%Y-%m-%d") + timedelta(days=1) - timedelta(seconds=1)
            q = q.filter(MissedPunchRequest.ts <= end)
        except Exception: return _fail("to must be YYYY-MM-DD", 422)

    s = (request.args.get("q") or "").strip()
    if s and hasattr(MissedPunchRequest, "note"):
        q = q.filter(MissedPunchRequest.note.ilike(f"%{s}%"))

    sort = (request.args.get("sort") or "-created_at").split(",")
    for part in [p.strip() for p in sort if p.strip()]:
        asc_order = not part.startswith("-")
        key = part[1:] if part.startswith("-") else part
        if key == "created_at" and hasattr(MissedPunchRequest, "created_at"):
            col = MissedPunchRequest.created_at
        elif key == "ts": col = MissedPunchRequest.ts
        elif key == "status": col = MissedPunchRequest.status
        else: continue
        q = q.order_by(asc(col) if asc_order else desc(col))

    page, size = _page_size()
    total = q.count()
    items = q.offset((page - 1) * size).limit(size).all()
    return _ok([_row(i) for i in items], page=page, size=size, total=total)

@bp.post("")
@jwt_required()
@requires_perms("attendance.missed.create")
def create_request():
    """
    Body: { "ts": "YYYY-MM-DD HH:MM[:SS]" or ISO, "kind": "in|out", "note": "optional" }
    """
    emp_id = _get_emp_from_jwt()
    if not emp_id: return _fail("Employee context not found on token", 401)
    if not Employee.query.get(emp_id): return _fail("Employee not found", 404)

    d = request.get_json(silent=True, force=True) or {}
    ts = _parse_ts(d.get("ts"))
    kind = _normalize_kind(d.get("kind"))
    note = (d.get("note") or "").strip() or None
    if not (ts and kind): return _fail("ts and kind are required", 422)

    if ts.date() < date.today() - timedelta(days=7):
        return _fail("Backdate window exceeded (7 days)", 422)
    if ts > datetime.now() + timedelta(minutes=5):
        return _fail("Future timestamp not allowed", 422)

    exists = AttendancePunch.query.filter(
        and_(AttendancePunch.employee_id == emp_id, AttendancePunch.ts == ts, AttendancePunch.kind == kind)
    ).first()
    if exists: return _fail("Punch already exists at the same timestamp", 409)

    r = MissedPunchRequest(employee_id=emp_id, ts=ts, kind=kind, note=note, status="pending")
    db.session.add(r); db.session.commit()
    return _ok(_row(r), 201)

@bp.post("/<int:req_id>/cancel")
@jwt_required()
@requires_perms("attendance.missed.create")
def cancel_request(req_id: int):
    emp_id = _get_emp_from_jwt()
    if not emp_id: return _fail("Employee context not found on token", 401)

    r = MissedPunchRequest.query.get(req_id)
    if not r: return _fail("Request not found", 404)
    if r.employee_id != emp_id: return _fail("Not allowed", 403)
    if r.status != "pending": return _fail("Only pending requests can be cancelled", 409)

    r.status = "cancelled"
    r.approved_by = emp_id  # requester
    r.approved_at = datetime.utcnow()
    db.session.commit()
    return _ok(_row(r))

@bp.post("/<int:req_id>/approve")
@jwt_required()
@requires_perms("attendance.missed.approve")
@requires_roles("manager")
def approve_request(req_id: int):
    """
    On approve:
      - upsert punch via engine helper
      - mark request 'approved' with approver fields
      - recompute the daily summary
    """
    r = MissedPunchRequest.query.get(req_id)
    if not r: return _fail("Request not found", 404)
    if r.status != "pending": return _fail("Only pending requests can be approved", 409)

    emp = Employee.query.get(r.employee_id)
    if not emp: return _fail("Employee not found", 404)

    # engine upsert (handles duplicate safely)
    upsert_manual_punch(emp_id=r.employee_id, ts=r.ts, kind=r.kind, source="missed", note=r.note)

    r.status = "approved"
    r.approved_at = datetime.utcnow()
    approver = _get_emp_from_jwt()
    if approver: r.approved_by = approver
    db.session.commit()

    recompute_daily(r.employee_id, r.ts.date())
    return _ok(_row(r))

@bp.post("/<int:req_id>/reject")
@jwt_required()
@requires_perms("attendance.missed.approve")
@requires_roles("manager")
def reject_request(req_id: int):
    d = request.get_json(silent=True, force=True) or {}
    note = (d.get("reason") or d.get("note") or "").strip() or None

    r = MissedPunchRequest.query.get(req_id)
    if not r: return _fail("Request not found", 404)
    if r.status != "pending": return _fail("Only pending requests can be rejected", 409)

    r.status = "rejected"
    r.approved_at = datetime.utcnow()
    approver = _get_emp_from_jwt()
    if approver: r.approved_by = approver
    if hasattr(r, "approver_note"): r.approver_note = note
    db.session.commit()
    return _ok(_row(r))
