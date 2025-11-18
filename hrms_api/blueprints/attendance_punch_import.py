from __future__ import annotations

import csv
import io
import logging
from collections import defaultdict
from datetime import datetime, date
from typing import List, Dict, Any, Tuple, Optional

from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required
from sqlalchemy import and_
from sqlalchemy.exc import SQLAlchemyError

from hrms_api.extensions import db
from hrms_api.models.employee import Employee
from hrms_api.models.attendance_punch import AttendancePunch

log = logging.getLogger(__name__)

# ---- Guard: prefer DB-backed permission, fallback to role, else JWT only ----
_guard_perm = None
try:
    from hrms_api.common.auth import requires_perms

    _guard_perm = requires_perms("attendance.punch.import")
except Exception:
    _guard_perm = None

try:
    from hrms_api.common.auth import requires_roles

    _guard_role = requires_roles("admin")  # optional fallback
except Exception:

    def _guard_role(fn):
        return fn


def _GUARD(fn):
    # prefer permission guard if available else role guard else jwt only
    if _guard_perm:
        return _guard_perm(fn)
    return _guard_role(fn)


bp = Blueprint(
    "attendance_punch_import",
    __name__,
    url_prefix="/api/v1/attendance/punches",
)

# ---------- envelopes ----------


def _ok(data=None, status=200):
    return jsonify({"success": True, "data": data}), status


def _fail(msg, status=400, errors: List[Dict[str, Any]] | None = None):
    payload = {"success": False, "error": {"message": msg}}
    if errors:
        payload["error"]["rows"] = errors
    return jsonify(payload), status


# ---------- parsing helpers ----------
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
    # try ISO first
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


def _normalize_direction(v: Any) -> Optional[str]:
    if v is None:
        return None
    try:
        # delegate to model helper if present
        return AttendancePunch.normalize_direction(v)
    except Exception:
        s = str(v).strip().lower()
        if s in ("in", "1", "i"):
            return "in"
        if s in ("out", "0", "o"):
            return "out"
        return None


def _normalize_method(v: Any, default: str = "machine") -> str:
    """
    Map various 'source' / 'method' strings to our canonical:
      - 'machine' : device / reader / machine
      - 'excel'   : excel / sheet / xlsx / csv / backfill
      - 'selfie'  : reserved for other paths (not used here normally)
    """
    if v is None:
        return default
    s = str(v).strip().lower()
    if s in ("machine", "device", "reader", "punch", "biometric"):
        return "machine"
    if s in ("excel", "sheet", "xlsx", "csv", "backfill", "manual"):
        return "excel"
    if s in ("self", "selfie", "mobile", "face"):
        return "selfie"
    return default


def _get_rows_from_request() -> Tuple[List[Dict[str, Any]], str]:
    """
    Returns (rows, source) where source is 'csv' or 'json'.

    CSV:
      - multipart/form-data, field name 'file'
      - first row is header, remaining rows are data

    JSON:
      - { "rows": [ { ... }, ... ] }
    """
    ctype = (request.content_type or "")
    if "multipart/form-data" in ctype:
        f = request.files.get("file")
        if not f:
            return [], "csv"
        text = f.read().decode("utf-8-sig", errors="ignore")
        reader = csv.DictReader(io.StringIO(text))
        rows = [dict(r) for r in reader]
        return rows, "csv"
    else:
        data = request.get_json(silent=True) or {}
        rows = data.get("rows") or []
        if not isinstance(rows, list):
            rows = []
        return rows, "json"


# ---------- employee code → id mapping (fast, done once per call) ----------
def _code_columns() -> List[str]:
    # check common code columns on Employee (ordered by preference)
    cols = []
    for c in ("code", "emp_code", "employee_code"):
        if hasattr(Employee, c):
            cols.append(c)
    return cols


def _make_code_map() -> dict[str, int]:
    cols = _code_columns()
    if not cols:
        return {}
    primary = cols[0]
    data = db.session.query(Employee.id, getattr(Employee, primary)).all()
    mapping: dict[str, int] = {}
    for eid, code in data:
        if code:
            mapping[str(code).strip()] = eid
    return mapping


# ---------- recompute hook (safe no-op if service missing) ----------
def _recompute_after_commit(inserted: List[AttendancePunch]):
    by_emp: dict[int, set[date]] = defaultdict(set)
    for p in inserted:
        try:
            by_emp[p.employee_id].add(p.ts.date())
        except Exception:
            continue
    if not by_emp:
        return
    try:
        from hrms_api.services.attendance_engine import recompute_daily
    except Exception:
        recompute_daily = None
    if not recompute_daily:
        log.info("[punch-import] recompute skipped (engine not available)")
        return
    for emp_id, days in by_emp.items():
        for d in days:
            try:
                recompute_daily(emp_id, d)
            except Exception as e:
                log.exception(
                    "[punch-import] recompute failed emp=%s day=%s: %s",
                    emp_id,
                    d,
                    e,
                )


# ---------- normalization per row ----------
def _normalize_row(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Map a raw CSV/JSON row into a normalized dict.
    Supported aliases:

      employee_id:  employee_id | employeeId
      employee_code: employee_code | code | emp_code

      timestamp: ts | timestamp | time | punch_time

      direction: direction | kind | inout
                 (values: 'in'/'out', 1/0, etc.)

      method/source (optional):
           method | source

      device_id (optional):
           device_id | device | reader_id

      geo (optional):
           lat | latitude
           lon | longitude
           accuracy_m | accuracy | gps_accuracy
    """
    # employee
    emp_id = raw.get("employee_id") or raw.get("employeeId")
    emp_code = (
        raw.get("employee_code")
        or raw.get("code")
        or raw.get("emp_code")
    )

    # timestamp
    ts = (
        raw.get("ts")
        or raw.get("timestamp")
        or raw.get("time")
        or raw.get("punch_time")
    )

    # direction / kind
    dir_raw = (
        raw.get("direction")
        or raw.get("kind")
        or raw.get("inout")
    )

    # provenance & extras
    method_raw = raw.get("method")
    source_raw = raw.get("source")
    device_id = (
        raw.get("device_id")
        or raw.get("device")
        or raw.get("reader_id")
    )
    lat = raw.get("lat") or raw.get("latitude")
    lon = raw.get("lon") or raw.get("longitude")
    accuracy = (
        raw.get("accuracy_m")
        or raw.get("accuracy")
        or raw.get("gps_accuracy")
    )
    note = raw.get("note")

    # coerce & clean
    try:
        emp_id = _as_int(emp_id, "employee_id")
    except ValueError:
        emp_id = None

    ts_dt = _parse_ts(ts)
    direction = _normalize_direction(dir_raw)
    method = _normalize_method(method_raw or source_raw, default="machine")

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

    return {
        "employee_id": emp_id,
        "employee_code": str(emp_code).strip() if emp_code is not None else None,
        "ts": ts_dt,
        "direction": direction,
        "method": method,
        "device_id": str(device_id).strip() if device_id else None,
        "lat": lat_f,
        "lon": lon_f,
        "accuracy_m": acc_f,
        "note": (str(note).strip() if note else None),
        "raw": raw,
    }


# ---------- endpoint ----------
@bp.post("/import")
@jwt_required()
@_GUARD
def import_punches():
    """
    POST /api/v1/attendance/punches/import
        ?mode=dry|commit
        &on_duplicate=skip|error
        &recompute=1|0

    Accepts:

    1) CSV (multipart/form-data):
        - field name: "file"
        - header columns (aliases allowed):
            employee_id | employeeId
            employee_code | code | emp_code
            ts | timestamp | time | punch_time
            direction | kind | inout
            method | source              (optional; "machine"/"excel")
            device_id | device           (optional)
            lat | latitude               (optional)
            lon | longitude              (optional)
            accuracy_m | accuracy        (optional)
            note                         (optional)

    2) JSON body:
    {
      "rows": [
        {
          "employee_code": "E-0001",
          "ts": "2025-11-12T09:10:00",
          "direction": "in",
          "method": "machine",
          "device_id": "GATE-01",
          "lat": 18.5821,
          "lon": 73.7384,
          "accuracy_m": 12.5,
          "note": "Entry gate"
        }
      ]
    }

    Response (dry-run example):
    {
      "success": true,
      "data": {
        "mode": "dry",
        "source": "csv",
        "processed": 120,
        "valid": 118,
        "duplicates_skipped": 2,
        "errors_count": 0,
        "errors": []
      }
    }
    """
    mode = (request.args.get("mode") or "dry").lower()  # dry | commit
    on_dup = (request.args.get("on_duplicate") or "skip").lower()  # skip | error
    want_recompute = (request.args.get("recompute") or "1") in ("1", "true", "yes")

    raw_rows, src = _get_rows_from_request()
    if not raw_rows:
        return _fail(
            "No rows found. Upload a CSV with header or send JSON {rows:[...]}.", 422
        )

    # build code map once (cheap)
    code_map = _make_code_map()

    errors: List[Dict[str, Any]] = []
    to_create: List[AttendancePunch] = []
    duplicates = 0
    processed = 0

    for idx, raw in enumerate(raw_rows, start=1):
        processed += 1
        n = _normalize_row(raw)

        # resolve employee_id using code if needed
        emp_id = n["employee_id"]
        if emp_id is None and n["employee_code"]:
            emp_id = code_map.get(n["employee_code"])

        # validations
        if not emp_id:
            errors.append(
                {"row": idx, "error": "employee_id missing (or code not found)"}
            )
            continue

        emp = Employee.query.get(emp_id)
        if not emp:
            errors.append({"row": idx, "error": f"employee_id not found: {emp_id}"})
            continue

        if not n["ts"]:
            errors.append({"row": idx, "error": "invalid ts (timestamp) format"})
            continue

        if not n["direction"]:
            errors.append({"row": idx, "error": "direction must be 'in' or 'out'"})
            continue

        # duplicate check (per employee, ts, direction)
        existing = AttendancePunch.query.filter(
            and_(
                AttendancePunch.employee_id == emp_id,
                AttendancePunch.ts == n["ts"],
                AttendancePunch.direction == n["direction"],
            )
        ).first()
        if existing:
            if on_dup == "skip":
                duplicates += 1
                continue
            else:  # error policy
                errors.append({"row": idx, "error": "duplicate punch"})
                continue

        p = AttendancePunch(
            company_id=emp.company_id,
            employee_id=emp_id,
            ts=n["ts"],
            direction=n["direction"],
            method=n["method"],
            device_id=n["device_id"],
            note=n["note"],
        )

        # geo optional
        p.lat = n["lat"]
        p.lon = n["lon"]
        p.accuracy_m = n["accuracy_m"]

        to_create.append(p)

    # If dry-run, return report without writing
    if mode == "dry":
        return _ok(
            {
                "mode": "dry",
                "source": src,
                "processed": processed,
                "valid": len(to_create),
                "duplicates_skipped": duplicates if on_dup == "skip" else 0,
                "errors_count": len(errors),
                "errors": errors[:200],  # safety cap
            }
        )

    # Commit mode
    if not to_create:
        # still return a clean report (no writes), so client UX is smooth
        return _ok(
            {
                "mode": "commit",
                "source": src,
                "processed": processed,
                "inserted": 0,
                "duplicates_skipped": duplicates if on_dup == "skip" else 0,
                "errors_count": len(errors),
                "errors": errors[:200],
            }
        )

    inserted: List[AttendancePunch] = []
    try:
        for p in to_create:
            db.session.add(p)
            inserted.append(p)
        db.session.commit()
    except SQLAlchemyError as e:
        db.session.rollback()
        log.exception("punch import failed: %s", e)
        return _fail("database error while inserting punches", 500)

    # recompute (safe/no-op if engine not available)
    if want_recompute:
        _recompute_after_commit(inserted)

    return _ok(
        {
            "mode": "commit",
            "source": src,
            "processed": processed,
            "inserted": len(inserted),
            "duplicates_skipped": duplicates if on_dup == "skip" else 0,
            "errors_count": len(errors),
            "errors": errors[:200],
        },
        status=201,
    )
