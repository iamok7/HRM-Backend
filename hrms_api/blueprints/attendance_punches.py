from __future__ import annotations

from datetime import datetime, date
from typing import Optional, List, Dict, Any

from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required
from sqlalchemy import and_, asc, desc
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
_DT_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M",
)


def _parse_ts(s: str | None):
    if not s:
        return None
    s = s.strip()
    # prefer ISO
    try:
        return datetime.fromisoformat(s.replace(" ", "T"))
    except Exception:
        pass
    for fmt in _DT_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            pass
    return None


def _as_int(val, field) -> Optional[int]:
    if val in (None, "", "null"):
        return None
    try:
        return int(val)
    except Exception:
        raise ValueError(f"{field} must be integer")


def _as_bool(val, field):
    if val is None:
        return None
    v = str(val).lower()
    if v in ("true", "1", "yes"):
        return True
    if v in ("false", "0", "no"):
        return False
    raise ValueError(f"{field} must be true/false")


def _normalize_direction(v: Any) -> Optional[str]:
    """
    Normalize direction / kind: accepts 'in'/'out', 1/0, and synonyms.
    Delegates to AttendancePunch.normalize_direction where available.
    """
    if v is None:
        return None
    try:
        return AttendancePunch.normalize_direction(v)
    except Exception:
        s = str(v).strip().lower()
        if s in ("in", "1", "i", "enter", "entry"):
            return "in"
        if s in ("out", "0", "o", "exit", "leave"):
            return "out"
        return None


def _normalize_method(v: Any, default: str = "excel") -> str:
    """
    Map free-form method/source → canonical values:
      - 'machine' : device / reader / machine
      - 'excel'   : excel / sheet / xlsx / csv / manual admin entry
      - 'selfie'  : mobile / face / self
    """
    if v is None:
        return default
    s = str(v).strip().lower()
    if s in ("machine", "device", "reader", "punch", "biometric"):
        return "machine"
    if s in ("self", "selfie", "mobile", "face"):
        return "selfie"
    if s in ("excel", "sheet", "xlsx", "csv", "manual", "admin"):
        return "excel"
    return default


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
        "company_id": getattr(p, "company_id", None),
        "employee_id": p.employee_id,
        "ts": p.ts.isoformat() if p.ts else None,
        "direction": getattr(p, "direction", None),
        "method": getattr(p, "method", None),
        "device_id": getattr(p, "device_id", None),
        "lat": float(p.lat) if getattr(p, "lat", None) is not None else None,
        "lon": float(p.lon) if getattr(p, "lon", None) is not None else None,
        "accuracy_m": float(p.accuracy_m) if getattr(p, "accuracy_m", None) is not None else None,
        "photo_url": getattr(p, "photo_url", None),
        "face_score": float(p.face_score) if getattr(p, "face_score", None) is not None else None,
        "note": getattr(p, "note", None),
        "source_meta": getattr(p, "source_meta", None),
        "is_active": getattr(p, "is_active", True),
        "created_at": getattr(p, "created_at", None).isoformat()
        if getattr(p, "created_at", None)
        else None,
        "updated_at": getattr(p, "updated_at", None).isoformat()
        if getattr(p, "updated_at", None)
        else None,
    }


# ---------- routes ----------


@bp.get("")
@jwt_required()
@requires_perms("attendance.punch.read")
def list_punches():
    """
    GET /api/v1/attendance/punches
      ?company_id|companyId=1
      &employee_id|employeeId=123
      &from|date_from=YYYY-MM-DD
      &to|date_to=YYYY-MM-DD
      &direction=in|out          (also supports legacy 'kind')
      &method=machine|excel|selfie
      &q=note-text
      &is_active=true|false      (if column exists)
      &page=1&size=20
      &sort=ts,-created_at

    Example response:
    {
      "success": true,
      "data": [
        {
          "id": 1,
          "company_id": 1,
          "employee_id": 7,
          "ts": "2025-11-12T09:10:00",
          "direction": "in",
          "method": "machine",
          "device_id": "GATE-01",
          "lat": 18.582123,
          "lon": 73.738456,
          "accuracy_m": 12.5,
          "photo_url": null,
          "face_score": null,
          "note": "Entry gate",
          "source_meta": null,
          "is_active": true,
          "created_at": "...",
          "updated_at": "..."
        }
      ],
      "meta": {
        "page": 1,
        "size": 20,
        "total": 1
      }
    }
    """
    q = AttendancePunch.query

    # company filter (optional)
    try:
        cid = _as_int(
            request.args.get("company_id") or request.args.get("companyId"),
            "company_id",
        )
    except ValueError as ex:
        return _fail(str(ex), 422)
    if cid:
        q = q.filter(AttendancePunch.company_id == cid)

    # employee filter
    try:
        eid = _as_int(
            request.args.get("employee_id") or request.args.get("employeeId"),
            "employee_id",
        )
    except ValueError as ex:
        return _fail(str(ex), 422)
    if eid:
        q = q.filter(AttendancePunch.employee_id == eid)

    # date window
    dfrom = request.args.get("from") or request.args.get("date_from")
    dto = request.args.get("to") or request.args.get("date_to")
    df = datetime.strptime(dfrom, "%Y-%m-%d").date() if dfrom else None
    dt = datetime.strptime(dto, "%Y-%m-%d").date() if dto else None
    if df:
        q = q.filter(
            AttendancePunch.ts >= datetime.combine(df, datetime.min.time())
        )
    if dt:
        q = q.filter(
            AttendancePunch.ts <= datetime.combine(dt, datetime.max.time())
        )

    # direction filter (direction / kind)
    direction = _normalize_direction(
        request.args.get("direction") or request.args.get("kind")
    )
    if direction:
        q = q.filter(AttendancePunch.direction == direction)

    # method filter (supports 'method' and legacy 'source')
    method_raw = request.args.get("method") or request.args.get("source")
    if method_raw:
        method = _normalize_method(method_raw, default="machine")
        q = q.filter(AttendancePunch.method == method)

    # text search on note
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
        "direction": getattr(AttendancePunch, "direction", AttendancePunch.id),
        "method": getattr(AttendancePunch, "method", AttendancePunch.id),
        "company_id": getattr(AttendancePunch, "company_id", AttendancePunch.id),
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
    """
    GET /api/v1/attendance/punches/{id}

    Response:
    {
      "success": true,
      "data": {
        "id": ...,
        "company_id": ...,
        "employee_id": ...,
        "ts": "...",
        "direction": "in",
        "method": "machine",
        "device_id": "GATE-01",
        "lat": 18.5821,
        "lon": 73.7384,
        "accuracy_m": 12.5,
        "photo_url": null,
        "face_score": null,
        "note": "Entry gate",
        "source_meta": null,
        "is_active": true,
        "created_at": "...",
        "updated_at": "..."
      }
    }
    """
    p = AttendancePunch.query.get(punch_id)
    if not p:
        return _fail("Punch not found", 404)
    return _ok(_row(p))


@bp.post("")
@jwt_required()
@requires_perms("attendance.punch.create")
def create_punch():
    """
    POST /api/v1/attendance/punches

    Body:
    {
      "employee_id": 123,             // required
      "ts": "2025-10-10 09:00",       // required (or ISO 8601)
      "direction": "in",              // 'in' | 'out'  (also supports legacy 'kind')
      "method": "excel",              // optional; default "excel" (admin/manual)
      "device_id": "MANUAL-UI",       // optional
      "note": "late entry",           // optional
      "lat": 18.5821,                 // optional
      "lon": 73.7384,                 // optional
      "accuracy_m": 10.0              // optional
    }

    Success (201):
    {
      "success": true,
      "data": {
        "id": ...,
        "company_id": ...,
        "employee_id": ...,
        "ts": "...",
        "direction": "in",
        "method": "excel",
        "device_id": "MANUAL-UI",
        "lat": 18.5821,
        "lon": 73.7384,
        "accuracy_m": 10.0,
        "photo_url": null,
        "face_score": null,
        "note": "late entry",
        "source_meta": null,
        "is_active": true,
        "created_at": "...",
        "updated_at": "..."
      }
    }
    """
    d = request.get_json(silent=True, force=True) or {}

    # employee
    try:
        emp_id = _as_int(d.get("employee_id"), "employee_id")
    except ValueError as ex:
        return _fail(str(ex), 422)

    ts = _parse_ts(d.get("ts"))
    direction = _normalize_direction(d.get("direction") or d.get("kind"))
    method = _normalize_method(d.get("method") or d.get("source"), default="excel")
    device_id = (d.get("device_id") or d.get("device") or "").strip() or None
    note = (d.get("note") or "").strip() or None

    # geo optional
    lat = d.get("lat")
    lon = d.get("lon")
    accuracy = d.get("accuracy_m")

    if not (emp_id and ts and direction):
        return _fail("employee_id, ts and direction are required", 422)

    emp = Employee.query.get(emp_id)
    if not emp:
        return _fail("Employee not found", 404)

    # duplicate check (same as import)
    dup = AttendancePunch.query.filter(
        and_(
            AttendancePunch.employee_id == emp_id,
            AttendancePunch.ts == ts,
            AttendancePunch.direction == direction,
        )
    ).first()
    if dup:
        return _fail("Duplicate punch", 409)

    # coerce geo fields
    try:
        lat_f = float(lat) if lat not in (None, "", "null") else None
    except Exception:
        lat_f = None
    try:
        lon_f = float(lon) if lon not in (None, "", "null") else None
    except Exception:
        lon_f = None
    try:
        acc_f = float(accuracy) if accuracy not in (None, "", "null") else None
    except Exception:
        acc_f = None

    p = AttendancePunch(
        company_id=emp.company_id,
        employee_id=emp_id,
        ts=ts,
        direction=direction,
        method=method,
        device_id=device_id,
    )

    p.note = note
    p.lat = lat_f
    p.lon = lon_f
    p.accuracy_m = acc_f

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
    """
    DELETE /api/v1/attendance/punches/{id}

    Response:
    {
      "success": true,
      "data": {
        "id": 123,
        "deleted": true
      }
    }
    """
    p = AttendancePunch.query.get(punch_id)
    if not p:
        return _fail("Punch not found", 404)

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
