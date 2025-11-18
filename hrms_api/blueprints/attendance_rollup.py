# hrms_api/blueprints/attendance_rollup.py
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
    if code:
        payload["error"]["code"] = code
    if extra:
        payload["error"]["extra"] = extra
    return jsonify(payload), status


def _d(s) -> Optional[date]:
    if not s:
        return None
    try:
        # strict ISO: YYYY-MM-DD
        return date.fromisoformat(str(s))
    except Exception:
        return None


def _page_emps(emp_ids: Iterable[int] | None) -> List[int] | None:
    """
    Normalise and de-duplicate employee IDs.
    """
    if emp_ids is None:
        return None
    uniq: List[int] = []
    seen: set[int] = set()
    for e in emp_ids:
        try:
            eid = int(e)
        except Exception:
            continue
        if eid not in seen:
            seen.add(eid)
            uniq.append(eid)
    return uniq or None


def _emp_param_ids() -> Optional[List[int]]:
    """
    Accept both:
      - employee_id=1,2,3
      - employeeId=1,2,3
    """
    raw = request.args.get("employee_id") or request.args.get("employeeId")
    if not raw:
        return None
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    return _page_emps(parts)


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
def compute_rollup(
    company_id: Optional[int],
    d_from: date,
    d_to: date,
    emp_ids: Optional[List[int]] = None,
) -> Dict[int, Dict[str, Any]]:
    """
    Returns { employee_id: { days_worked, lop_days, ot_hours, holidays, weekly_off } }

    Strategy:
      1) If physical table `attendance_monthly` exists, use that (richer, status-aware).
      2) Else, fall back to `attendance_punches` (distinct punch days = worked).

    NOTE: This is intentionally DB-agnostic. It only checks table/column presence.
    """
    if d_to < d_from:
        return {}

    params: Dict[str, Any] = {"d_from": d_from, "d_to": d_to}

    # --- Strategy 1: attendance_monthly ---
    if _has_table("attendance_monthly") and _has_columns(
        "attendance_monthly",
        ["employee_id", "work_date", "status"],
    ):
        # Optional columns
        has_ot = _has_columns("attendance_monthly", ["ot_hours"])
        has_hol = _has_columns("attendance_monthly", ["is_holiday"])
        has_wof = _has_columns("attendance_monthly", ["is_weekly_off"])
        has_company = _has_columns("attendance_monthly", ["company_id"])

        # Status normalization is done using lower(status) in SQL to tolerate:
        #   - 'Present', 'present', 'P', 'PR', 'WORKED', etc.
        #   - 'Absent', 'ABSENT', 'lop', 'LOP', 'LWP', 'A', etc.
        sql: List[str] = [
            "SELECT employee_id,",
            "  SUM(CASE",
            "        WHEN lower(status) IN ('present','p','pr','worked') THEN 1",
            "        WHEN lower(status) = 'present_full' THEN 1",
            "        WHEN lower(status) = 'present_half' THEN 0.5",
            "        ELSE 0",
            "      END) AS days_worked,",
            "  SUM(CASE",
            "        WHEN lower(status) IN ('lop','lwp','absent','a') THEN 1",
            "        WHEN lower(status) = 'lop_half' THEN 0.5",
            "        ELSE 0",
            "      END) AS lop_days,",
        ]

        if has_ot:
            sql.append("  COALESCE(SUM(ot_hours), 0) AS ot_hours,")
        else:
            sql.append("  0 AS ot_hours,")

        if has_hol:
            sql.append("  SUM(CASE WHEN is_holiday THEN 1 ELSE 0 END) AS holidays,")
        else:
            sql.append("  0 AS holidays,")

        if has_wof:
            sql.append("  SUM(CASE WHEN is_weekly_off THEN 1 ELSE 0 END) AS weekly_off")
        else:
            sql.append("  0 AS weekly_off")

        sql.append(" FROM attendance_monthly")
        sql.append(" WHERE work_date BETWEEN :d_from AND :d_to")

        if company_id is not None and has_company:
            sql.append("   AND company_id = :cid")
            params["cid"] = int(company_id)

        if emp_ids:
            sql.append("   AND employee_id = ANY(:emp_ids)")
            params["emp_ids"] = emp_ids

        sql.append(" GROUP BY employee_id")

        rows = db.session.execute(text("\n".join(sql)), params).mappings().all()

        out: Dict[int, Dict[str, Any]] = {}
        for r in rows:
            out[int(r["employee_id"])] = {
                "days_worked": float(r["days_worked"] or 0),
                "lop_days": float(r["lop_days"] or 0),
                "ot_hours": float(r.get("ot_hours") or 0),
                "holidays": float(r.get("holidays") or 0),
                "weekly_off": float(r.get("weekly_off") or 0),
            }
        return out

    # --- Strategy 2: attendance_punches fallback ---
    # Here we have only punches – we consider "has any punch on that day → worked 1 day".
    if _has_table("attendance_punches") and (
        _has_columns("attendance_punches", ["employee_id", "punch_time"])
        or _has_columns("attendance_punches", ["employee_id", "ts"])  # our model uses 'ts'
    ):
        has_company = _has_columns("attendance_punches", ["company_id"])
        has_ot = _has_columns("attendance_punches", ["ot_hours"])

        # choose timestamp column name
        ts_col = (
            "punch_time"
            if _has_columns("attendance_punches", ["punch_time"])
            else "ts"
        )

        sql: List[str] = [
            "WITH days AS (",
            f"  SELECT employee_id, DATE({ts_col}) AS d,",
        ]
        if has_ot:
            sql.append("         COALESCE(SUM(ot_hours), 0) AS ot_hours")
        else:
            sql.append("         0 AS ot_hours")

        sql.append(f"    FROM attendance_punches")
        sql.append(f"   WHERE {ts_col}::date BETWEEN :d_from AND :d_to")

        if company_id is not None and has_company:
            sql.append("     AND company_id = :cid")
            params["cid"] = int(company_id)

        if emp_ids:
            sql.append("     AND employee_id = ANY(:emp_ids)")
            params["emp_ids"] = emp_ids

        sql.append(f"   GROUP BY employee_id, DATE({ts_col})")
        sql.append(")")
        sql.append("SELECT employee_id,")
        sql.append("       COUNT(*) AS days_worked,")
        sql.append("       0 AS lop_days,")
        sql.append("       COALESCE(SUM(ot_hours), 0) AS ot_hours,")
        sql.append("       0 AS holidays,")
        sql.append("       0 AS weekly_off")
        sql.append("  FROM days")
        sql.append(" GROUP BY employee_id")

        rows = db.session.execute(text("\n".join(sql)), params).mappings().all()

        out2: Dict[int, Dict[str, Any]] = {}
        for r in rows:
            out2[int(r["employee_id"])] = {
                "days_worked": float(r["days_worked"] or 0),
                "lop_days": float(r["lop_days"] or 0),
                "ot_hours": float(r["ot_hours"] or 0),
                "holidays": float(r["holidays"] or 0),
                "weekly_off": float(r["weekly_off"] or 0),
            }
        return out2

    # nothing available → empty
    return {}


# ---------------- endpoints ----------------
@bp.get("")
@requires_perms("payroll.attendance.read")
def get_rollup():
    """
    GET /api/v1/attendance-rollup
      ?from=YYYY-MM-DD
      &to=YYYY-MM-DD
      [&company_id=1]
      [&employee_id=1,2,3] or [&employeeId=1,2,3]

    Response (data):
    {
      "10": {
        "days_worked": 22.0,
        "lop_days": 1.0,
        "ot_hours": 5.5,
        "holidays": 2.0,
        "weekly_off": 4.0
      },
      "11": {
        "days_worked": 20.5,
        "lop_days": 0.5,
        "ot_hours": 0.0,
        "holidays": 1.0,
        "weekly_off": 4.0
      }
    }

    Wrapped:
    {
      "success": true,
      "data": { ...above... },
      "meta": {
        "from": "2025-10-01",
        "to": "2025-10-31",
        "company_id": 1,
        "employee_ids": [10, 11],
        "strategy": "attendance_monthly" | "attendance_punches" | "none"
      }
    }
    """
    d_from = _d(request.args.get("from"))
    d_to = _d(request.args.get("to"))
    if not (d_from and d_to):
        return _fail("'from' and 'to' (YYYY-MM-DD) are required", 422)

    # company_id (optional)
    raw_cid = request.args.get("company_id") or request.args.get("companyId")
    try:
        company_id = int(raw_cid) if raw_cid else None
    except Exception:
        return _fail("company_id must be integer", 422)

    emp_ids = _emp_param_ids()

    # detect strategy for meta
    if _has_table("attendance_monthly") and _has_columns(
        "attendance_monthly",
        ["employee_id", "work_date", "status"],
    ):
        strategy = "attendance_monthly"
    elif _has_table("attendance_punches") and (
        _has_columns("attendance_punches", ["employee_id", "punch_time"])
        or _has_columns("attendance_punches", ["employee_id", "ts"])
    ):
        strategy = "attendance_punches"
    else:
        strategy = "none"

    data = compute_rollup(company_id, d_from, d_to, emp_ids)

    return _ok(
        data,
        from_date=d_from.isoformat(),
        to_date=d_to.isoformat(),
        company_id=company_id,
        employee_ids=emp_ids,
        strategy=strategy,
    )


# Optional: tiny health/debug
@bp.get("/_capability")
@requires_perms("payroll.attendance.read")
def capability():
    """
    Quick probe used by UI/support tools to see what this environment supports.
    """
    caps = {
        "attendance_monthly": _has_table("attendance_monthly"),
        "attendance_punches": _has_table("attendance_punches"),
    }
    return _ok(caps)
