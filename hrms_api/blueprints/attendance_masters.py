from datetime import datetime, date, time
from flask import Blueprint, request, jsonify
from sqlalchemy import and_
from hrms_api.extensions import db
from hrms_api.common.auth import requires_roles
from flask_jwt_extended import jwt_required
from hrms_api.models.attendance import Holiday, WeeklyOffRule, Shift

bp = Blueprint("attendance_masters", __name__, url_prefix="/api/v1/attendance")

# --- helpers
def _ok(data=None, status=200, **meta):
    payload = {"success": True, "data": data}
    if meta: payload["meta"] = meta
    return jsonify(payload), status

def _fail(msg, status=400):
    return jsonify({"success": False, "error": {"message": msg}}), status

def _parse_date(s):
    if not s: return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d"):
        try: return datetime.strptime(s, fmt).date()
        except Exception: pass
    return None

def _parse_time(s):
    if not s: return None
    for fmt in ("%H:%M", "%H:%M:%S"):
        try: return datetime.strptime(s, fmt).time()
        except Exception: pass
    return None

def _page_limit():
    try:
        page  = max(int(request.args.get("page", 1)), 1)
        limit = min(max(int(request.args.get("limit", 20)), 1), 100)
    except Exception:
        page, limit = 1, 20
    return page, limit

# ===== Holidays =====
def _holiday_row(h: Holiday):
    return {
        "id": h.id, "company_id": h.company_id, "location_id": h.location_id,
        "date": h.date.isoformat(), "name": h.name, "is_optional": h.is_optional,
        "created_at": h.created_at.isoformat() if h.created_at else None
    }

@bp.get("/holidays")
@jwt_required()
def list_holidays():
    q = Holiday.query
    cid = request.args.get("companyId", type=int)
    if cid: q = q.filter(Holiday.company_id == cid)
    loc = request.args.get("locationId", type=int)
    if loc is not None: q = q.filter(Holiday.location_id == loc)
    year = request.args.get("year", type=int)
    month = request.args.get("month", type=int)
    if year: q = q.filter(db.extract("year", Holiday.date) == year)
    if month: q = q.filter(db.extract("month", Holiday.date) == month)
    page, limit = _page_limit()
    total = q.count()
    items = q.order_by(Holiday.date.asc()).offset((page-1)*limit).limit(limit).all()
    return _ok([_holiday_row(i) for i in items], page=page, limit=limit, total=total)

@bp.post("/holidays")
@requires_roles("admin")
def create_holiday():
    d = request.get_json(silent=True, force=True) or {}
    cid = d.get("company_id")
    dt  = _parse_date(d.get("date"))
    name = (d.get("name") or "").strip()
    if not (cid and dt and name):
        return _fail("company_id, date, name are required", 422)
    h = Holiday(company_id=cid, location_id=d.get("location_id"), date=dt, name=name, is_optional=bool(d.get("is_optional", False)))
    db.session.add(h)
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return _fail("Duplicate holiday for company/location/date", 409)
    return _ok(_holiday_row(h), status=201)

@bp.delete("/holidays/<int:hid>")
@requires_roles("admin")
def delete_holiday(hid: int):
    h = Holiday.query.get(hid)
    if not h: return _fail("Holiday not found", 404)
    db.session.delete(h); db.session.commit()
    return _ok({"id": hid, "deleted": True})

# ===== Weekly Off Rules =====
def _wo_row(w: WeeklyOffRule):
    return {
        "id": w.id, "company_id": w.company_id, "location_id": w.location_id,
        "weekday": w.weekday, "is_alternate": w.is_alternate, "week_numbers": w.week_numbers,
        "created_at": w.created_at.isoformat() if w.created_at else None
    }

@bp.get("/weekly-offs")
@jwt_required()
def list_weekly_offs():
    q = WeeklyOffRule.query
    cid = request.args.get("companyId", type=int)
    if cid: q = q.filter(WeeklyOffRule.company_id == cid)
    loc = request.args.get("locationId", type=int)
    if loc is not None: q = q.filter(WeeklyOffRule.location_id == loc)
    page, limit = _page_limit()
    total = q.count()
    items = q.order_by(WeeklyOffRule.weekday.asc()).offset((page-1)*limit).limit(limit).all()
    return _ok([_wo_row(i) for i in items], page=page, limit=limit, total=total)

@bp.post("/weekly-offs")
@requires_roles("admin")
def create_weekly_off():
    d = request.get_json(silent=True, force=True) or {}
    cid = d.get("company_id")
    weekday = d.get("weekday")
    is_alt = bool(d.get("is_alternate", False))
    week_numbers = (d.get("week_numbers") or "").strip() or None
    if weekday is None or cid is None:
        return _fail("company_id and weekday are required", 422)
    if not (0 <= int(weekday) <= 6):
        return _fail("weekday must be 0..6 (Mon..Sun)", 422)
    if is_alt and not week_numbers:
        return _fail("week_numbers required when is_alternate=true (e.g. '2,4')", 422)
    w = WeeklyOffRule(company_id=cid, location_id=d.get("location_id"), weekday=int(weekday),
                      is_alternate=is_alt, week_numbers=week_numbers)
    db.session.add(w)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return _fail("Duplicate weekly off rule", 409)
    return _ok(_wo_row(w), status=201)

@bp.put("/weekly-offs/<int:wid>")
@requires_roles("admin")
def update_weekly_off(wid: int):
    w = WeeklyOffRule.query.get(wid)
    if not w: return _fail("Rule not found", 404)
    d = request.get_json(silent=True, force=True) or {}
    if "weekday" in d:
        if d["weekday"] is None or not (0 <= int(d["weekday"]) <= 6):
            return _fail("weekday must be 0..6", 422)
        w.weekday = int(d["weekday"])
    if "location_id" in d: w.location_id = d["location_id"]
    if "is_alternate" in d: w.is_alternate = bool(d["is_alternate"])
    if "week_numbers" in d: w.week_numbers = (d["week_numbers"] or "").strip() or None
    db.session.commit()
    return _ok(_wo_row(w))

@bp.delete("/weekly-offs/<int:wid>")
@requires_roles("admin")
def delete_weekly_off(wid: int):
    w = WeeklyOffRule.query.get(wid)
    if not w: return _fail("Rule not found", 404)
    db.session.delete(w); db.session.commit()
    return _ok({"id": wid, "deleted": True})

# ===== Shifts =====
def _shift_row(s: Shift):
    return {
        "id": s.id, "company_id": s.company_id, "code": s.code, "name": s.name,
        "start_time": s.start_time.strftime("%H:%M:%S"), "end_time": s.end_time.strftime("%H:%M:%S"),
        "break_minutes": s.break_minutes, "grace_minutes": s.grace_minutes, "is_night": s.is_night,
        "created_at": s.created_at.isoformat() if s.created_at else None
    }

@bp.get("/shifts")
@jwt_required()
def list_shifts():
    q = Shift.query
    cid = request.args.get("companyId", type=int)
    if cid: q = q.filter(Shift.company_id == cid)
    page, limit = _page_limit()
    total = q.count()
    items = q.order_by(Shift.code.asc()).offset((page-1)*limit).limit(limit).all()
    return _ok([_shift_row(i) for i in items], page=page, limit=limit, total=total)

@bp.get("/shifts/<int:sid>")
@jwt_required()
def get_shift(sid: int):
    s = Shift.query.get(sid)
    if not s: return _fail("Shift not found", 404)
    return _ok(_shift_row(s))

@bp.post("/shifts")
@requires_roles("admin")
def create_shift():
    d = request.get_json(silent=True, force=True) or {}
    cid = d.get("company_id")
    code = (d.get("code") or "").strip()
    name = (d.get("name") or "").strip()
    st = _parse_time(d.get("start_time"))
    et = _parse_time(d.get("end_time"))
    if not (cid and code and name and st and et):
        return _fail("company_id, code, name, start_time, end_time are required", 422)
    s = Shift(company_id=cid, code=code, name=name, start_time=st, end_time=et,
              break_minutes=int(d.get("break_minutes") or 0),
              grace_minutes=int(d.get("grace_minutes") or 0),
              is_night=bool(d.get("is_night", False)))
    db.session.add(s)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return _fail("Duplicate shift code for company", 409)
    return _ok(_shift_row(s), status=201)

@bp.put("/shifts/<int:sid>")
@requires_roles("admin")
def update_shift(sid: int):
    s = Shift.query.get(sid)
    if not s: return _fail("Shift not found", 404)
    d = request.get_json(silent=True, force=True) or {}
    for k in ("code", "name"):
        if k in d: setattr(s, k, (d[k] or "").strip() or getattr(s, k))
    if "start_time" in d:
        t = _parse_time(d["start_time"]);  s.start_time = t or s.start_time
    if "end_time" in d:
        t = _parse_time(d["end_time"]);    s.end_time = t or s.end_time
    for k in ("break_minutes", "grace_minutes"):
        if k in d: setattr(s, k, int(d[k] or 0))
    if "is_night" in d: s.is_night = bool(d["is_night"])
    db.session.commit()
    return _ok(_shift_row(s))

@bp.delete("/shifts/<int:sid>")
@requires_roles("admin")
def delete_shift(sid: int):
    s = Shift.query.get(sid)
    if not s: return _fail("Shift not found", 404)
    db.session.delete(s); db.session.commit()
    return _ok({"id": sid, "deleted": True})
