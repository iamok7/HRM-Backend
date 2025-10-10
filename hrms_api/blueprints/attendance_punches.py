# hrms_api/blueprints/attendance_punches.py
from __future__ import annotations
from datetime import datetime, date
from typing import Optional, List, Dict, Any

from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required
from sqlalchemy import and_, asc, desc, or_
from sqlalchemy.exc import SQLAlchemyError

from hrms_api.extensions import db
from hrms_api.models.employee import Employee
from hrms_api.models.attendance_punch import AttendancePunch

# permissions – prefer perms, fall back to role if needed
try:
    from hrms_api.common.auth import requires_perms
except Exception:
    def requires_perms(_):  # noop fallback
        def _wrap(fn): return fn
        return _wrap

try:
    from hrms_api.common.auth import requires_roles
except Exception:
    def requires_roles(_):
        def _wrap(fn): return fn
        return _wrap

bp = Blueprint("attendance_punches", __name__, url_prefix="/api/v1/attendance/punches")

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
    # prefer ISO
    try:
        return datetime.fromisoformat(s.replace(" ", "T"))
    except Exception:
        pass
    for fmt in _DT_FORMATS:
        try: return datetime.strptime(s, fmt)
        except Exception: pass
    return None

def _as_int(val, field) -> Optional[int]:
    if val in (None, "", "null"): return None
    try:
        return int(val)
    except Exception:
        raise ValueError(f"{field} must be integer")

def _as_bool(val, field):
    if val is None: return None
    v = str(val).lower()
    if v in ("true","1","yes"):  return True
    if v in ("false","0","no"):  return False
    raise ValueError(f"{field} must be true/false")

def _normalize_kind(v: Any) -> Optional[str]:
    if v is None: return None
    s = str(v).strip().lower()
    if s in ("in", "out"): return s
    return None

def _page_size():
    try:
        page = max(int(request.args.get("page", 1)), 1)
    except Exception:
        page = 1
    raw = request.args.get("size", request.args.get("limit", 20))
    try:
        size = max(min(int(raw), 100), 1)
    except Exception:
        size = 20
    return page, size

def _recompute_after(emp_id: int, day: date):
    try:
        from hrms_api.services.attendance_engine import recompute_daily
    except Exception:
        recompute_daily = None
    if recompute_daily:
        try:
            recompute_daily(emp_id, day)
        except Exception:
            # don't fail the API because recompute failed
            pass

def _row(p: AttendancePunch):
    return {
        "id": p.id,
        "employee_id": p.employee_id,
        "ts": p.ts.isoformat() if p.ts else None,
        "kind": p.kind,
        "source": getattr(p, "source", None),
        "note": getattr(p, "note", None),
        "is_active": getattr(p, "is_active", True),
        "created_at": getattr(p, "created_at", None).isoformat() if getattr(p, "created_at", None) else None,
        "updated_at": getattr(p, "updated_at", None).isoformat() if getattr(p, "updated_at", None) else None,
    }

# ---------- routes ----------

@bp.get("")
@jwt_required()
@requires_perms("attendance.punch.read")
def list_punches():
    """
    GET /api/v1/attendance/punches
      ?employee_id|employeeId
      &from|date_from=YYYY-MM-DD
      &to|date_to=YYYY-MM-DD
      &kind=in|out
      &source=device|manual|*
      &q=note-text
      &is_active=true|false   (if column exists)
      &page=1&size=20&sort=ts,-created_at
    """
    q = AttendancePunch.query

    # filters
    try:
        eid = _as_int(request.args.get("employee_id") or request.args.get("employeeId"), "employee_id")
    except ValueError as ex:
        return _fail(str(ex), 422)
    if eid:
        q = q.filter(AttendancePunch.employee_id == eid)

    dfrom = request.args.get("from") or request.args.get("date_from")
    dto   = request.args.get("to")   or request.args.get("date_to")
    df = datetime.strptime(dfrom, "%Y-%m-%d").date() if dfrom else None
    dt = datetime.strptime(dto, "%Y-%m-%d").date()   if dto   else None
    if df:
        q = q.filter(AttendancePunch.ts >= datetime.combine(df, datetime.min.time()))
    if dt:
        q = q.filter(AttendancePunch.ts <= datetime.combine(dt, datetime.max.time()))

    k = _normalize_kind(request.args.get("kind"))
    if k:
        q = q.filter(AttendancePunch.kind == k)

    src = (request.args.get("source") or "").strip().lower()
    if src:
        if hasattr(AttendancePunch, "source"):
            q = q.filter(AttendancePunch.source == src)

    s = (request.args.get("q") or "").strip()
    if s and hasattr(AttendancePunch, "note"):
        like = f"%{s}%"
        q = q.filter(AttendancePunch.note.ilike(like))

    # is_active flag (if present)
    if "is_active" in request.args and hasattr(AttendancePunch, "is_active"):
        try:
            b = _as_bool(request.args.get("is_active"), "is_active")
        except ValueError as ex:
            return _fail(str(ex), 422)
        if b is True:
            q = q.filter(AttendancePunch.is_active.is_(True))
        elif b is False:
            q = q.filter(AttendancePunch.is_active.is_(False))

    # sorting
    allowed = {
        "id": AttendancePunch.id,
        "ts": AttendancePunch.ts,
        "created_at": getattr(AttendancePunch, "created_at", AttendancePunch.id),
        "updated_at": getattr(AttendancePunch, "updated_at", AttendancePunch.id),
        "employee_id": AttendancePunch.employee_id,
        "kind": AttendancePunch.kind,
    }
    raw_sort = (request.args.get("sort") or "").strip()
    if raw_sort:
        for part in [p.strip() for p in raw_sort.split(",") if p.strip()]:
            asc_order = True
            key = part
            if part.startswith("-"):
                asc_order = False
                key = part[1:]
            col = allowed.get(key)
            if col is not None:
                q = q.order_by(asc(col) if asc_order else desc(col))
    else:
        q = q.order_by(desc(AttendancePunch.ts))

    # paging
    page, size = _page_size()
    total = q.count()
    items = q.offset((page - 1) * size).limit(size).all()
    return _ok([_row(i) for i in items], page=page, size=size, total=total)

@bp.get("/<int:punch_id>")
@jwt_required()
@requires_perms("attendance.punch.read")
def get_punch(punch_id: int):
    p = AttendancePunch.query.get(punch_id)
    if not p: return _fail("Punch not found", 404)
    return _ok(_row(p))

@bp.post("")
@jwt_required()
@requires_perms("attendance.punch.create")
def create_punch():
    """
    Body:
    {
      "employee_id": 123,            // or "123" (coerced)
      "ts": "2025-10-10 09:00",      // or ISO 8601
      "kind": "in" | "out",
      "source": "manual",            // optional
      "note": "late entry"           // optional
    }
    """
    d = request.get_json(silent=True, force=True) or {}
    try:
        emp_id = _as_int(d.get("employee_id"), "employee_id")
    except ValueError as ex:
        return _fail(str(ex), 422)

    ts = _parse_ts(d.get("ts"))
    kind = _normalize_kind(d.get("kind"))
    source = (d.get("source") or "manual").strip().lower()
    note = (d.get("note") or "").strip() or None

    if not (emp_id and ts and kind):
        return _fail("employee_id, ts and kind are required", 422)

    if not Employee.query.get(emp_id):
        return _fail("Employee not found", 404)

    # duplicate check (same as import)
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
    if hasattr(AttendancePunch, "source"): p.source = source
    if hasattr(AttendancePunch, "note"):   p.note = note

    try:
        db.session.add(p)
        db.session.commit()
    except SQLAlchemyError as e:
        db.session.rollback()
        return _fail("Database error while creating punch", 500, detail=str(e))

    # recompute for that day
    _recompute_after(emp_id, ts.date())

    return _ok(_row(p), 201)

@bp.delete("/<int:punch_id>")
@jwt_required()
@requires_perms("attendance.punch.delete")
def delete_punch(punch_id: int):
    p = AttendancePunch.query.get(punch_id)
    if not p: return _fail("Punch not found", 404)

    the_day = p.ts.date() if p.ts else None
    emp_id = p.employee_id

    # soft delete if supported; otherwise hard delete
    if hasattr(AttendancePunch, "is_active"):
        p.is_active = False
        try:
            db.session.commit()
        except SQLAlchemyError as e:
            db.session.rollback()
            return _fail("Database error while deleting punch", 500, detail=str(e))
    else:
        try:
            db.session.delete(p)
            db.session.commit()
        except SQLAlchemyError as e:
            db.session.rollback()
            return _fail("Database error while deleting punch", 500, detail=str(e))

    if emp_id and the_day:
        _recompute_after(emp_id, the_day)

    return _ok({"id": punch_id, "deleted": True})
