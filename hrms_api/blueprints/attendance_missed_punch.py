# hrms_api/blueprints/attendance_missed_punch.py
from __future__ import annotations

from datetime import datetime, timedelta, date, time as _time
from typing import Optional, Any

from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from sqlalchemy import and_, asc, desc

from hrms_api.extensions import db
from hrms_api.models.employee import Employee
from hrms_api.models.attendance_punch import AttendancePunch
from hrms_api.models.attendance_missed import MissedPunchRequest
from hrms_api.services.attendance_engine import (
    upsert_manual_punch,
    recompute_daily,
)

# permissions (fallbacks keep endpoints usable if perms not seeded yet)
try:
    from hrms_api.common.auth import requires_perms, requires_roles
except Exception:  # pragma: no cover
    def requires_perms(_):
        def _wrap(fn):
            return fn
        return _wrap

    def requires_roles(_):
        def _wrap(fn):
            return fn
        return _wrap


bp = Blueprint(
    "attendance_missed_punch",
    __name__,
    url_prefix="/api/v1/attendance/missed-punch",
)

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
    """Parse a full datetime string (used only for legacy ts/kind payload)."""
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


def _parse_time_str(s: str | None, field: str) -> Optional[_time]:
    if not s:
        return None
    s = s.strip()
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(s, fmt).time()
        except Exception:
            continue
    raise ValueError(f"{field} must be HH:MM or HH:MM:SS")


def _parse_ymd_arg(raw: str | None, field_name: str) -> Optional[date]:
    """
    Very defensive parser for query args `from` / `to`.

    Accepts:
      2025-11-01
      "2025-11-01"
      2025-11-01T10:00:00
      2025-11-01 10:00:00
    and returns a date object.
    """
    if not raw:
        return None

    s = str(raw).strip()

    # remove surrounding quotes if any
    if (s.startswith("'") and s.endswith("'")) or (
        s.startswith('"') and s.endswith('"')
    ):
        s = s[1:-1].strip()

    # split off time if present
    if "T" in s:
        s = s.split("T", 1)[0]
    if " " in s:
        s = s.split(" ", 1)[0]

    s = s[:10]

    try:
        dt = datetime.strptime(s, "%Y-%m-%d")
    except Exception:
        raise ValueError(f"{field_name} must be YYYY-MM-DD")

    return dt.date()


def _normalize_kind(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip().lower()
    return s if s in ("in", "out") else None


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


def _row(m: MissedPunchRequest):
    """Serialize MissedPunchRequest based on model fields (req_date, in_time, out_time...)."""
    return {
        "id": m.id,
        "employee_id": m.employee_id,
        "req_date": m.req_date.isoformat() if getattr(m, "req_date", None) else None,
        "in_time": m.in_time.isoformat() if getattr(m, "in_time", None) else None,
        "out_time": m.out_time.isoformat() if getattr(m, "out_time", None) else None,
        "note": getattr(m, "note", None),
        "status": getattr(m, "status", None),  # pending|approved|rejected|cancelled
        "created_at": (
            m.created_at.isoformat()
            if getattr(m, "created_at", None)
            else None
        ),
        "approved_by": getattr(m, "approved_by", None),
        "approved_at": (
            m.approved_at.isoformat()
            if getattr(m, "approved_at", None)
            else None
        ),
        "approver_note": getattr(m, "approver_note", None),
    }


def _get_emp_from_jwt() -> Optional[int]:
    ident = get_jwt_identity()
    if isinstance(ident, int):
        return ident
    if isinstance(ident, dict):
        for k in ("employee_id", "emp_id"):
            if ident.get(k) is not None:
                try:
                    return int(ident[k])
                except Exception:
                    pass
        uid = ident.get("user_id") or ident.get("id")
        if uid:
            try:
                emp = Employee.query.filter_by(user_id=int(uid)).first()
                return emp.id if emp else None
            except Exception:
                pass
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
      &from=YYYY-MM-DD&to=YYYY-MM-DD   (filters req_date)
      &q=note-text
      &self=1 (only my requests)
      &page=1&size=20&sort=-created_at
    """
    q = MissedPunchRequest.query

    # self=1 → only my own requests based on JWT employee_id
    me_only = str(request.args.get("self", "0")).lower() in ("1", "true", "yes")
    if me_only:
        my_emp = _get_emp_from_jwt()
        if not my_emp:
            return _fail("Employee context not found on token", 401)
        q = q.filter(MissedPunchRequest.employee_id == my_emp)
    else:
        try:
            eid = _as_int(
                request.args.get("employee_id") or request.args.get("employeeId"),
                "employee_id",
            )
        except ValueError as ex:
            return _fail(str(ex), 422)
        if eid:
            q = q.filter(MissedPunchRequest.employee_id == eid)

    # Status filter
    st = (request.args.get("status") or "").strip().lower()
    if st:
        if st not in ("pending", "approved", "rejected", "cancelled"):
            return _fail("invalid status", 422)
        q = q.filter(MissedPunchRequest.status == st)

    # -------- from / to on req_date (IMPORTANT FIX) --------
    raw_from = request.args.get("from")
    raw_to = request.args.get("to")

    from_date = None
    to_date = None

    if raw_from:
        s = str(raw_from).strip()
        try:
            from_date = datetime.strptime(s[:10], "%Y-%m-%d").date()
        except Exception:
            return _fail("from must be YYYY-MM-DD", 422)

    if raw_to:
        s = str(raw_to).strip()
        try:
            to_date = datetime.strptime(s[:10], "%Y-%m-%d").date()
        except Exception:
            return _fail("to must be YYYY-MM-DD", 422)

    if from_date:
        q = q.filter(MissedPunchRequest.req_date >= from_date)

    if to_date:
        q = q.filter(MissedPunchRequest.req_date <= to_date)
    # -------------------------------------------------------

    # free-text search in note
    s = (request.args.get("q") or "").strip()
    if s and hasattr(MissedPunchRequest, "note"):
        q = q.filter(MissedPunchRequest.note.ilike(f"%{s}%"))

    # Sorting
    sort = (request.args.get("sort") or "-created_at").split(",")
    for part in [p.strip() for p in sort if p.strip()]:
        asc_order = not part.startswith("-")
        key = part[1:] if part.startswith("-") else part
        if key == "created_at" and hasattr(MissedPunchRequest, "created_at"):
            col = MissedPunchRequest.created_at
        elif key == "req_date" and hasattr(MissedPunchRequest, "req_date"):
            col = MissedPunchRequest.req_date
        elif key == "status":
            col = MissedPunchRequest.status
        else:
            continue
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
    Create a missed punch request.

    Employee (self) flow → employee_id taken from JWT.
    Admin/HR flow       → can pass employee_id in body.

    Preferred body:

    {
      "employee_id": 1,              # optional for self, required for admin
      "req_date": "2025-11-01",      # YYYY-MM-DD
      "in_time": "09:05",            # optional HH:MM or HH:MM:SS
      "out_time": "18:10",           # optional
      "note": "Forgot to punch"      # or 'reason' / 'remarks'
    }

    Legacy compatible body also supported:

    {
      "ts": "2025-11-01 09:00",
      "kind": "in",
      "note": "..."
    }
    """
    d = request.get_json(silent=True, force=True) or {}

    # ---- 1. figure out employee id ----
    emp_id = _get_emp_from_jwt()

    # if token doesn't carry employee_id (e.g. admin user),
    # allow explicit employee_id from body
    if not emp_id:
        body_emp = d.get("employee_id") or d.get("emp_id")
        if body_emp not in (None, ""):
            try:
                emp_id = int(body_emp)
            except Exception:
                return _fail("employee_id must be integer", 422)
        else:
            # neither JWT nor body gave us employee_id
            return _fail("Employee context not found on token", 401)

    emp = Employee.query.get(emp_id)
    if not emp:
        return _fail("Employee not found", 404)

    # ---- 2. common note text (map later to field that exists on model) ----
    raw_note = (
        d.get("note")
        or d.get("reason")
        or d.get("remarks")
        or ""
    )
    note_text = raw_note.strip() or None

    # ---- 3. preferred path: req_date + in_time/out_time ----
    req_date_str = d.get("req_date") or d.get("date") or d.get("work_date")
    in_time_str = d.get("in_time")
    out_time_str = d.get("out_time")

    req_date: Optional[date] = None
    in_time: Optional[_time] = None
    out_time: Optional[_time] = None

    if req_date_str:
        # parse date
        try:
            req_date = datetime.strptime(req_date_str[:10], "%Y-%m-%d").date()
        except Exception:
            return _fail("req_date must be YYYY-MM-DD", 422)

        # parse times (optional but at least one required)
        try:
            in_time = _parse_time_str(in_time_str, "in_time") if in_time_str else None
        except ValueError as ex:
            return _fail(str(ex), 422)

        try:
            out_time = _parse_time_str(out_time_str, "out_time") if out_time_str else None
        except ValueError as ex:
            return _fail(str(ex), 422)

        if not in_time and not out_time:
            return _fail("At least one of in_time or out_time is required", 422)

    else:
        # ---- 4. legacy path: ts + kind ----
        ts = _parse_ts(d.get("ts"))
        kind = _normalize_kind(d.get("kind"))

        if not (ts and kind):
            return _fail("Either req_date or (ts + kind) must be provided", 422)

        req_date = ts.date()
        if kind == "in":
            in_time = ts.time()
        else:
            out_time = ts.time()

    # ---- 5. date guard rails ----
    if req_date < date.today() - timedelta(days=7):
        return _fail("Backdate window exceeded (7 days)", 422)
    if req_date > date.today() + timedelta(days=1):
        return _fail("Future date not allowed", 422)

    # ---- 6. build kwargs WITHOUT 'note' and map note_text to existing field ----
    kwargs = dict(
        employee_id=emp_id,
        req_date=req_date,
        in_time=in_time,
        out_time=out_time,
        status="pending",
    )

    # try to find which text field actually exists on the model
    text_fields_in_model = ["note", "reason", "remarks", "comment", "description"]
    if note_text:
        for fld in text_fields_in_model:
            if hasattr(MissedPunchRequest, fld):
                kwargs[fld] = note_text
                break

    # now construct safely
    r = MissedPunchRequest(**kwargs)
    db.session.add(r)
    db.session.commit()
    return _ok(_row(r), 201)


@bp.post("/<int:req_id>/cancel")
@jwt_required()
@requires_perms("attendance.missed.create")
def cancel_request(req_id: int):
    emp_id = _get_emp_from_jwt()
    if not emp_id:
        return _fail("Employee context not found on token", 401)

    r = MissedPunchRequest.query.get(req_id)
    if not r:
        return _fail("Request not found", 404)
    if r.employee_id != emp_id:
        return _fail("Not allowed", 403)
    if r.status != "pending":
        return _fail("Only pending requests can be cancelled", 409)

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
      - upsert punches via engine helper (req_date + in_time/out_time)
      - mark request 'approved' with approver fields
      - recompute the daily summary
    """
    r = MissedPunchRequest.query.get(req_id)
    if not r:
        return _fail("Request not found", 404)
    if r.status != "pending":
        return _fail("Only pending requests can be approved", 409)

    emp = Employee.query.get(r.employee_id)
    if not emp:
        return _fail("Employee not found", 404)
    if not r.req_date:
        return _fail("Request has no req_date", 500)

    work_date = r.req_date

    # create IN / OUT punches according to available times
    if getattr(r, "in_time", None):
        upsert_manual_punch(
            employee_id=r.employee_id,
            work_date=work_date,
            direction="in",
            at_time=r.in_time,
            source="missed",
        )

    if getattr(r, "out_time", None):
        upsert_manual_punch(
            employee_id=r.employee_id,
            work_date=work_date,
            direction="out",
            at_time=r.out_time,
            source="missed",
        )

    r.status = "approved"
    r.approved_at = datetime.utcnow()
    approver = _get_emp_from_jwt()
    if approver:
        r.approved_by = approver
    db.session.commit()

    # recompute for that day (DailyStatus, etc.)
    recompute_daily(r.employee_id, work_date)
    return _ok(_row(r))


@bp.post("/<int:req_id>/reject")
@jwt_required()
@requires_perms("attendance.missed.approve")
@requires_roles("manager")
def reject_request(req_id: int):
    d = request.get_json(silent=True, force=True) or {}
    note = (d.get("reason") or d.get("note") or "").strip() or None

    r = MissedPunchRequest.query.get(req_id)
    if not r:
        return _fail("Request not found", 404)
    if r.status != "pending":
        return _fail("Only pending requests can be rejected", 409)

    r.status = "rejected"
    r.approved_at = datetime.utcnow()
    approver = _get_emp_from_jwt()
    if approver:
        r.approved_by = approver
    if hasattr(r, "approver_note"):
        r.approver_note = note
    db.session.commit()
    return _ok(_row(r))
