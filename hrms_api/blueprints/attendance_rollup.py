from __future__ import annotations
from datetime import date
from typing import Dict, Any, Iterable, Optional, List
from flask import Blueprint, request, jsonify
from sqlalchemy import text
from sqlalchemy.engine.reflection import Inspector
from hrms_api.extensions import db
from hrms_api.common.auth import requires_perms

bp = Blueprint("attendance_rollup", __name__, url_prefix="/api/v1/attendance-rollup")

# ---------------- helpers ----------------
def _ok(data=None, status=200, **meta):
    payload = {"success": True, "data": data}
    if meta:
        payload["meta"] = meta
    return jsonify(payload), status

def _fail(message, status=400, code=None, extra=None):
    payload = {"success": False, "error": {"message": message}}
    if code: payload["error"]["code"] = code
    if extra: payload["error"]["extra"] = extra
    return jsonify(payload), status

def _d(s) -> Optional[date]:
    if not s: return None
    try:
        return date.fromisoformat(str(s))
    except Exception:
        return None

def _page_emps(emp_ids: Iterable[int] | None) -> List[int] | None:
    if emp_ids is None: return None
    uniq = []
    seen = set()
    for e in emp_ids:
        try:
            eid = int(e)
        except Exception:
            continue
        if eid not in seen:
            seen.add(eid); uniq.append(eid)
    return uniq

# ---------------- detection ----------------
def _has_table(name: str) -> bool:
    try:
        insp: Inspector = db.inspect(db.engine)
        return name in insp.get_table_names()
    except Exception:
        return False

def _has_columns(table: str, cols: Iterable[str]) -> bool:
    try:
        insp: Inspector = db.inspect(db.engine)
        names = {c["name"] for c in insp.get_columns(table)}
        return all(c in names for c in cols)
    except Exception:
        return False

# ---------------- core rollup logic ----------------
def compute_rollup(company_id: Optional[int], d_from: date, d_to: date, emp_ids: Optional[List[int]] = None) -> Dict[int, Dict[str, Any]]:
    """
    Returns { employee_id: { days_worked, lop_days, ot_hours, holidays, weekly_off } }
    Strategy:
      1) Use attendance_monthly if available (richer, status-aware)
      2) Else, use attendance_punches (distinct punch days = worked)
    """
    if d_to < d_from:
        return {}

    where = []
    params = {"d_from": d_from, "d_to": d_to}

    # --- Strategy 1: attendance_monthly ---
    if _has_table("attendance_monthly") and _has_columns(
        "attendance_monthly",
        ["employee_id", "work_date", "status"]
    ):
        # Optional columns
        has_ot = _has_columns("attendance_monthly", ["ot_hours"])
        has_hol = _has_columns("attendance_monthly", ["is_holiday"])
        has_wof = _has_columns("attendance_monthly", ["is_weekly_off"])
        has_company = _has_columns("attendance_monthly", ["company_id"])

        sql = [
            "SELECT employee_id,",
            " SUM(CASE WHEN status IN ('present','P','PR','WORKED') THEN 1 ELSE 0 END) AS days_worked,",
            " SUM(CASE WHEN status IN ('lop','LWP','LOP','ABSENT','A') THEN 1 ELSE 0 END) AS lop_days,",
        ]
        if has_ot:
            sql.append(" COALESCE(SUM(ot_hours),0) AS ot_hours,")
        else:
            sql.append(" 0 AS ot_hours,")

        if has_hol:
            sql.append(" SUM(CASE WHEN is_holiday THEN 1 ELSE 0 END) AS holidays,")
        else:
            sql.append(" 0 AS holidays,")

        if has_wof:
            sql.append(" SUM(CASE WHEN is_weekly_off THEN 1 ELSE 0 END) AS weekly_off")
        else:
            sql.append(" 0 AS weekly_off")

        sql.append(" FROM attendance_monthly WHERE work_date BETWEEN :d_from AND :d_to")

        if company_id and has_company:
            sql.append(" AND company_id = :cid"); params["cid"] = int(company_id)

        if emp_ids:
            sql.append(" AND employee_id = ANY(:emp_ids)"); params["emp_ids"] = emp_ids

        sql.append(" GROUP BY employee_id")
        rows = db.session.execute(text("\n".join(sql)), params).mappings().all()

        out: Dict[int, Dict[str, Any]] = {}
        for r in rows:
            out[int(r["employee_id"])] = {
                "days_worked": float(r["days_worked"] or 0),
                "lop_days": float(r["lop_days"] or 0),
                "ot_hours": float(r["ot_hours"] or 0),
                "holidays": float(r["holidays"] or 0),
                "weekly_off": float(r["weekly_off"] or 0),
            }
        return out

    # --- Strategy 2: attendance_punches fallback ---
    if _has_table("attendance_punches") and (
        _has_columns("attendance_punches", ["employee_id", "punch_time"]) or
        _has_columns("attendance_punches", ["employee_id", "ts"])  # our model uses 'ts'
    ):
        has_company = _has_columns("attendance_punches", ["company_id"])
        has_ot = _has_columns("attendance_punches", ["ot_hours"])
        # choose timestamp column name
        ts_col = "punch_time" if _has_columns("attendance_punches", ["punch_time"]) else "ts"

        sql = [
            "WITH days AS (",
            f" SELECT employee_id, DATE({ts_col}) AS d,",
        ]
        if has_ot:
            sql.append(" COALESCE(SUM(ot_hours),0) AS ot_hours")
        else:
            sql.append(" 0 AS ot_hours")
        sql.append(f" FROM attendance_punches WHERE {ts_col}::date BETWEEN :d_from AND :d_to")

        if company_id and has_company:
            sql.append(" AND company_id = :cid"); params["cid"] = int(company_id)

        if emp_ids:
            sql.append(" AND employee_id = ANY(:emp_ids)"); params["emp_ids"] = emp_ids

        sql.append(f" GROUP BY employee_id, DATE({ts_col}))")
        sql.append(" SELECT employee_id, COUNT(*) AS days_worked, 0 AS lop_days, COALESCE(SUM(ot_hours),0) AS ot_hours")
        sql.append(" , 0 AS holidays, 0 AS weekly_off")
        sql.append(" FROM days GROUP BY employee_id")
        rows = db.session.execute(text("\n".join(sql)), params).mappings().all()

        out: Dict[int, Dict[str, Any]] = {}
        for r in rows:
            out[int(r["employee_id"])] = {
                "days_worked": float(r["days_worked"] or 0),
                "lop_days": float(r["lop_days"] or 0),
                "ot_hours": float(r["ot_hours"] or 0),
                "holidays": float(r["holidays"] or 0),
                "weekly_off": float(r["weekly_off"] or 0),
            }
        return out

    # nothing available
    return {}

# ---------------- endpoints ----------------
@bp.get("")
@requires_perms("payroll.attendance.read")
def get_rollup():
    """
    Query:
      ?from=YYYY-MM-DD&to=YYYY-MM-DD
      [&company_id=1]
      [&employee_id=1,2,3]   (comma-separated)
    Response:
      { "<employee_id>": { days_worked, lop_days, ot_hours, holidays, weekly_off }, ... }
    """
    d_from = _d(request.args.get("from"))
    d_to = _d(request.args.get("to"))
    if not (d_from and d_to):
        return _fail("'from' and 'to' (YYYY-MM-DD) are required", 422)

    company_id = request.args.get("company_id")
    try:
        company_id = int(company_id) if company_id else None
    except Exception:
        return _fail("company_id must be integer", 422)

    eid_param = request.args.get("employee_id")
    emp_ids = None
    if eid_param:
        emp_ids = _page_emps([e.strip() for e in eid_param.split(",") if e.strip()])

    data = compute_rollup(company_id, d_from, d_to, emp_ids)
    return _ok(data)

# Optional: tiny health/debug
@bp.get("/_capability")
@requires_perms("payroll.attendance.read")
def capability():
    caps = {
        "attendance_monthly": _has_table("attendance_monthly"),
        "attendance_punches": _has_table("attendance_punches"),
    }
    return _ok(caps)
