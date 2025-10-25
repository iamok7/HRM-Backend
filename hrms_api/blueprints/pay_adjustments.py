from __future__ import annotations
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from flask import Blueprint, request, jsonify
from hrms_api.extensions import db
from hrms_api.common.auth import requires_perms

# ⬇️ adapt to your actual model module/path
# Expected fields: id, employee_id, adj_date, code, amount, kind('earning'|'deduction'),
# status('draft'|'approved'|'applied'|'void'), narration, created_at
from hrms_api.models.payroll.adjustments import Adjustment
from hrms_api.models.payroll.pay_run import PayRun, PayRunItem
from sqlalchemy.sql.sqltypes import JSON as _JSON

bp = Blueprint("pay_adjustments", __name__, url_prefix="/api/v1/adjustments")

# ---------- helpers ----------
def _ok(data=None, status=200, **meta):
    payload = {"success": True, "data": data}
    if meta: payload["meta"] = meta
    return jsonify(payload), status

def _fail(message, status=400, code=None, extra=None):
    payload = {"success": False, "error": {"message": message}}
    if code: payload["error"]["code"] = code
    if extra: payload["error"]["extra"] = extra
    return jsonify(payload), status

def _d(s) -> Optional[date]:
    if not s: return None
    try: return date.fromisoformat(str(s))
    except Exception: return None

def _dec(x) -> Optional[Decimal]:
    if x is None or x == "": return None
    try: return Decimal(str(x))
    except Exception: return None

def _flt(x):
    try: return float(x) if x is not None else None
    except Exception: return None

def _meta(a: Adjustment) -> Dict[str, Any]:
    return dict(getattr(a, "meta_json", None) or {})

def _meta_get(a: Adjustment, k: str, default=None):
    return _meta(a).get(k, default)

def _meta_set(a: Adjustment, **updates):
    m = _meta(a)
    m.update({k: v for k, v in updates.items() if v is not None})
    a.meta_json = m

def _status(a: Adjustment) -> str:
    return str(_meta_get(a, "status", "draft") or "draft")

def _code(a: Adjustment) -> Optional[str]:
    c = _meta_get(a, "code")
    return c.upper() if isinstance(c, str) and c else None

def _kind(a: Adjustment) -> Optional[str]:
    k = _meta_get(a, "kind")
    if isinstance(k, str):
        k = k.lower().strip()
        if k in ("earning", "deduction"):
            return k
    return None

def _adj_date(a: Adjustment):
    s = _meta_get(a, "adj_date")
    try:
        return date.fromisoformat(str(s)) if s else None
    except Exception:
        return None

def _row(a: Adjustment) -> Dict[str, Any]:
    return {
        "id": a.id,
        "employee_id": a.employee_id,
        "adj_date": _adj_date(a).isoformat() if _adj_date(a) else None,
        "code": _code(a),
        "amount": _flt(getattr(a, "amount", None)),
        "kind": _kind(a),            # earning | deduction (from meta)
        "status": _status(a),        # draft|approved|applied|void (in meta)
        "narration": getattr(a, "reason", None),
        "period": getattr(a, "period", None),
        "type": getattr(a, "type", None),          # incentive|bonus|recovery|arrear
        "created_at": getattr(a, "created_at", None).isoformat() if getattr(a, "created_at", None) else None,
    }

# ---------- CRUD ----------
@bp.post("")
@requires_perms("payroll.adjustments.write")
def create_adjustment():
    from flask import request, jsonify
    j = request.get_json(force=True) or {}

    emp_id = j.get("employee_id")
    kind   = (j.get("kind") or "").strip().lower()
    adj_d  = _d(j.get("adj_date"))
    amt    = _dec(j.get("amount"))
    narr   = (j.get("narration") or j.get("reason") or "").strip() or None
    code   = (j.get("code") or "").strip().upper() or None
    status_in = (j.get("status") or "draft").strip().lower()
    if status_in not in ("draft", "approved", "applied", "void"):
        status_in = "draft"

    if not emp_id or not kind or not adj_d or amt is None:
        return jsonify({"success": False, "error": {"message": "employee_id, kind, adj_date, amount required"}}), 400

    # Map kind -> model.type
    adj_type = "bonus" if kind == "earning" else "recovery"
    period = f"{adj_d.year:04d}-{adj_d.month:02d}"

    rec = Adjustment(
        employee_id=int(emp_id),
        period=period,
        type=adj_type,
        amount=amt,
        reason=narr,
    )
    _meta_set(rec, code=code, kind=kind, adj_date=adj_d.isoformat(), status=status_in)

    db.session.add(rec)
    db.session.commit()
    return jsonify({"success": True, "data": {"id": rec.id}}), 201


@bp.get("")
@requires_perms("payroll.adjust.read")
def list_adjustments():
    q = Adjustment.query
    eid = request.args.get("employee_id")
    if eid:
        try:
            q = q.filter(Adjustment.employee_id == int(eid))
        except Exception:
            return _fail("employee_id must be integer", 422)
    q = q.order_by(Adjustment.created_at.desc() if hasattr(Adjustment,"created_at") else Adjustment.id.desc())
    rows = q.all()

    st = (request.args.get("status") or "").strip().lower()
    code = (request.args.get("code") or "").strip().upper()
    d_from = _d(request.args.get("from")) if request.args.get("from") else None
    d_to   = _d(request.args.get("to")) if request.args.get("to") else None
    out = []
    for a in rows:
        if st and _status(a) != st:
            continue
        if code and (_code(a) or "") != code:
            continue
        ad = _adj_date(a)
        if d_from and (not ad or ad < d_from):
            continue
        if d_to and (not ad or ad > d_to):
            continue
        out.append(a)
    return _ok([_row(x) for x in out])

@bp.patch("/<int:adj_id>")
@requires_perms("payroll.adjust.write")
def patch_adjustment(adj_id: int):
    a = Adjustment.query.get_or_404(adj_id)
    if _status(a) not in ("draft","approved"):
        return _fail(f"cannot edit in status '{_status(a)}'", 409)
    j = request.get_json(silent=True) or {}

    if "code" in j:
        c = (j.get("code") or "").strip().upper() or None
        _meta_set(a, code=c)
    if "kind" in j:
        k = (j.get("kind") or "").strip().lower()
        if k in ("earning","deduction"):
            _meta_set(a, kind=k)
        else:
            return _fail("kind must be 'earning' or 'deduction'", 422)
    if "adj_date" in j:
        d = _d(j.get("adj_date"))
        if not d: return _fail("adj_date must be YYYY-MM-DD", 422)
        _meta_set(a, adj_date=d.isoformat())
        a.period = f"{d.year:04d}-{d.month:02d}"
    if "amount" in j:
        amt = _dec(j.get("amount"))
        if amt is None or amt == Decimal("0"): return _fail("amount required and non-zero", 422)
        a.amount = amt
    if "narration" in j:
        a.reason = (j.get("narration") or "").strip() or None
    db.session.commit()
    return _ok(_row(a))

@bp.post("/<int:adj_id>/approve")
@requires_perms("payroll.adjust.write")
def approve_adjustment(adj_id: int):
    a = Adjustment.query.get_or_404(adj_id)
    if _status(a) not in ("draft",):
        return _fail(f"only 'draft' can be approved; current={_status(a)}", 409)
    _meta_set(a, status="approved")
    db.session.commit()
    return _ok(_row(a))

@bp.post("/<int:adj_id>/void")
@requires_perms("payroll.adjust.write")
def void_adjustment(adj_id: int):
    a = Adjustment.query.get_or_404(adj_id)
    if _status(a) in ("applied","void"):
        return _fail(f"cannot void adjustment in status '{_status(a)}'", 409)
    _meta_set(a, status="void")
    db.session.commit()
    return _ok(_row(a))

@bp.delete("/<int:adj_id>")
@requires_perms("payroll.adjust.write")
def delete_adjustment(adj_id: int):
    a = Adjustment.query.get_or_404(adj_id)
    if _status(a) in ("applied",):
        return _fail("cannot delete an applied adjustment", 409)
    db.session.delete(a); db.session.commit()
    return _ok({"deleted": adj_id})

# ---------- Apply into a Pay Run ----------
def _merge_components(base: List[Dict[str, Any]] | None, adds: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return list(base or []) + list(adds or [])

def _pick_item_components_field() -> Optional[str]:
    cols = list(PayRunItem.__table__.columns.items())
    for name, col in cols:
        nm = name.lower()
        if any(k in nm for k in ("components","component_json","breakup","breakdown")):
            if isinstance(col.type, _JSON):
                return name
    for name, col in cols:
        if isinstance(col.type, _JSON):
            return name
    return None

ITEM_COMPONENTS_F = _pick_item_components_field()

@bp.post("/apply")
@requires_perms("payroll.adjust.write")
def apply_to_run():
    """
    Body:
    {
      "run_id": 123,
      "from": "YYYY-MM-DD",    // optional: defaults to run.period_start
      "to": "YYYY-MM-DD"       // optional: defaults to run.period_end
    }
    Behavior:
      - picks adjustments with status='approved' within [from..to]
      - merges into PayRunItem.components:
          * earning  => add component {code, amount}, add to gross and net
          * deduction=> add component {code, -amount} (kept positive in field), subtract from net
      - marks the used adjustments as 'applied'
    """
    j = request.get_json(silent=True) or {}
    run_id = j.get("run_id")
    if not run_id:
        return _fail("run_id is required", 422)

    r = PayRun.query.get_or_404(int(run_id))
    if r.status not in ("calculated","approved"):
        return _fail(f"Run in status '{r.status}' cannot accept adjustments. Recalculate/approve first.", 409)

    d_from = _d(j.get("from")) or r.period_start
    d_to   = _d(j.get("to"))   or r.period_end
    if not (d_from and d_to):
        return _fail("'from'/'to' fallback to run period, both required eventually", 422)
    if d_to < d_from:
        return _fail("to must be >= from", 422)

    # fetch run items
    items = {i.employee_id: i for i in PayRunItem.query.filter_by(pay_run_id=r.id).all()}
    if not items:
        return _fail("Run has no items. Calculate first.", 422)

    # pull candidate adjustments and filter by meta fields (status, adj_date)
    adjs_all = (Adjustment.query
                .filter(Adjustment.employee_id.in_(list(items.keys())))
                .all())
    adjs: List[Adjustment] = []
    for a in adjs_all:
        if _status(a) != "approved":
            continue
        ad = _adj_date(a)
        if not ad or ad < d_from or ad > d_to:
            continue
        adjs.append(a)
    if not adjs:
        return _ok({"items_updated": 0, "adjustments_used": 0})

    used = 0
    updated = 0
    for a in adjs:
        itm = items.get(a.employee_id)
        if not itm: 
            continue

        # base components
        comps = (getattr(itm, ITEM_COMPONENTS_F, None) if ITEM_COMPONENTS_F else None) or []

        # component line
        code = _code(a) or "GEN"
        comp_line = {"code": f"ADJ_{code}", "amount": _flt(a.amount)}
        comps2 = _merge_components(comps, [comp_line])
        if ITEM_COMPONENTS_F:
            setattr(itm, ITEM_COMPONENTS_F, comps2)

        # gross & net: earnings add to gross+net; deductions reduce net
        gross = Decimal(str(getattr(itm, "gross", 0) or 0))
        net   = Decimal(str(getattr(itm, "net", 0)   or 0))
        amt   = Decimal(str(getattr(a, "amount", 0)))

        k = _kind(a) or "earning"
        if k == "earning":
            gross = (gross + amt).quantize(Decimal("0.01"))
            net   = (net   + amt).quantize(Decimal("0.01"))
        else:  # deduction
            net   = (net   - amt).quantize(Decimal("0.01"))

        itm.gross = _flt(gross)
        itm.net   = _flt(net)

        _meta_set(a, status="applied", applied_to_run_id=r.id)
        used += 1; updated += 1
        db.session.add(itm); db.session.add(a)

    db.session.commit()
    return _ok({"items_updated": updated, "adjustments_used": used, "run_id": r.id})
