from __future__ import annotations
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, Optional, Tuple, List

from flask import Blueprint, request, jsonify
from sqlalchemy import or_
from sqlalchemy.sql.sqltypes import String, Date, Boolean, JSON as _JSON
from hrms_api.extensions import db
from hrms_api.common.auth import requires_perms

from hrms_api.models.payroll.pay_run import PayRun, PayRunItem
from hrms_api.models.payroll.compliance import ComplianceEvent as ConfigModel  # your single table

bp = Blueprint("pay_compliance", __name__, url_prefix="/api/v1/compliance")

# ---------- tiny helpers ----------
def _ok(data=None, status=200, **meta):
    payload = {"success": True, "data": data}
    if meta: payload["meta"] = meta
    return jsonify(payload), status

def _fail(message, status=400, code=None, extra=None):
    payload = {"success": False, "error": {"message": message}}
    if code: payload["error"]["code"] = code
    if extra is not None: payload["error"]["extra"] = extra
    return jsonify(payload), status

def _d(s) -> Optional[date]:
    if not s: return None
    try: return date.fromisoformat(str(s))
    except Exception: return None

def _dec(x) -> Optional[Decimal]:
    if x is None or x == "": return None
    try: return Decimal(str(x))
    except Exception: return None

def _as_float(x):
    try: return float(x) if x is not None else None
    except Exception: return None

# ---------- auto mapping to your column names ----------
def _cols():
    # list of (name, column) from the mapped table
    return list(ConfigModel.__table__.columns.items())

def _pick_by_keywords_and_type(keywords: List[str], prefer_type=None, fallback_type=None):
    cols = _cols()
    # 1) keyword + type match
    for name, col in cols:
        nm = name.lower()
        if any(k in nm for k in keywords):
            if prefer_type is None or isinstance(col.type, prefer_type):
                return name
    # 2) keyword only
    for name, col in cols:
        nm = name.lower()
        if any(k in nm for k in keywords):
            return name
    # 3) bare type guess
    if prefer_type:
        for name, col in cols:
            if isinstance(col.type, prefer_type):
                return name
    if fallback_type:
        for name, col in cols:
            if isinstance(col.type, fallback_type):
                return name
    return None

# Try to detect sensible defaults:
CODE_F   = _pick_by_keywords_and_type(["code", "key", "name", "type"], prefer_type=String) or "code"
SCOPE_F  = _pick_by_keywords_and_type(["scope", "state", "region", "company"], prefer_type=String) or "scope"
VALUE_F  = _pick_by_keywords_and_type(["value", "json", "data", "payload"], prefer_type=_JSON) or "value"
FROM_F   = _pick_by_keywords_and_type(["from", "start", "begin", "valid"], prefer_type=Date, fallback_type=Date) or "effective_from"
TO_F     = _pick_by_keywords_and_type(["to", "end", "till", "validto"], prefer_type=Date, fallback_type=Date) or "effective_to"
ACTIVE_F = _pick_by_keywords_and_type(["active", "enabled", "is_active"], prefer_type=Boolean)

def _get(obj, field: Optional[str]):
    return getattr(obj, field) if (obj is not None and field and hasattr(obj, field)) else None

def _set_kw(field: Optional[str], value: Any) -> Dict[str, Any]:
    return {field: value} if field else {}

@bp.get("/_introspect")
@requires_perms("payroll.compliance.read")
def _introspect():
    # Handy debug endpoint to see how columns were auto-mapped
    cols = [{"name": n, "type": str(c.type)} for n, c in _cols()]
    return _ok({
        "table": ConfigModel.__table__.name,
        "columns": cols,
        "mapping": {
            "CODE_F": CODE_F, "SCOPE_F": SCOPE_F, "VALUE_F": VALUE_F,
            "FROM_F": FROM_F, "TO_F": TO_F, "ACTIVE_F": ACTIVE_F
        }
    })

# ---------- config readers ----------
def _cfg_query(code: str, on: date, scope: Optional[str] = None):
    q = ConfigModel.query.filter(getattr(ConfigModel, CODE_F) == code)
    if scope is not None:
        q = q.filter(getattr(ConfigModel, SCOPE_F) == scope)
    q = (q.filter(getattr(ConfigModel, FROM_F) <= on)
           .filter(or_(getattr(ConfigModel, TO_F).is_(None), getattr(ConfigModel, TO_F) >= on))
           .order_by(getattr(ConfigModel, FROM_F).desc()))
    return q

def _cfg_get(code: str, on: date, scope: Optional[str] = None) -> Optional[Dict[str, Any]]:
    rec = _cfg_query(code, on, scope).first()
    return _get(rec, VALUE_F) if rec else None

# ---------- Config endpoints ----------
@bp.get("/stat-config")
@requires_perms("payroll.compliance.read")
def stat_config_list():
    q = ConfigModel.query
    if request.args.get("code"):
        q = q.filter(getattr(ConfigModel, CODE_F) == request.args["code"])
    if request.args.get("scope"):
        q = q.filter(getattr(ConfigModel, SCOPE_F) == request.args["scope"])
    active_on = _d(request.args.get("active_on"))
    if active_on:
        q = q.filter(
            getattr(ConfigModel, FROM_F) <= active_on,
            or_(getattr(ConfigModel, TO_F).is_(None), getattr(ConfigModel, TO_F) >= active_on),
        )
    q = q.order_by(getattr(ConfigModel, CODE_F).asc(),
                   getattr(ConfigModel, SCOPE_F).asc(),
                   getattr(ConfigModel, FROM_F).desc())
    rows = q.all()

    def row(x: ConfigModel):
        return {
            "id": x.id,
            "code": _get(x, CODE_F),
            "scope": _get(x, SCOPE_F),
            "value": _get(x, VALUE_F),
            "effective_from": _get(x, FROM_F).isoformat() if _get(x, FROM_F) else None,
            "effective_to": _get(x, TO_F).isoformat() if _get(x, TO_F) else None,
            "is_active": _get(x, ACTIVE_F) if ACTIVE_F else True,
        }
    return _ok([row(r) for r in rows])

@bp.post("/stat-config")
@requires_perms("payroll.compliance.write")
def stat_config_create():
    j = request.get_json(silent=True) or {}
    code = (j.get("code") or "").strip().upper()
    if not code: return _fail("code is required", 422)
    eff_from = _d(j.get("effective_from"))
    if not eff_from: return _fail("effective_from is required (YYYY-MM-DD)", 422)
    eff_to = _d(j.get("effective_to"))
    if eff_to and eff_to < eff_from: return _fail("effective_to must be >= effective_from", 422)
    scope = (j.get("scope") or "").strip() or None
    value = j.get("value")
    if value is None: return _fail("value (JSON) is required", 422)

    overlap = (
        ConfigModel.query
            .filter(getattr(ConfigModel, CODE_F) == code,
                    getattr(ConfigModel, SCOPE_F) == scope)
            .filter(getattr(ConfigModel, FROM_F) <= (eff_to or date.max))
            .filter(or_(getattr(ConfigModel, TO_F).is_(None), getattr(ConfigModel, TO_F) >= eff_from))
            .first()
    )
    if overlap:
        return _fail("Overlapping config period for the same code/scope", 409)

    fields = {}
    fields.update(_set_kw(CODE_F, code))
    fields.update(_set_kw(SCOPE_F, scope))
    fields.update(_set_kw(VALUE_F, value))
    fields.update(_set_kw(FROM_F, eff_from))
    fields.update(_set_kw(TO_F, eff_to))
    if ACTIVE_F: fields.update(_set_kw(ACTIVE_F, bool(j.get("is_active", True))))

    rec = ConfigModel(**fields)
    db.session.add(rec)
    db.session.commit()

    return _ok({
        "id": rec.id,
        "code": _get(rec, CODE_F),
        "scope": _get(rec, SCOPE_F),
        "value": _get(rec, VALUE_F),
        "effective_from": _get(rec, FROM_F).isoformat() if _get(rec, FROM_F) else None,
        "effective_to": _get(rec, TO_F).isoformat() if _get(rec, TO_F) else None,
        "is_active": _get(rec, ACTIVE_F) if ACTIVE_F else True,
    }, 201)

@bp.put("/stat-config/<int:config_id>/close")
@requires_perms("payroll.compliance.write")
def stat_config_close(config_id: int):
    cur = ConfigModel.query.get_or_404(config_id)
    j = request.get_json(silent=True) or {}
    new_from = _d(j.get("new_from"))
    if not new_from: return _fail("new_from is required (YYYY-MM-DD)", 422)
    cur_from = _get(cur, FROM_F)
    if cur_from and new_from <= cur_from:
        return _fail("new_from must be after current effective_from", 422)

    setattr(cur, TO_F, new_from - timedelta(days=1))

    if "new_value" in j:
        new_scope = j.get("new_scope", _get(cur, SCOPE_F))
        # guard overlap
        ov = _cfg_query(_get(cur, CODE_F), new_from, new_scope).filter(ConfigModel.id != cur.id).first()
        if ov: return _fail("Overlapping config period for the same code/scope", 409)
        fields = {}
        fields.update(_set_kw(CODE_F, _get(cur, CODE_F)))
        fields.update(_set_kw(SCOPE_F, new_scope))
        fields.update(_set_kw(VALUE_F, j.get("new_value")))
        fields.update(_set_kw(FROM_F, new_from))
        fields.update(_set_kw(TO_F, None))
        if ACTIVE_F: fields.update(_set_kw(ACTIVE_F, bool(j.get("new_is_active", True))))
        db.session.add(ConfigModel(**fields))

    db.session.add(cur)
    db.session.commit()
    return _ok({"closed_id": cur.id})

# ---------- PF/ESI/PT/LWF compute ----------
def _pick_basic_from_components(components: List[Dict[str, Any]], wage_tag: str = "BASIC") -> Decimal:
    for c in components or []:
        if (c.get("code") or "").upper() == wage_tag.upper():
            return _dec(c.get("amount")) or Decimal("0")
    s = Decimal("0")
    for c in components or []:
        s += _dec(c.get("amount")) or Decimal("0")
    return s

def _calc_pf(item: PayRunItem, on: date, scope="IN") -> Tuple[Decimal, Decimal]:
    cfg_emp = _cfg_get("PF_RATE_EMP", on, scope)
    cfg_er  = _cfg_get("PF_RATE_ER",  on, scope)
    if not cfg_emp or not cfg_er:
        raise KeyError("PF config missing: PF_RATE_EMP and PF_RATE_ER")
    rate_emp = Decimal(str(cfg_emp.get("rate", 0)))
    rate_er  = Decimal(str(cfg_er.get("rate", 0)))
    cap = Decimal(str(cfg_emp.get("wage_cap", 0))) if cfg_emp.get("wage_cap") else None
    tag = (cfg_emp.get("wage_tag") or "BASIC").upper()
    basic = _pick_basic_from_components(getattr(item, "components", None), tag)
    pf_wage = min(basic, cap) if cap else basic
    return (pf_wage * rate_emp).quantize(Decimal("0.01")), (pf_wage * rate_er).quantize(Decimal("0.01"))

def _calc_esi(item: PayRunItem, on: date, scope="IN") -> Tuple[Decimal, Decimal]:
    cfg = _cfg_get("ESI_RATE", on, scope)
    if not cfg: raise KeyError("ESI config missing: ESI_RATE")
    emp_r = Decimal(str(cfg.get("emp", 0)))
    er_r  = Decimal(str(cfg.get("er", 0)))
    thr   = Decimal(str(cfg.get("threshold", 0))) if cfg.get("threshold") else None
    gross = Decimal(str(getattr(item, "gross", 0) or 0))
    if thr and gross > thr: return Decimal("0.00"), Decimal("0.00")
    return (gross * emp_r).quantize(Decimal("0.01")), (gross * er_r).quantize(Decimal("0.01"))

def _calc_pt_mh(item: PayRunItem, on: date) -> Decimal:
    slabs = _cfg_get("MH_PT_SLABS", on, "MH")
    if not slabs: raise KeyError("PT config missing: MH_PT_SLABS")
    gross = Decimal(str(getattr(item, "gross", 0) or 0))
    for slab in slabs:
        mn = Decimal(str(slab.get("min", 0)))
        mx = Decimal(str(slab.get("max", 10**12)))
        amt = Decimal(str(slab.get("amount", 0)))
        if mn <= gross <= mx:
            return amt
    return Decimal("0.00")

def _calc_lwf_mh(item: PayRunItem, on: date) -> Tuple[Decimal, Decimal]:
    cfg = _cfg_get("LWF_MH", on, "MH")
    if not cfg: raise KeyError("LWF config missing: LWF_MH")
    for row in cfg:
        m = int(row.get("month", 0))
        if m == on.month or not m:
            return Decimal(str(row.get("emp", 0))), Decimal(str(row.get("er", 0)))
    return Decimal("0.00"), Decimal("0.00")

def _compose_components(base: List[Dict[str, Any]], adds: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return list(base or []) + list(adds or [])

@bp.get("/preview")
@requires_perms("payroll.compliance.read")
def preview_run():
    run_id = request.args.get("run_id")
    if not run_id: return _fail("run_id is required", 422)
    state = (request.args.get("state") or "MH").upper()

    r = PayRun.query.get_or_404(int(run_id))
    snap = r.period_end or r.period_start

    items = PayRunItem.query.filter_by(pay_run_id=r.id).all()
    if not items: return _fail("Run has no items. Calculate the run first.", 422)

    result = []
    totals = {"pf_emp": Decimal("0"), "pf_er": Decimal("0"),
              "esi_emp": Decimal("0"), "esi_er": Decimal("0"),
              "pt": Decimal("0"), "lwf_emp": Decimal("0"), "lwf_er": Decimal("0")}
    missing: List[str] = []

    for it in items:
        comp_add: List[Dict[str, Any]] = []
        try:
            pf_emp, pf_er = _calc_pf(it, snap, "IN")
            if pf_emp or pf_er:
                comp_add += [{"code":"PF_EMP","amount":_as_float(pf_emp)}, {"code":"PF_ER","amount":_as_float(pf_er)}]
                totals["pf_emp"] += pf_emp; totals["pf_er"] += pf_er
        except KeyError:
            if "PF" not in missing: missing.append("PF")
        try:
            esi_emp, esi_er = _calc_esi(it, snap, "IN")
            if esi_emp or esi_er:
                comp_add += [{"code":"ESI_EMP","amount":_as_float(esi_emp)}, {"code":"ESI_ER","amount":_as_float(esi_er)}]
                totals["esi_emp"] += esi_emp; totals["esi_er"] += esi_er
        except KeyError:
            if "ESI" not in missing: missing.append("ESI")
        try:
            if state == "MH":
                pt = _calc_pt_mh(it, snap)
                if pt: comp_add.append({"code":"PT_MH","amount":_as_float(pt)}); totals["pt"] += pt
        except KeyError:
            if "PT" not in missing: missing.append("PT")
        try:
            if state == "MH":
                lwf_emp, lwf_er = _calc_lwf_mh(it, snap)
                if lwf_emp or lwf_er:
                    comp_add += [{"code":"LWF_EMP","amount":_as_float(lwf_emp)}, {"code":"LWF_ER","amount":_as_float(lwf_er)}]
                    totals["lwf_emp"] += lwf_emp; totals["lwf_er"] += lwf_er
        except KeyError:
            if "LWF" not in missing: missing.append("LWF")

        gross = Decimal(str(getattr(it, "gross", 0) or 0))
        emp_deductions = Decimal("0")
        for c in comp_add:
            if c["code"] in ("PF_EMP","ESI_EMP","PT_MH","LWF_EMP"):
                emp_deductions += Decimal(str(c["amount"]))
        preview_net = (gross - emp_deductions).quantize(Decimal("0.01"))

        result.append({
            "item_id": it.id,
            "employee_id": getattr(it, "employee_id", None),
            "add_components": comp_add,
            "gross": _as_float(gross),
            "preview_net": _as_float(preview_net),
        })

    out = {"items": result, "totals": {k:_as_float(v) for k,v in totals.items()}, "missing_config": missing or None}
    if missing:
        return _fail("Missing statutory config. See 'extra.missing_config' to seed required entries.", 422, extra=out)
    return _ok(out)

@bp.post("/apply")
@requires_perms("payroll.compliance.write")
def apply_to_run():
    j = request.get_json(silent=True) or {}
    run_id = j.get("run_id")
    if not run_id: return _fail("run_id is required", 422)

    r = PayRun.query.get_or_404(int(run_id))
    if r.status not in ("calculated", "approved"):
        return _fail(f"Run in status '{r.status}' cannot apply compliance. Recalculate/approve first.", 409)

    # reuse preview logic
    with bp.test_request_context(query_string={"run_id": str(run_id), "state": j.get("state", "MH")}):
        resp, status = preview_run()
    if status != 200: return resp, status
    prev = resp.get_json()["data"]

    applied = 0
    for it_prev in prev["items"]:
        it = PayRunItem.query.get(it_prev["item_id"])
        if not it: continue
        it.components = _compose_components(getattr(it, "components", None) or [], it_prev["add_components"])
        gross = Decimal(str(getattr(it, "gross", 0) or 0))
        emp_deductions = Decimal("0")
        for c in it_prev["add_components"]:
            if c["code"] in ("PF_EMP","ESI_EMP","PT_MH","LWF_EMP"):
                emp_deductions += Decimal(str(c["amount"]))
        it.net = _as_float((gross - emp_deductions).quantize(Decimal("0.01")))
        applied += 1

    if r.status == "calculated":
        r.status = "approved"
        if hasattr(r, "approved_at"):
            r.approved_at = datetime.utcnow()

    db.session.commit()
    return _ok({"items_updated": applied, "run": {"id": r.id, "status": r.status}})
