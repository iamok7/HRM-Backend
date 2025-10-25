from __future__ import annotations
from datetime import date, timedelta
from decimal import Decimal
from flask import Blueprint, request, jsonify

from hrms_api.extensions import db
from hrms_api.models.payroll.trade import TradeCategory
from hrms_api.common.auth import requires_perms  # your JWT+RBAC decorator

bp = Blueprint("trades", __name__, url_prefix="/api/v1/trades")



from flask_jwt_extended import jwt_required, get_jwt
from datetime import datetime, timezone

@bp.get("/debug/me")
@jwt_required()
def trades_me_debug():
    claims = get_jwt()
    return _ok({
        "server_utc_now": datetime.now(timezone.utc).isoformat(),
        "roles": claims.get("roles"),
        "perms": claims.get("perms"),
        "exp": claims.get("exp"),
        "iat": claims.get("iat"),
    })

# ---------- helpers ----------
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

def _row(x: TradeCategory):
    return {
        "id": x.id,
        "code": x.code,
        "name": x.name,
        "per_day_rate": float(x.per_day_rate) if x.per_day_rate is not None else None,
        "ot_rate": float(x.ot_rate) if x.ot_rate is not None else None,
        "min_wage_zone": x.min_wage_zone,
        "min_wage_skill": x.min_wage_skill,
        "effective_from": x.effective_from.isoformat() if x.effective_from else None,
        "effective_to": x.effective_to.isoformat() if x.effective_to else None,
        "is_active": x.is_active,
        "created_at": x.created_at.isoformat() if x.created_at else None,
    }

def _parse_decimal(v):
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v))
    except Exception:
        return None

def _page_limit():
    try:
        page = max(int(request.args.get("page", 1)), 1)
        size = min(max(int(request.args.get("size", 20)), 1), 100)
    except Exception:
        page, size = 1, 20
    return page, size

# ---------- routes ----------
@bp.post("")
@requires_perms("payroll.trades.write")
def create_trade():
    data = request.get_json(silent=True) or {}
    code = (data.get("code") or "").strip().upper()
    name = (data.get("name") or "").strip()
    if not code or not name:
        return _fail("code and name are required", 422)

    per_day_rate = _parse_decimal(data.get("per_day_rate"))
    ot_rate = _parse_decimal(data.get("ot_rate"))
    eff_from = data.get("effective_from")
    try:
        eff_from = date.fromisoformat(eff_from) if eff_from else date.today()
    except Exception:
        return _fail("invalid effective_from (use YYYY-MM-DD)", 422)

    # ensure no overlapping active version with same code on eff_from
    overlap = (
        TradeCategory.query
        .filter(TradeCategory.code == code)
        .filter(
            (TradeCategory.effective_to.is_(None) & (TradeCategory.effective_from <= eff_from))
            | ((TradeCategory.effective_to.is_not(None)) & (TradeCategory.effective_from <= eff_from) & (TradeCategory.effective_to >= eff_from))
        )
        .first()
    )
    if overlap:
        return _fail("version overlap for this code on effective_from date", 409)

    x = TradeCategory(
        code=code,
        name=name,
        per_day_rate=per_day_rate,
        ot_rate=ot_rate,
        min_wage_zone=(data.get("min_wage_zone") or "").strip() or None,
        min_wage_skill=(data.get("min_wage_skill") or "").strip() or None,
        effective_from=eff_from,
        is_active=bool(data.get("is_active", True)),
    )
    db.session.add(x)
    db.session.commit()
    return _ok(_row(x), 201)

@bp.get("")
@requires_perms("payroll.trades.read")
def list_trades():
    q = (request.args.get("q") or "").strip()
    active = request.args.get("active")
    on_date = request.args.get("on")  # optional snapshot date YYYY-MM-DD
    page, size = _page_limit()

    stmt = TradeCategory.query
    if q:
        like = f"%{q}%"
        stmt = stmt.filter(db.or_(TradeCategory.code.ilike(like), TradeCategory.name.ilike(like)))
    if active is not None:
        want = active.lower() in ("1", "true", "yes")
        stmt = stmt.filter(TradeCategory.is_active == want)
    if on_date:
        try:
            d = date.fromisoformat(on_date)
            stmt = stmt.filter(TradeCategory.effective_from <= d).filter(
                db.or_(TradeCategory.effective_to.is_(None), TradeCategory.effective_to >= d)
            )
        except Exception:
            return _fail("invalid 'on' date (use YYYY-MM-DD)", 422)

    total = stmt.count()
    rows = (
        stmt.order_by(TradeCategory.code.asc(), TradeCategory.effective_from.desc())
        .offset((page - 1) * size)
        .limit(size)
        .all()
    )
    return _ok([_row(x) for x in rows], meta={"page": page, "size": size, "total": total})

@bp.get("/<int:trade_id>")
@requires_perms("payroll.trades.read")
def get_trade(trade_id: int):
    x = TradeCategory.query.get_or_404(trade_id)
    return _ok(_row(x))

@bp.patch("/<int:trade_id>")
@requires_perms("payroll.trades.write")
def patch_trade(trade_id: int):
    # allow toggling is_active or updating name/min_wage fields (not versioned fields)
    x = TradeCategory.query.get_or_404(trade_id)
    data = request.get_json(silent=True) or {}

    if "name" in data:
        nm = (data.get("name") or "").strip()
        if not nm:
            return _fail("name cannot be empty", 422)
        x.name = nm
    if "is_active" in data:
        x.is_active = bool(data.get("is_active"))
    for f in ("min_wage_zone", "min_wage_skill"):
        if f in data:
            v = (data.get(f) or "").strip() or None
            setattr(x, f, v)

    db.session.commit()
    return _ok(_row(x))

@bp.put("/<int:trade_id>/rates")
@requires_perms("payroll.trades.write")
def new_rate_version(trade_id: int):
    """
    Closes the current version and creates a new effective-dated row for the same code.
    Body can update per_day_rate, ot_rate, effective_from, and optionally name or min_wage_*.
    """
    current = TradeCategory.query.get_or_404(trade_id)
    data = request.get_json(silent=True) or {}

    per_day_rate = _parse_decimal(data.get("per_day_rate"))
    ot_rate = _parse_decimal(data.get("ot_rate"))

    try:
        eff_from = date.fromisoformat(data.get("effective_from"))
    except Exception:
        return _fail("effective_from is required and must be YYYY-MM-DD", 422)

    if eff_from <= current.effective_from:
        return _fail("effective_from must be after the current version's effective_from", 422)

    # Close the current version the day before new eff_from
    current.effective_to = eff_from - timedelta(days=1)

    # Create the new version
    new_row = TradeCategory(
        code=current.code,
        name=(data.get("name") or current.name).strip(),
        per_day_rate=per_day_rate if per_day_rate is not None else current.per_day_rate,
        ot_rate=ot_rate if ot_rate is not None else current.ot_rate,
        min_wage_zone=(data.get("min_wage_zone") or current.min_wage_zone),
        min_wage_skill=(data.get("min_wage_skill") or current.min_wage_skill),
        effective_from=eff_from,
        effective_to=None,
        is_active=current.is_active,
    )
    db.session.add(new_row)
    db.session.commit()
    return _ok({"previous": _row(current), "current": _row(new_row)}, 201)
