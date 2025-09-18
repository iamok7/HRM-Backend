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

def _ok(data=None, status=200):
    return jsonify({"success": True, "data": data}), status

def _fail(msg, status=400, errors: List[Dict[str, Any]] | None = None):
    payload = {"success": False, "error": {"message": msg}}
    if errors: payload["error"]["rows"] = errors
    return jsonify(payload), status

# -------- parsing helpers --------
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

def _normalize_kind(v: Any) -> Optional[str]:
    if v is None: return None
    s = str(v).strip().lower()
    if s in ("in", "i"): return "in"
    if s in ("out", "o"): return "out"
    return None

def _normalize_row(r: Dict[str, Any]) -> Dict[str, Any]:
    """Map aliases, trim, and coerce types. Supports employee_id OR employee_code (code/emp_code)."""
    employee_id = r.get("employee_id") or r.get("employeeId")
    employee_code = r.get("employee_code") or r.get("code") or r.get("emp_code")
    kind = _normalize_kind(r.get("kind"))
    ts = r.get("ts") or r.get("timestamp")
    note = r.get("note")
    source = (r.get("source") or "device").strip().lower()

    try:
        employee_id = int(employee_id) if employee_id not in (None, "", "null") else None
    except Exception:
        employee_id = None

    ts_parsed = _parse_ts(str(ts)) if ts else None
    return {
        "employee_id": employee_id,
        "employee_code": (str(employee_code).strip() if employee_code else None),
        "kind": kind,
        "ts": ts_parsed,
        "note": note,
        "source": source if source else "device",
    }

def _exists(emp_id: int, ts: datetime, kind: str) -> bool:
    return db.session.query(AttendancePunch.id).filter(
        and_(
            AttendancePunch.employee_id == emp_id,
            AttendancePunch.kind == kind,
            AttendancePunch.ts == ts,
        )
    ).first() is not None

def _code_columns():
    # prefer explicit 'employee_code' then 'code' then 'emp_code'
    cols = []
    for c in ("employee_code", "code", "emp_code"):
        if hasattr(Employee, c):
            cols.append(getattr(Employee, c))
    return cols

def _make_code_map() -> dict[str, int]:
    cols = _code_columns()
    if not cols:
        return {}
    primary = cols[0]
    data = db.session.query(Employee.id, primary).all()
    mapping = {}
    for eid, code in data:
        if code:
            mapping[str(code).strip()] = eid
    return mapping

def _recompute_after_commit(inserted: List[AttendancePunch]):
    # Gather (emp_id -> set(days))
    by_emp: dict[int, set[date]] = defaultdict(set)
    for p in inserted:
        try:
            by_emp[p.employee_id].add(p.ts.date())
        except Exception:
            continue
    if not by_emp:
        return
    # Lazy import so startup never breaks if service missing
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

# -------- endpoint --------
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
            errors.append({"row": idx, "employee_id": emp_id, "error": "employee not found"})
            continue
        if not n["ts"]:
            errors.append({"row": idx, "error": "ts missing/invalid"})
            continue
        if n["kind"] not in ("in", "out"):
            errors.append({"row": idx, "error": "kind must be 'in' or 'out'"})
            continue

        # duplicate check
        if _exists(emp_id, n["ts"], n["kind"]):
            duplicates += 1
            if on_dup == "error":
                errors.append({"row": idx, "error": "duplicate (employee_id, ts, kind) exists"})
            continue

        to_create.append(AttendancePunch(
            employee_id=emp_id,
            ts=n["ts"],
            kind=n["kind"],
            source=n.get("source") or "device",
            note=n.get("note")
        ))

    report = {
        "mode": mode,
        "rows_in_payload": len(raw_rows),
        "processed": processed,
        "to_insert": len(to_create),
        "duplicates": duplicates,
        "errors_count": len(errors)
    }

    if mode == "dry":
        return _ok({**report, "errors": errors})

    # commit mode
    try:
        if to_create:
            db.session.bulk_save_objects(to_create)
        db.session.commit()
    except SQLAlchemyError as e:
        db.session.rollback()
        return _fail(f"DB error while inserting: {str(e)}", 500, errors=errors)

    # recompute daily per affected day
    if want_recompute and to_create:
        _recompute_after_commit(to_create)

    inserted_preview = [{
        "id": p.id, "employee_id": p.employee_id, "kind": p.kind,
        "ts": p.ts.isoformat(sep=" "), "source": getattr(p, "source", None)
    } for p in to_create[:25]]

    return _ok({**report, "inserted_preview": inserted_preview, "errors": errors})
