# hrms_api/blueprints/attendance_self_punch.py
from __future__ import annotations

from datetime import datetime, date, timedelta
from math import radians, sin, cos, asin, sqrt
from typing import Optional, Any

from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from sqlalchemy import and_, desc

from hrms_api.extensions import db
from hrms_api.models.employee import Employee
from hrms_api.models.attendance_punch import AttendancePunch
from hrms_api.models.master import Location

# Optional permissions: if not present, endpoints still work with JWT only.
try:
    from hrms_api.common.auth import requires_perms
except Exception:
    def requires_perms(_):  # noop fallback
        def _wrap(fn): return fn
        return _wrap


bp = Blueprint(
    "attendance_self_punch",
    __name__,
    url_prefix="/api/v1/attendance/self-punches",
)

# ---- settings (tune as needed) ----
MAX_SELF_PUNCHES_PER_DAY = 6        # prevent spam
SELF_PUNCH_BACKDATE_DAYS = 3        # how far back a user can self-punch
DEFAULT_GEOFENCE_M = 500            # if location.geo_radius_m is null


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
    if detail is not None:
        err["detail"] = detail
    return jsonify({"success": False, "error": err}), status


# ---------- helpers ----------
_DT_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M",
)


def _parse_ts(s: str | None) -> Optional[datetime]:
    if not s:
        return None
    s = s.strip()
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


def _normalize_direction(v: Any) -> Optional[str]:
    """
    Accepts: "in"/"out", "IN"/"OUT", 1/0 and some synonyms.
    Delegates to AttendancePunch.normalize_direction where possible.
    """
    if v is None:
        return None
    try:
        # prefer model helper if present
        return AttendancePunch.normalize_direction(v)
    except Exception:
        s = str(v).strip().lower()
        if s in ("in", "1", "i", "enter", "entry"):
            return "in"
        if s in ("out", "0", "o", "exit", "leave"):
            return "out"
        return None


def _get_employee_id_from_jwt() -> Optional[int]:
    """
    Try common identity shapes:
      - int employee_id directly
      - {"employee_id": 123, ...}
      - {"emp_id": 123, ...}
      - {"user_id": 7, ...} with Employee.user_id relation (fallback)
    """
    ident = get_jwt_identity()

    # direct int
    if isinstance(ident, int):
        return ident

    # dict-like
    if isinstance(ident, dict):
        if ident.get("employee_id"):
            try:
                return int(ident["employee_id"])
            except Exception:
                pass
        if ident.get("emp_id"):
            try:
                return int(ident["emp_id"])
            except Exception:
                pass

        # optional fallback via user_id -> employee
        uid = ident.get("user_id") or ident.get("id")
        if uid:
            try:
                emp = Employee.query.filter_by(user_id=int(uid)).first()
                if emp:
                    return emp.id
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
            # best-effort; don't break API
            pass


def _row(p: AttendancePunch):
    return {
        "id": p.id,
        "employee_id": p.employee_id,
        "company_id": p.company_id,
        "ts": p.ts.isoformat() if p.ts else None,
        "direction": p.direction,
        "method": p.method,
        "device_id": p.device_id,
        "lat": float(p.lat) if p.lat is not None else None,
        "lon": float(p.lon) if p.lon is not None else None,
        "accuracy_m": float(p.accuracy_m) if p.accuracy_m is not None else None,
        "photo_url": p.photo_url,
        "face_score": float(p.face_score) if p.face_score is not None else None,
        "note": getattr(p, "note", None),
        "source_meta": getattr(p, "source_meta", None),
        "created_at": p.created_at.isoformat() if getattr(p, "created_at", None) else None,
        "updated_at": p.updated_at.isoformat() if getattr(p, "updated_at", None) else None,
    }


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Distance between two lat/lon pairs in meters.
    """
    # convert decimal degrees to radians
    rlat1, rlon1, rlat2, rlon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat = rlat2 - rlat1
    dlon = rlon2 - rlon1

    a = sin(dlat / 2) ** 2 + cos(rlat1) * cos(rlat2) * sin(dlon / 2) ** 2
    c = 2 * asin(sqrt(a))
    earth_radius_m = 6371000.0
    return earth_radius_m * c


# ---------- routes ----------


@bp.get("")
@jwt_required()
@requires_perms("attendance.self.read")
def list_my_punches():
    """
    GET /api/v1/attendance/self-punches?from=&to=&direction=&page=&size=

    Query params:
      from / date_from : YYYY-MM-DD  (inclusive)
      to   / date_to   : YYYY-MM-DD  (inclusive)
      direction        : in | out (also accepts 'kind' for backward compat)
      page             : default 1
      size / limit     : default 20, max 100
    """
    emp_id = _get_employee_id_from_jwt()
    if not emp_id:
        return _fail("Employee context not found on token", 401)

    emp = Employee.query.get(emp_id)
    if not emp:
        return _fail("Employee not found", 404)

    dfrom = request.args.get("from") or request.args.get("date_from")
    dto = request.args.get("to") or request.args.get("date_to")
    df = datetime.strptime(dfrom, "%Y-%m-%d").date() if dfrom else None
    dt = datetime.strptime(dto, "%Y-%m-%d").date() if dto else None

    direction = (
        _normalize_direction(request.args.get("direction"))
        or _normalize_direction(request.args.get("kind"))
    )

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
        q = q.filter(
            AttendancePunch.ts >= datetime.combine(df, datetime.min.time())
        )
    if dt:
        q = q.filter(
            AttendancePunch.ts <= datetime.combine(dt, datetime.max.time())
        )
    if direction:
        q = q.filter(AttendancePunch.direction == direction)

    q = q.order_by(desc(AttendancePunch.ts))

    total = q.count()
    items = q.offset((page - 1) * size).limit(size).all()
    return _ok([_row(i) for i in items], page=page, size=size, total=total)


@bp.post("")
@jwt_required()
@requires_perms("attendance.self.create")
def create_my_punch():
    """
    POST /api/v1/attendance/self-punches

    Self / face attendance punch for the logged-in employee.

    Request JSON:
    {
      "direction": "in" | "out",            // or: "kind": "in" | "out" (legacy)
      "ts": "2025-11-12T09:15:00",          // optional; defaults to now (server time)
      "lat": 18.582123,                     // optional but recommended for geofence
      "lon": 73.738456,                     // optional but recommended
      "accuracy_m": 15.5,                   // optional; GPS accuracy in meters
      "photo_url": "https://.../selfie.jpg",// optional; future face-match uses this
      "location_id": 1,                     // optional; override employee.location_id
      "note": "Reached office gate"         // optional
    }

    Response JSON (201):
    {
      "success": true,
      "data": {
        "id": ...,
        "employee_id": ...,
        "company_id": ...,
        "ts": "...",
        "direction": "in",
        "method": "selfie",
        "lat": 18.582123,
        "lon": 73.738456,
        "accuracy_m": 15.5,
        "photo_url": "...",
        "face_score": null,
        "note": "...",
        "source_meta": {
          "location_id": 1,
          "distance_m": 120.3,
          "radius_m": 500
        },
        "created_at": "...",
        "updated_at": "..."
      }
    }

    Rules:
      - Daily cap (MAX_SELF_PUNCHES_PER_DAY, across all punches)
      - Backdate only up to SELF_PUNCH_BACKDATE_DAYS
      - No far-future timestamps (>5 min from 'now')
      - If location has geo config and lat/lon is provided:
            distance <= geo_radius_m (or DEFAULT_GEOFENCE_M)
    """
    emp_id = _get_employee_id_from_jwt()
    if not emp_id:
        return _fail("Employee context not found on token", 401)

    emp = Employee.query.get(emp_id)
    if not emp:
        return _fail("Employee not found", 404)

    payload = request.get_json(silent=True, force=True) or {}

    # timestamp
    ts = _parse_ts(payload.get("ts"))
    if ts is None:
        ts = datetime.utcnow()

    direction = (
        _normalize_direction(payload.get("direction"))
        or _normalize_direction(payload.get("kind"))
    )
    note = (payload.get("note") or "").strip() or None

    # geo + selfie metadata
    lat = payload.get("lat")
    lon = payload.get("lon")
    accuracy_m = payload.get("accuracy_m")
    photo_url = payload.get("photo_url") or None
    loc_id = payload.get("location_id")

    # basic validation
    if not direction:
        return _fail("direction (or kind) is required as 'in' or 'out'", 422)

    today = date.today()
    min_day = today - timedelta(days=SELF_PUNCH_BACKDATE_DAYS)
    if ts.date() < min_day:
        return _fail(
            f"Backdate window exceeded (max {SELF_PUNCH_BACKDATE_DAYS} days)", 422
        )

    if ts > datetime.now() + timedelta(minutes=5):
        return _fail("Future timestamp not allowed", 422)

    # daily cap (count all punches for the day; you can filter only method='selfie' if desired)
    day_start = datetime.combine(ts.date(), datetime.min.time())
    day_end = datetime.combine(ts.date(), datetime.max.time())
    day_count = AttendancePunch.query.filter(
        AttendancePunch.employee_id == emp_id,
        AttendancePunch.ts >= day_start,
        AttendancePunch.ts <= day_end,
    ).count()
    if day_count >= MAX_SELF_PUNCHES_PER_DAY:
        return _fail("Daily self-punch limit reached", 409)

    # duplicate protection
    dup = AttendancePunch.query.filter(
        and_(
            AttendancePunch.employee_id == emp_id,
            AttendancePunch.ts == ts,
            AttendancePunch.direction == direction,
        )
    ).first()
    if dup:
        return _fail("Duplicate punch", 409)

    # ---- geofence check (if geo + location is configured) ----
    distance_m = None
    radius_m = None

    # locate the reference Location
    loc_obj = None
    if loc_id:
        loc_obj = Location.query.filter_by(id=loc_id, company_id=emp.company_id).first()
    elif hasattr(emp, "location_id") and emp.location_id:
        loc_obj = Location.query.filter_by(
            id=emp.location_id, company_id=emp.company_id
        ).first()

    if lat is not None and lon is not None and loc_obj is not None:
        try:
            lat = float(lat)
            lon = float(lon)
        except Exception:
            return _fail("lat/lon must be numeric values", 422)

        if loc_obj.geo_lat is not None and loc_obj.geo_lon is not None:
            loc_lat = float(loc_obj.geo_lat)
            loc_lon = float(loc_obj.geo_lon)
            radius_m = int(loc_obj.geo_radius_m or DEFAULT_GEOFENCE_M)
        else:
            # location has no geo configured, fall back to default radius around given point
            loc_lat = lat
            loc_lon = lon
            radius_m = DEFAULT_GEOFENCE_M

        distance_m = _haversine_m(lat, lon, loc_lat, loc_lon)

        if distance_m > radius_m:
            return _fail(
                "Outside allowed attendance radius",
                422,
                detail={
                    "distance_m": round(distance_m, 2),
                    "radius_m": radius_m,
                    "location_id": loc_obj.id,
                },
            )

    # ---- create punch ----
    p = AttendancePunch(
        company_id=emp.company_id,
        employee_id=emp_id,
        ts=ts,
        direction=direction,
        method="selfie",
    )

    # geo / selfie metadata
    p.lat = float(lat) if lat is not None else None
    p.lon = float(lon) if lon is not None else None
    p.accuracy_m = float(accuracy_m) if accuracy_m is not None else None
    p.photo_url = photo_url
    p.note = note

    meta = {}
    if loc_obj is not None:
        meta["location_id"] = loc_obj.id
    if distance_m is not None:
        meta["distance_m"] = round(distance_m, 2)
    if radius_m is not None:
        meta["radius_m"] = radius_m
    if meta:
        p.source_meta = meta

    db.session.add(p)
    db.session.commit()

    _recompute_after(emp_id, ts.date())
    return _ok(_row(p), 201)
