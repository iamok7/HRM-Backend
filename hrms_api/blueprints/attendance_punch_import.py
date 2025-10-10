from __future__ import annotations
import csv, io, logging
from datetime import datetime, date
from typing import List, Dict, Any, Tuple, Optional
from collections import defaultdict

from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import and_

from hrms_api.extensions import db
from hrms_api.models.employee import Employee
from hrms_api.models.attendance_punch import AttendancePunch

log = logging.getLogger(__name__)

# ---- Guard: prefer DB-backed permission, fallback to role, else JWT only ----
_guard_perm = None
try:
    from hrms_api.common.auth import requires_perms
    _guard_perm = requires_perms("attendance.punch.create")
except Exception:
    _guard_perm = None

try:
    from hrms_api.common.auth import requires_roles
    _guard_role = requires_roles("admin")  # optional fallback
except Exception:
    def _guard_role(fn): return fn

def _GUARD(fn):
    # prefer permission guard if available else role guard else jwt only
    if _guard_perm: return _guard_perm(fn)
    return _guard_role(fn)

bp = Blueprint("attendance_punch_import", __name__, url_prefix="/api/v1/attendance/punches")

# ---------- envelopes ----------
def _ok(data=None, status=200):
    return jsonify({"success": True, "data": data}), status

def _fail(msg, status=400, errors: List[Dict[str, Any]] | None = None):
    payload = {"success": False, "error": {"message": msg}}
    if errors: payload["error"]["rows"] = errors
    return jsonify(payload), status

# ---------- parsing helpers ----------
_DT_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M",
)

def _parse_ts(s: str | None):
    if not s: return None
    s = s.strip()
    # try ISO first
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

def _normalize_kind(v: Any) -> Optional[str]:
    if v is None: return None
    s = str(v).strip().lower()
    if s in ("in", "out"): return s
    return None

def _get_rows_from_request() -> Tuple[List[Dict[str, Any]], str]:
    """
    Returns (rows, source) where source is 'csv' or 'json'.
    CSV: expects multipart/form-data with input name 'file'.
    JSON: expects { "rows": [ {employee_id|employeeId|employee_code|code|emp_code, ts|timestamp, kind, [source], [note]} ] }
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
    mapping = {}
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
                log.exception("[punch-import] recompute failed emp=%s day=%s: %s", emp_id, d, e)

# ---------- normalization per row ----------
def _normalize_row(raw: Dict[str, Any]) -> Dict[str, Any]:
    # aliases
    emp_id = raw.get("employee_id") or raw.get("employeeId")
    emp_code = (raw.get("employee_code")
                or raw.get("code")
                or raw.get("emp_code"))
    ts = raw.get("ts") or raw.get("timestamp")
    kind = raw.get("kind")
    source = raw.get("source", "device")
    note = raw.get("note")

    # coerce & clean
    try:
        emp_id = _as_int(emp_id, "employee_id")
    except ValueError:
        emp_id = None

    ts_dt = _parse_ts(ts)
    k = _normalize_kind(kind)

    return {
        "employee_id": emp_id,
        "employee_code": str(emp_code).strip() if emp_code is not None else None,
        "ts": ts_dt,
        "kind": k,
        "source": (str(source).strip().lower() if source else "device"),
        "note": (str(note).strip() if note else None),
        "raw": raw,
    }

# ---------- endpoint ----------
@bp.post("/import")
@jwt_required()
@_GUARD
def import_punches():
    """
    POST /api/v1/attendance/punches/import?mode=dry|commit&on_duplicate=skip|error&recompute=1|0
    Accepts CSV (multipart/form-data, field 'file') or JSON { rows:[...] }.
    Columns per row:
      - employee_id (or employeeId)     : int
      - OR employee_code/code/emp_code  : str   (will map to employee_id if possible)
      - ts / timestamp                  : 'YYYY-MM-DD HH:MM[:SS]' or ISO8601 (required)
      - kind                            : 'in' | 'out' (required)
      - source                          : optional (default 'device')
      - note                            : optional
    """
    mode = (request.args.get("mode") or "dry").lower()             # dry | commit
    on_dup = (request.args.get("on_duplicate") or "skip").lower()  # skip | error
    want_recompute = (request.args.get("recompute") or "1") in ("1", "true", "yes")

    raw_rows, src = _get_rows_from_request()
    if not raw_rows:
        return _fail("No rows found. Upload a CSV with header or send JSON {rows:[...]}.", 422)

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
            errors.append({"row": idx, "error": "employee_id missing (or code not found)"})
            continue
        if not Employee.query.get(emp_id):
            errors.append({"row": idx, "error": f"employee_id not found: {emp_id}"})
            continue
        if not n["ts"]:
            errors.append({"row": idx, "error": "invalid ts (timestamp) format"})
            continue
        if not n["kind"]:
            errors.append({"row": idx, "error": "kind must be 'in' or 'out'"})
            continue

        # duplicate check
        existing = AttendancePunch.query.filter(
            and_(
                AttendancePunch.employee_id == emp_id,
                AttendancePunch.ts == n["ts"],
                AttendancePunch.kind == n["kind"],
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
            employee_id=emp_id,
            ts=n["ts"],
            kind=n["kind"],
            source=n["source"],
            note=n["note"],
        )
        to_create.append(p)

    # If dry-run, return report without writing
    if mode == "dry":
        return _ok({
            "mode": "dry",
            "source": src,
            "processed": processed,
            "valid": len(to_create),
            "duplicates_skipped": duplicates if on_dup == "skip" else 0,
            "errors_count": len(errors),
            "errors": errors[:200],  # safety cap
        })

    # Commit mode
    if not to_create:
        # still return a clean report (no writes), so client UX is smooth
        return _ok({
            "mode": "commit",
            "source": src,
            "processed": processed,
            "inserted": 0,
            "duplicates_skipped": duplicates if on_dup == "skip" else 0,
            "errors_count": len(errors),
            "errors": errors[:200],
        })

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

    return _ok({
        "mode": "commit",
        "source": src,
        "processed": processed,
        "inserted": len(inserted),
        "duplicates_skipped": duplicates if on_dup == "skip" else 0,
        "errors_count": len(errors),
        "errors": errors[:200],
    }, status=201)
