# hrms_api/blueprints/attendance_masters.py
from __future__ import annotations

from datetime import datetime, date, time
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required
from sqlalchemy import or_, asc, desc

from hrms_api.extensions import db
from hrms_api.models.master import Company, Location
from hrms_api.models.attendance import Holiday, WeeklyOffRule, Shift

# RBAC (no-op fallbacks if not wired)
try:
    from hrms_api.common.auth import requires_perms
except Exception:
    def requires_perms(_):
        def _wrap(fn): return fn
        return _wrap

bp = Blueprint("attendance_masters", __name__, url_prefix="/api/v1/attendance/masters")

# ========== envelopes ==========
def _ok(data=None, status=200, **meta):
    payload = {"success": True, "data": data}
    if meta:
        payload["meta"] = meta
    return jsonify(payload), status

def _fail(msg, status=400, code=None, detail=None):
    err = {"message": msg}
    if code: err["code"] = code
    if detail: err["detail"] = detail
    return jsonify({"success": False, "error": err}), status

# ========== helpers ==========
DEFAULT_PAGE, DEFAULT_SIZE, MAX_SIZE = 1, 20, 100

def _page_size():
    try:
        page = max(int(request.args.get("page", DEFAULT_PAGE)), 1)
    except Exception:
        page = DEFAULT_PAGE
    raw = request.args.get("size", request.args.get("limit", DEFAULT_SIZE))
    try:
        size = max(1, min(int(raw), MAX_SIZE))
    except Exception:
        size = DEFAULT_SIZE
    return page, size

def _sort_params(allowed: dict[str, object]):
    raw = (request.args.get("sort") or "").strip()
    out = []
    if not raw:
        return out
    for part in [p.strip() for p in raw.split(",") if p.strip()]:
        asc_order = True
        key = part
        if part.startswith("-"):
            asc_order = False
            key = part[1:]
        col = allowed.get(key)
        if col is not None:
            out.append((col, asc_order))
    return out

def _as_int(val, field):
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

def _parse_hhmm(s, field):
    if s in (None, "", "null"): return None
    try:
        hh, mm = str(s).split(":")
        hh = int(hh); mm = int(mm)
        if not (0 <= hh <= 23 and 0 <= mm <= 59): raise ValueError
        return time(hour=hh, minute=mm)
    except Exception:
        raise ValueError(f"{field} must be HH:MM (00–23:00–59)")

def _parse_date(s, field):
    if s in (None, "", "null"): return None
    try:
        return datetime.strptime(str(s), "%Y-%m-%d").date()
    except Exception:
        raise ValueError(f"{field} must be YYYY-MM-DD")

# ========================= SHIFTS =========================
def _shift_row(s: Shift):
    is_night = bool(getattr(s, "is_night", getattr(s, "is_night_shift", False)))
    return {
        "id": s.id,
        "company_id": s.company_id,
        "name": getattr(s, "name", None),
        "code": getattr(s, "code", None),
        "start_time": s.start_time.strftime("%H:%M") if getattr(s, "start_time", None) else None,
        "end_time": s.end_time.strftime("%H:%M") if getattr(s, "end_time", None) else None,
        "break_minutes": int(getattr(s, "break_minutes", 0) or 0),
        "grace_minutes": int(getattr(s, "grace_minutes", 0) or 0),
        "is_night": is_night,
        "is_active": getattr(s, "is_active", True),
        "created_at": getattr(s, "created_at", None).isoformat() if getattr(s, "created_at", None) else None,
        "updated_at": getattr(s, "updated_at", None).isoformat() if getattr(s, "updated_at", None) else None,
    }

@bp.get("/shifts")
@jwt_required()
@requires_perms("attendance.shift.read")
def list_shifts():
    q = Shift.query

    # filters
    try:
        cid = _as_int(request.args.get("company_id"), "company_id") if "company_id" in request.args else None
    except ValueError as ex:
        return _fail(str(ex), 422)
    if cid: q = q.filter(Shift.company_id == cid)

    try:
        is_active = _as_bool(request.args.get("is_active"), "is_active") if "is_active" in request.args else None
    except ValueError as ex:
        return _fail(str(ex), 422)
    if is_active is True: q = q.filter(Shift.is_active.is_(True))
    if is_active is False: q = q.filter(Shift.is_active.is_(False))

    s = (request.args.get("q") or "").strip()
    if s:
        like = f"%{s}%"
        conds = [Shift.name.ilike(like)]
        if hasattr(Shift, "code"): conds.append(Shift.code.ilike(like))
        q = q.filter(or_(*conds))

    # sorting
    allowed = {
        "id": Shift.id,
        "name": getattr(Shift, "name", Shift.id),
        "code": getattr(Shift, "code", Shift.id),
        "created_at": getattr(Shift, "created_at", Shift.id),
        "updated_at": getattr(Shift, "updated_at", Shift.id),
    }
    for col, asc_order in _sort_params(allowed):
        q = q.order_by(asc(col) if asc_order else desc(col))
    if not request.args.get("sort"):
        q = q.order_by(asc(getattr(Shift, "name", Shift.id)))

    # paging
    page, size = _page_size()
    total = q.count()
    items = q.offset((page - 1) * size).limit(size).all()
    return _ok([_shift_row(i) for i in items], page=page, size=size, total=total)

@bp.get("/shifts/<int:sid>")
@jwt_required()
@requires_perms("attendance.shift.read")
def get_shift(sid: int):
    s = Shift.query.get(sid)
    if not s: return _fail("Shift not found", 404)
    return _ok(_shift_row(s))

@bp.post("/shifts")
@jwt_required()
@requires_perms("attendance.shift.create")
def create_shift():
    d = request.get_json(silent=True, force=True) or {}
    try:
        cid = _as_int(d.get("company_id"), "company_id")
    except ValueError as ex:
        return _fail(str(ex), 422)
    name = (d.get("name") or "").strip()
    if not (cid and name): return _fail("company_id and name are required", 422)

    if not Company.query.get(cid): return _fail("company_id not found", 404)

    # times/flags
    try:
        st = _parse_hhmm(d.get("start_time"), "start_time") if d.get("start_time") else None
        et = _parse_hhmm(d.get("end_time"), "end_time") if d.get("end_time") else None
    except ValueError as ex:
        return _fail(str(ex), 422)

    try:
        is_night = _as_bool(d.get("is_night") or d.get("is_night_shift"), "is_night") if ("is_night" in d or "is_night_shift" in d) else False
        is_active = _as_bool(d.get("is_active"), "is_active") if "is_active" in d else True
    except ValueError as ex:
        return _fail(str(ex), 422)

    break_minutes = None
    grace_minutes = None
    if "break_minutes" in d and d.get("break_minutes") is not None:
        try: break_minutes = _as_int(d["break_minutes"], "break_minutes")
        except ValueError as ex: return _fail(str(ex), 422)
    if "grace_minutes" in d and d.get("grace_minutes") is not None:
        try: grace_minutes = _as_int(d["grace_minutes"], "grace_minutes")
        except ValueError as ex: return _fail(str(ex), 422)

    code = (d.get("code") or "").strip() or None

    # duplicates
    dup = Shift.query.filter(Shift.company_id == cid, db.func.lower(Shift.name) == name.lower()).first()
    if dup: return _fail("Shift with same name already exists for this company", 409)
    if code and hasattr(Shift, "code"):
        dupc = Shift.query.filter(Shift.company_id == cid, db.func.lower(Shift.code) == code.lower()).first()
        if dupc: return _fail("Shift code already exists for this company", 409)

    s = Shift(company_id=cid, name=name)
    if hasattr(Shift, "code"): s.code = code
    if hasattr(Shift, "start_time"): s.start_time = st
    if hasattr(Shift, "end_time"): s.end_time = et
    if hasattr(Shift, "is_active"): s.is_active = bool(is_active)
    # handle both field names for night shift
    if hasattr(Shift, "is_night"): s.is_night = bool(is_night)
    if hasattr(Shift, "is_night_shift"): s.is_night_shift = bool(is_night)
    if hasattr(Shift, "break_minutes"): s.break_minutes = break_minutes
    if hasattr(Shift, "grace_minutes"): s.grace_minutes = grace_minutes

    db.session.add(s); db.session.commit()
    return _ok(_shift_row(s), 201)

@bp.put("/shifts/<int:sid>")
@jwt_required()
@requires_perms("attendance.shift.update")
def update_shift(sid: int):
    s = Shift.query.get(sid)
    if not s: return _fail("Shift not found", 404)
    d = request.get_json(silent=True, force=True) or {}

    if "company_id" in d:
        try:
            cid = _as_int(d.get("company_id"), "company_id")
        except ValueError as ex:
            return _fail(str(ex), 422)
        if not Company.query.get(cid): return _fail("company_id not found", 404)
        s.company_id = cid

    if "name" in d:
        nm = (d.get("name") or "").strip()
        if not nm: return _fail("name cannot be empty", 422)
        dup = Shift.query.filter(Shift.id != s.id, Shift.company_id == s.company_id, db.func.lower(Shift.name) == nm.lower()).first()
        if dup: return _fail("Shift with same name already exists for this company", 409)
        s.name = nm

    if "code" in d and hasattr(Shift, "code"):
        cd = (d.get("code") or "").strip() or None
        if cd:
            dupc = Shift.query.filter(Shift.id != s.id, Shift.company_id == s.company_id, db.func.lower(Shift.code) == cd.lower()).first()
            if dupc: return _fail("Shift code already exists for this company", 409)
        s.code = cd

    if "start_time" in d and hasattr(Shift, "start_time"):
        try: s.start_time = _parse_hhmm(d.get("start_time"), "start_time")
        except ValueError as ex: return _fail(str(ex), 422)

    if "end_time" in d and hasattr(Shift, "end_time"):
        try: s.end_time = _parse_hhmm(d.get("end_time"), "end_time")
        except ValueError as ex: return _fail(str(ex), 422)

    if "is_active" in d and hasattr(Shift, "is_active"):
        try: s.is_active = bool(_as_bool(d.get("is_active"), "is_active"))
        except ValueError as ex: return _fail(str(ex), 422)

    if "is_night" in d or "is_night_shift" in d:
        try: is_night = bool(_as_bool(d.get("is_night", d.get("is_night_shift")), "is_night"))
        except ValueError as ex: return _fail(str(ex), 422)
        if hasattr(Shift, "is_night"): s.is_night = is_night
        if hasattr(Shift, "is_night_shift"): s.is_night_shift = is_night

    for key in ("break_minutes", "grace_minutes"):
        if key in d and hasattr(Shift, key):
            try: setattr(s, key, _as_int(d.get(key), key) if d.get(key) is not None else None)
            except ValueError as ex: return _fail(str(ex), 422)

    db.session.commit()
    return _ok(_shift_row(s))

@bp.delete("/shifts/<int:sid>")
@jwt_required()
@requires_perms("attendance.shift.delete")
def delete_shift(sid: int):
    s = Shift.query.get(sid)
    if not s: return _fail("Shift not found", 404)
    if hasattr(Shift, "is_active"):
        s.is_active = False
        db.session.commit()
        return _ok({"id": sid, "is_active": False})
    try:
        db.session.delete(s); db.session.commit()
        return _ok({"deleted": True, "id": sid})
    except Exception as e:
        db.session.rollback()
        return _fail("Cannot delete: referenced by other records", 409, detail=str(e))

# ========================= WEEKLY OFF RULES =========================
def _wor_row(w: WeeklyOffRule):
    return {
        "id": w.id,
        "company_id": w.company_id,
        "location_id": getattr(w, "location_id", None),
        "weekday": int(getattr(w, "weekday", 0) or 0),   # 0=Mon..6=Sun
        "is_alternate": bool(getattr(w, "is_alternate", False)),
        "week_numbers": getattr(w, "week_numbers", None),  # "1,3" etc.
        "is_active": getattr(w, "is_active", True),
        "created_at": getattr(w, "created_at", None).isoformat() if getattr(w, "created_at", None) else None,
        "updated_at": getattr(w, "updated_at", None).isoformat() if getattr(w, "updated_at", None) else None,
    }

@bp.get("/weekly-off")
@jwt_required()
@requires_perms("attendance.weeklyoff.read")
def list_weeklyoff():
    q = WeeklyOffRule.query
    try:
        cid = _as_int(request.args.get("company_id"), "company_id") if "company_id" in request.args else None
        lid = _as_int(request.args.get("location_id"), "location_id") if "location_id" in request.args else None
    except ValueError as ex:
        return _fail(str(ex), 422)
    if cid: q = q.filter(WeeklyOffRule.company_id == cid)
    if lid: q = q.filter(WeeklyOffRule.location_id == lid)

    try:
        is_active = _as_bool(request.args.get("is_active"), "is_active") if "is_active" in request.args else None
    except ValueError as ex:
        return _fail(str(ex), 422)
    if is_active is True: q = q.filter(WeeklyOffRule.is_active.is_(True))
    if is_active is False: q = q.filter(WeeklyOffRule.is_active.is_(False))

    allowed = {
        "id": WeeklyOffRule.id,
        "company_id": WeeklyOffRule.company_id,
        "location_id": WeeklyOffRule.location_id,
        "weekday": WeeklyOffRule.weekday,
        "created_at": getattr(WeeklyOffRule, "created_at", WeeklyOffRule.id),
    }
    for col, asc_order in _sort_params(allowed):
        q = q.order_by(asc(col) if asc_order else desc(col))

    page, size = _page_size()
    total = q.count()
    items = q.offset((page - 1) * size).limit(size).all()
    return _ok([_wor_row(i) for i in items], page=page, size=size, total=total)

@bp.post("/weekly-off")
@jwt_required()
@requires_perms("attendance.weeklyoff.create")
def create_weeklyoff():
    d = request.get_json(silent=True, force=True) or {}
    try:
        cid = _as_int(d.get("company_id"), "company_id")
        lid = _as_int(d.get("location_id"), "location_id") if d.get("location_id") is not None else None
    except ValueError as ex:
        return _fail(str(ex), 422)
    if not cid: return _fail("company_id is required", 422)
    if not Company.query.get(cid): return _fail("company_id not found", 404)
    if lid and not Location.query.get(lid): return _fail("location_id not found", 404)

    try:
        weekday = _as_int(d.get("weekday"), "weekday")
        if weekday is None or not (0 <= weekday <= 6): raise ValueError
    except Exception:
        return _fail("weekday must be integer 0..6 (Mon=0)", 422)

    try:
        is_alt = bool(_as_bool(d.get("is_alternate"), "is_alternate")) if "is_alternate" in d else False
        is_active = bool(_as_bool(d.get("is_active"), "is_active")) if "is_active" in d else True
    except ValueError as ex:
        return _fail(str(ex), 422)

    week_numbers = (d.get("week_numbers") or "").strip() or None  # e.g., "1,3"
    w = WeeklyOffRule(company_id=cid, location_id=lid, weekday=weekday)
    if hasattr(WeeklyOffRule, "is_alternate"): w.is_alternate = is_alt
    if hasattr(WeeklyOffRule, "week_numbers"): w.week_numbers = week_numbers
    if hasattr(WeeklyOffRule, "is_active"): w.is_active = is_active

    db.session.add(w); db.session.commit()
    return _ok(_wor_row(w), 201)

@bp.put("/weekly-off/<int:wid>")
@jwt_required()
@requires_perms("attendance.weeklyoff.update")
def update_weeklyoff(wid: int):
    w = WeeklyOffRule.query.get(wid)
    if not w: return _fail("Weekly-off rule not found", 404)
    d = request.get_json(silent=True, force=True) or {}

    if "company_id" in d:
        try:
            cid = _as_int(d.get("company_id"), "company_id")
        except ValueError as ex:
            return _fail(str(ex), 422)
        if not Company.query.get(cid): return _fail("company_id not found", 404)
        w.company_id = cid

    if "location_id" in d:
        try:
            lid = _as_int(d.get("location_id"), "location_id") if d.get("location_id") is not None else None
        except ValueError as ex:
            return _fail(str(ex), 422)
        if lid and not Location.query.get(lid): return _fail("location_id not found", 404)
        w.location_id = lid

    if "weekday" in d:
        try:
            wd = _as_int(d.get("weekday"), "weekday")
            if wd is None or not (0 <= wd <= 6): raise ValueError
        except Exception:
            return _fail("weekday must be integer 0..6 (Mon=0)", 422)
        w.weekday = wd

    if "is_alternate" in d and hasattr(WeeklyOffRule, "is_alternate"):
        try: w.is_alternate = bool(_as_bool(d.get("is_alternate"), "is_alternate"))
        except ValueError as ex: return _fail(str(ex), 422)

    if "week_numbers" in d and hasattr(WeeklyOffRule, "week_numbers"):
        w.week_numbers = (d.get("week_numbers") or "").strip() or None

    if "is_active" in d and hasattr(WeeklyOffRule, "is_active"):
        try: w.is_active = bool(_as_bool(d.get("is_active"), "is_active"))
        except ValueError as ex: return _fail(str(ex), 422)

    db.session.commit()
    return _ok(_wor_row(w))

@bp.delete("/weekly-off/<int:wid>")
@jwt_required()
@requires_perms("attendance.weeklyoff.delete")
def delete_weeklyoff(wid: int):
    w = WeeklyOffRule.query.get(wid)
    if not w: return _fail("Weekly-off rule not found", 404)
    if hasattr(WeeklyOffRule, "is_active"):
        w.is_active = False
        db.session.commit()
        return _ok({"id": wid, "is_active": False})
    try:
        db.session.delete(w); db.session.commit()
        return _ok({"deleted": True, "id": wid})
    except Exception as e:
        db.session.rollback()
        return _fail("Cannot delete: referenced by other records", 409, detail=str(e))

# ========================= HOLIDAYS =========================
def _holiday_row(h: Holiday):
    return {
        "id": h.id,
        "company_id": h.company_id,
        "location_id": getattr(h, "location_id", None),
        "date": h.date.isoformat() if getattr(h, "date", None) else None,
        "name": getattr(h, "name", None),
        "is_active": getattr(h, "is_active", True),
        "created_at": getattr(h, "created_at", None).isoformat() if getattr(h, "created_at", None) else None,
        "updated_at": getattr(h, "updated_at", None).isoformat() if getattr(h, "updated_at", None) else None,
    }

@bp.get("/holidays")
@jwt_required()
@requires_perms("attendance.holiday.read")
def list_holidays():
    q = Holiday.query
    try:
        cid = _as_int(request.args.get("company_id"), "company_id") if "company_id" in request.args else None
        lid = _as_int(request.args.get("location_id"), "location_id") if "location_id" in request.args else None
    except ValueError as ex:
        return _fail(str(ex), 422)
    if cid: q = q.filter(Holiday.company_id == cid)
    if lid: q = q.filter(Holiday.location_id == lid)

    try:
        is_active = _as_bool(request.args.get("is_active"), "is_active") if "is_active" in request.args else None
    except ValueError as ex:
        return _fail(str(ex), 422)
    if is_active is True: q = q.filter(Holiday.is_active.is_(True))
    if is_active is False: q = q.filter(Holiday.is_active.is_(False))

    dfrom = request.args.get("from")
    dto   = request.args.get("to")
    if dfrom:
        try: q = q.filter(Holiday.date >= _parse_date(dfrom, "from"))
        except ValueError as ex: return _fail(str(ex), 422)
    if dto:
        try: q = q.filter(Holiday.date <= _parse_date(dto, "to"))
        except ValueError as ex: return _fail(str(ex), 422)

    s = (request.args.get("q") or "").strip()
    if s:
        like = f"%{s}%"
        q = q.filter(Holiday.name.ilike(like))

    allowed = {
        "id": Holiday.id,
        "date": Holiday.date,
        "name": Holiday.name,
        "created_at": getattr(Holiday, "created_at", Holiday.id),
    }
    for col, asc_order in _sort_params(allowed):
        q = q.order_by(asc(col) if asc_order else desc(col))
    if not request.args.get("sort"):
        q = q.order_by(desc(Holiday.date))

    page, size = _page_size()
    total = q.count()
    items = q.offset((page - 1) * size).limit(size).all()
    return _ok([_holiday_row(i) for i in items], page=page, size=size, total=total)

@bp.get("/holidays/<int:hid>")
@jwt_required()
@requires_perms("attendance.holiday.read")
def get_holiday(hid: int):
    h = Holiday.query.get(hid)
    if not h: return _fail("Holiday not found", 404)
    return _ok(_holiday_row(h))

@bp.post("/holidays")
@jwt_required()
@requires_perms("attendance.holiday.create")
def create_holiday():
    d = request.get_json(silent=True, force=True) or {}
    try:
        cid = _as_int(d.get("company_id"), "company_id")
        lid = _as_int(d.get("location_id"), "location_id") if d.get("location_id") is not None else None
        the_date = _parse_date(d.get("date"), "date")
    except ValueError as ex:
        return _fail(str(ex), 422)

    name = (d.get("name") or "").strip()
    if not (cid and the_date and name): return _fail("company_id, date, name are required", 422)
    if not Company.query.get(cid): return _fail("company_id not found", 404)
    if lid and not Location.query.get(lid): return _fail("location_id not found", 404)

    # prevent duplicate holiday on same date + scope
    dup = Holiday.query.filter(
        Holiday.company_id == cid,
        Holiday.date == the_date,
        (Holiday.location_id == lid) if lid else Holiday.location_id.is_(None)
    ).first()
    if dup: return _fail("Holiday already exists for this scope/date", 409)

    h = Holiday(company_id=cid, date=the_date, name=name, location_id=lid)
    if hasattr(Holiday, "is_active"): h.is_active = True
    db.session.add(h); db.session.commit()
    return _ok(_holiday_row(h), 201)

@bp.put("/holidays/<int:hid>")
@jwt_required()
@requires_perms("attendance.holiday.update")
def update_holiday(hid: int):
    h = Holiday.query.get(hid)
    if not h: return _fail("Holiday not found", 404)
    d = request.get_json(silent=True, force=True) or {}

    if "company_id" in d:
        try:
            cid = _as_int(d.get("company_id"), "company_id")
        except ValueError as ex:
            return _fail(str(ex), 422)
        if not Company.query.get(cid): return _fail("company_id not found", 404)
        h.company_id = cid

    if "location_id" in d:
        try:
            lid = _as_int(d.get("location_id"), "location_id") if d.get("location_id") is not None else None
        except ValueError as ex:
            return _fail(str(ex), 422)
        if lid and not Location.query.get(lid): return _fail("location_id not found", 404)
        h.location_id = lid

    if "date" in d:
        try: h.date = _parse_date(d.get("date"), "date")
        except ValueError as ex: return _fail(str(ex), 422)

    if "name" in d:
        nm = (d.get("name") or "").strip()
        if not nm: return _fail("name cannot be empty", 422)
        h.name = nm

    if "is_active" in d and hasattr(Holiday, "is_active"):
        try: h.is_active = bool(_as_bool(d.get("is_active"), "is_active"))
        except ValueError as ex: return _fail(str(ex), 422)

    db.session.commit()
    return _ok(_holiday_row(h))

@bp.delete("/holidays/<int:hid>")
@jwt_required()
@requires_perms("attendance.holiday.delete")
def delete_holiday(hid: int):
    h = Holiday.query.get(hid)
    if not h: return _fail("Holiday not found", 404)
    if hasattr(Holiday, "is_active"):
        h.is_active = False
        db.session.commit()
        return _ok({"id": hid, "is_active": False})
    try:
        db.session.delete(h); db.session.commit()
        return _ok({"deleted": True, "id": hid})
    except Exception as e:
        db.session.rollback()
        return _fail("Cannot delete: referenced by other records", 409, detail=str(e))
