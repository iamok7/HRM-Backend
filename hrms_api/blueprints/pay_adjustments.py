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

def _row(a: Adjustment) -> Dict[str, Any]:
    return {
        "id": a.id,
        "employee_id": a.employee_id,
        "adj_date": a.adj_date.isoformat() if getattr(a, "adj_date", None) else None,
        "code": a.code,
        "amount": _flt(getattr(a, "amount", None)),
        "kind": getattr(a, "kind", None),           # earning | deduction
        "status": getattr(a, "status", None),       # draft|approved|applied|void
        "narration": getattr(a, "narration", None),
        "created_at": getattr(a, "created_at", None).isoformat() if getattr(a, "created_at", None) else None,
    }

# ---------- CRUD ----------
@bp.post("")
@requires_perms("payroll.adjustments.write")
def create_adjustment():
    from flask import request, jsonify
    j = request.get_json(force=True) or {}
    # allowed fields as per your model:
    allowed = {"employee_id", "kind", "adj_date", "amount", "narration", "status"}
    payload = {k: j.get(k) for k in allowed}

    # if user sends 'code', keep it as a tag in narration (optional)
    if j.get("code"):
        note = payload.get("narration") or ""
        payload["narration"] = (note + f" [code:{j['code']}]").strip()

    if not payload.get("employee_id") or not payload.get("kind") or not payload.get("adj_date") or payload.get("amount") is None:
        return jsonify({"success": False, "error": {"message": "employee_id, kind, adj_date, amount required"}}), 400

    rec = Adjustment(**payload)
    db.session.add(rec)
    db.session.commit()
    return jsonify({"success": True, "data": {"id": rec.id}}), 201


@bp.get("")
@requires_perms("payroll.adjust.read")
def list_adjustments():
    q = Adjustment.query
    if request.args.get("employee_id"):
        try: q = q.filter(Adjustment.employee_id == int(request.args["employee_id"]))
        except Exception: return _fail("employee_id must be integer", 422)
    if request.args.get("status"):
        q = q.filter(Adjustment.status == request.args["status"])
    if request.args.get("code"):
        q = q.filter(Adjustment.code == request.args["code"])
    if request.args.get("from"):
        d = _d(request.args["from"]); 
        if not d: return _fail("from must be YYYY-MM-DD", 422)
        q = q.filter(Adjustment.adj_date >= d)
    if request.args.get("to"):
        d = _d(request.args["to"]); 
        if not d: return _fail("to must be YYYY-MM-DD", 422)
        q = q.filter(Adjustment.adj_date <= d)
    q = q.order_by(Adjustment.created_at.desc() if hasattr(Adjustment,"created_at") else Adjustment.id.desc())
    rows = q.all()
    return _ok([_row(x) for x in rows])

@bp.patch("/<int:adj_id>")
@requires_perms("payroll.adjust.write")
def patch_adjustment(adj_id: int):
    a = Adjustment.query.get_or_404(adj_id)
    if a.status not in ("draft","approved"):
        return _fail(f"cannot edit in status '{a.status}'", 409)
    j = request.get_json(silent=True) or {}

    if "code" in j: a.code = (j.get("code") or "").strip().upper() or a.code
    if "kind" in j:
        k = (j.get("kind") or "").strip().lower()
        if k in ("earning","deduction"): a.kind = k
        else: return _fail("kind must be 'earning' or 'deduction'", 422)
    if "adj_date" in j:
        d = _d(j.get("adj_date")); 
        if not d: return _fail("adj_date must be YYYY-MM-DD", 422)
        a.adj_date = d
    if "amount" in j:
        amt = _dec(j.get("amount")); 
        if amt is None or amt == Decimal("0"): return _fail("amount required and non-zero", 422)
        a.amount = amt
    if "narration" in j:
        a.narration = (j.get("narration") or "").strip() or None
    db.session.commit()
    return _ok(_row(a))

@bp.post("/<int:adj_id>/approve")
@requires_perms("payroll.adjust.write")
def approve_adjustment(adj_id: int):
    a = Adjustment.query.get_or_404(adj_id)
    if a.status not in ("draft",):
        return _fail(f"only 'draft' can be approved; current={a.status}", 409)
    a.status = "approved"
    db.session.commit()
    return _ok(_row(a))

@bp.post("/<int:adj_id>/void")
@requires_perms("payroll.adjust.write")
def void_adjustment(adj_id: int):
    a = Adjustment.query.get_or_404(adj_id)
    if a.status in ("applied","void"):
        return _fail(f"cannot void adjustment in status '{a.status}'", 409)
    a.status = "void"
    db.session.commit()
    return _ok(_row(a))

@bp.delete("/<int:adj_id>")
@requires_perms("payroll.adjust.write")
def delete_adjustment(adj_id: int):
    a = Adjustment.query.get_or_404(adj_id)
    if a.status in ("applied",):
        return _fail("cannot delete an applied adjustment", 409)
    db.session.delete(a); db.session.commit()
    return _ok({"deleted": adj_id})

# ---------- Apply into a Pay Run ----------
def _merge_components(base: List[Dict[str, Any]] | None, adds: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return list(base or []) + list(adds or [])

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

    # pull approved adjustments for employees included in the run, within window
    adjs = (Adjustment.query
            .filter(Adjustment.status == "approved")
            .filter(Adjustment.employee_id.in_(list(items.keys())))
            .filter(Adjustment.adj_date.between(d_from, d_to))
            .all())
    if not adjs:
        return _ok({"items_updated": 0, "adjustments_used": 0})

    used = 0
    updated = 0
    for a in adjs:
        itm = items.get(a.employee_id)
        if not itm: 
            continue

        # base components
        comps = getattr(itm, "components", None) or []

        # component line
        comp_line = {"code": f"ADJ_{a.code}", "amount": _flt(a.amount)}
        comps2 = _merge_components(comps, [comp_line])
        itm.components = comps2

        # gross & net: earnings add to gross+net; deductions reduce net
        gross = Decimal(str(getattr(itm, "gross", 0) or 0))
        net   = Decimal(str(getattr(itm, "net", 0)   or 0))
        amt   = Decimal(str(a.amount))

        if a.kind == "earning":
            gross = (gross + amt).quantize(Decimal("0.01"))
            net   = (net   + amt).quantize(Decimal("0.01"))
        else:  # deduction
            net   = (net   - amt).quantize(Decimal("0.01"))

        itm.gross = _flt(gross)
        itm.net   = _flt(net)

        a.status = "applied"
        used += 1; updated += 1
        db.session.add(itm); db.session.add(a)

    db.session.commit()
    return _ok({"items_updated": updated, "adjustments_used": used, "run_id": r.id})
