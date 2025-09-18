from datetime import datetime, timedelta
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from sqlalchemy import and_
from hrms_api.extensions import db

from hrms_api.models.employee import Employee
from hrms_api.models.leave import LeaveType, LeaveBalance, LeaveRequest


from hrms_api.common.auth import requires_perms
from flask_jwt_extended import jwt_required

bp = Blueprint("leave", __name__, url_prefix="/api/v1/leave")

def _ok(data=None, status=200): return jsonify({"success": True, "data": data}), status
def _fail(msg, status=400): return jsonify({"success": False, "error": {"message": msg}}), status

# ---------- utils ----------
def _parse_date(s):
    if not s: return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y"):
        try: return datetime.strptime(s, fmt).date()
        except Exception: pass
    return None

def _days_between(start, end, part_day=None):
    if start > end: return 0.0
    days = (end - start).days + 1
    if part_day in ("half", "am", "pm"):
        # if start==end and half day ⇒ 0.5, else naive 0.5 off total
        return 0.5 if days == 1 else max(days - 0.5, 0.5)
    return float(days)

def _num(x):
    try: return float(x)
    except Exception: return 0.0

# ---------- Leave Types ----------
@bp.get("/types")
@jwt_required()
def list_types():
    items = (LeaveType.query
             .filter_by(active=True)
             .order_by(LeaveType.code.asc())
             .all())
    return _ok([{
        "id": t.id, "company_id": t.company_id, "code": t.code, "name": t.name,
        "unit": t.unit, "paid": bool(t.paid),
        "accrual_per_month": float(t.accrual_per_month or 0),
        "carry_forward_limit": float(t.carry_forward_limit or 0) if t.carry_forward_limit is not None else None,
        "negative_balance_allowed": bool(t.negative_balance_allowed),
        "requires_approval": bool(t.requires_approval),
        "active": bool(t.active),
        "created_at": t.created_at.isoformat(sep=" ")
    } for t in items])

@bp.post("/types")
@jwt_required()
def create_type():
    d = request.get_json(silent=True) or {}
    req = ("company_id","code","name")
    if any(k not in d for k in req): return _fail("company_id, code, name are required", 422)
    t = LeaveType(
        company_id=d["company_id"], code=d["code"].strip(), name=d["name"].strip(),
        unit=(d.get("unit") or "day"), paid=bool(d.get("paid", True)),
        accrual_per_month=_num(d.get("accrual_per_month", 0)),
        carry_forward_limit=_num(d.get("carry_forward_limit")) if d.get("carry_forward_limit") is not None else None,
        negative_balance_allowed=bool(d.get("negative_balance_allowed", False)),
        requires_approval=bool(d.get("requires_approval", True)),
        active=bool(d.get("active", True)),
    )
    db.session.add(t); db.session.commit()
    return _ok({"id": t.id}, 201)

@bp.put("/types/<int:tid>")
@jwt_required()
def update_type(tid: int):
    t = LeaveType.query.get(tid)
    if not t: return _fail("LeaveType not found", 404)
    d = request.get_json(silent=True) or {}
    for k in ("code","name","unit"):
        if k in d and d[k]: setattr(t, k, d[k])
    for k in ("paid","negative_balance_allowed","requires_approval","active"):
        if k in d: setattr(t, k, bool(d[k]))
    if "accrual_per_month" in d: t.accrual_per_month = _num(d["accrual_per_month"])
    if "carry_forward_limit" in d:
        t.carry_forward_limit = _num(d["carry_forward_limit"]) if d["carry_forward_limit"] is not None else None
    db.session.commit()
    return _ok({"id": t.id, "updated": True})

@bp.delete("/types/<int:tid>")
@jwt_required()
def delete_type(tid: int):
    t = LeaveType.query.get(tid)
    if not t: return _fail("LeaveType not found", 404)
    t.active = False
    db.session.commit()
    return _ok({"id": tid, "archived": True})

# ---------- Balances ----------
@bp.get("/balances")
@jwt_required()
def balances():
    emp_id = request.args.get("employeeId", type=int)
    if not emp_id: return _fail("employeeId is required", 422)
    rows = (LeaveBalance.query
            .filter(LeaveBalance.employee_id == emp_id)
            .order_by(LeaveBalance.leave_type_id.asc())
            .all())
    data = [{
        "id": r.id, "employee_id": r.employee_id, "leave_type_id": r.leave_type_id,
        "balance": float(r.balance or 0), "ytd_accrued": float(r.ytd_accrued or 0),
        "ytd_taken": float(r.ytd_taken or 0), "updated_at": r.updated_at.isoformat(sep=" ")
    } for r in rows]
    return _ok(data)

@bp.post("/balances/adjust")
@jwt_required()
@requires_perms("leave.balance.adjust")
def adjust_balance():
    d = request.get_json(silent=True) or {}
    emp_id = d.get("employee_id")
    lt_id = d.get("leave_type_id")
    if not (emp_id and lt_id): return _fail("employee_id and leave_type_id are required", 422)
    mode = (d.get("mode") or "delta").lower()  # delta | set
    amount = _num(d.get("amount", 0))

    r = LeaveBalance.query.filter_by(employee_id=emp_id, leave_type_id=lt_id).first()
    if not r:
        r = LeaveBalance(employee_id=emp_id, leave_type_id=lt_id, balance=0, ytd_accrued=0, ytd_taken=0)
        db.session.add(r); db.session.flush()

    if mode == "set":
        r.balance = amount
    else:
        r.balance = _num(r.balance) + amount
    r.updated_at = datetime.utcnow()
    db.session.commit()
    return _ok({"id": r.id, "employee_id": emp_id, "leave_type_id": lt_id, "balance": float(r.balance)})

# ---------- Requests (apply / approve / reject) ----------
@bp.get("/requests")
@jwt_required()
def list_requests():
    emp_id = request.args.get("employeeId", type=int)
    status = request.args.get("status")
    q = LeaveRequest.query
    if emp_id: q = q.filter(LeaveRequest.employee_id == emp_id)
    if status: q = q.filter(LeaveRequest.status == status)
    q = q.order_by(LeaveRequest.created_at.desc())
    items = q.all()
    return _ok([{
        "id": r.id, "employee_id": r.employee_id, "leave_type_id": r.leave_type_id,
        "start_date": r.start_date.isoformat(), "end_date": r.end_date.isoformat(),
        "part_day": r.part_day, "days": float(r.days), "status": r.status,
        "reason": r.reason, "approver_id": r.approver_id,
        "approved_at": r.approved_at.isoformat(sep=" ") if r.approved_at else None,
        "created_at": r.created_at.isoformat(sep=" ")
    } for r in items])

@bp.post("/requests")
@jwt_required()
def create_request():
    d = request.get_json(silent=True) or {}
    emp_id = d.get("employee_id")
    lt_id = d.get("leave_type_id")
    sd = _parse_date(d.get("start_date")); ed = _parse_date(d.get("end_date"))
    part = d.get("part_day")
    if not (emp_id and lt_id and sd and ed): return _fail("employee_id, leave_type_id, start_date, end_date required", 422)
    if not Employee.query.get(emp_id): return _fail("Employee not found", 404)
    if not LeaveType.query.get(lt_id): return _fail("Leave type not found", 404)

    days = _days_between(sd, ed, part)
    r = LeaveRequest(employee_id=emp_id, leave_type_id=lt_id, start_date=sd, end_date=ed,
                     part_day=part, days=days, status="draft", reason=d.get("reason"))
    db.session.add(r); db.session.commit()
    return _ok({"id": r.id, "days": float(days), "status": r.status}, 201)

@bp.post("/requests/<int:rid>/submit")
@jwt_required()
def submit_request(rid: int):
    r = LeaveRequest.query.get(rid)
    if not r: return _fail("Leave request not found", 404)
    if r.status not in ("draft","rejected"): return _fail(f"Cannot submit from status {r.status}", 409)
    r.status = "pending"
    db.session.commit()
    return _ok({"id": r.id, "status": r.status})

@bp.post("/requests/<int:rid>/approve")
@requires_perms("leave.request.approve")
@jwt_required()
def approve_request(rid: int):
    d = request.get_json(silent=True) or {}
    approver_id = d.get("approver_id")  # optional for now
    r = LeaveRequest.query.get(rid)
    if not r: return _fail("Leave request not found", 404)
    if r.status not in ("pending","draft"): return _fail(f"Cannot approve from status {r.status}", 409)

    bal = LeaveBalance.query.filter_by(employee_id=r.employee_id, leave_type_id=r.leave_type_id).first()
    lt = LeaveType.query.get(r.leave_type_id)
    if not bal:
        bal = LeaveBalance(employee_id=r.employee_id, leave_type_id=r.leave_type_id, balance=0, ytd_accrued=0, ytd_taken=0)
        db.session.add(bal); db.session.flush()

    if not lt.negative_balance_allowed and _num(bal.balance) < _num(r.days):
        return _fail("Insufficient balance", 409)

    # deduct
    bal.balance = _num(bal.balance) - _num(r.days)
    bal.ytd_taken = _num(bal.ytd_taken) + _num(r.days)
    bal.updated_at = datetime.utcnow()

    r.status = "approved"
    r.approver_id = approver_id
    r.approved_at = datetime.utcnow()

    db.session.commit()
    return _ok({
        "id": r.id, "status": r.status, "approved_at": r.approved_at.isoformat(sep=" "),
        "balance_after": float(bal.balance)
    })

@bp.post("/requests/<int:rid>/reject")
@requires_perms("leave.request.approve")
@jwt_required()
def reject_request(rid: int):
    d = request.get_json(silent=True) or {}
    reason = d.get("reason")
    r = LeaveRequest.query.get(rid)
    if not r: return _fail("Leave request not found", 404)
    if r.status not in ("pending","draft"): return _fail(f"Cannot reject from status {r.status}", 409)
    r.status = "rejected"; r.reason = reason or r.reason
    db.session.commit()
    return _ok({"id": r.id, "status": r.status})

@bp.delete("/requests/<int:rid>")
@jwt_required()
def cancel_request(rid: int):
    r = LeaveRequest.query.get(rid)
    if not r: return _fail("Leave request not found", 404)
    if r.status in ("approved","cancelled"): return _fail(f"Cannot cancel from status {r.status}", 409)
    r.status = "cancelled"
    db.session.commit()
    return _ok({"id": r.id, "status": r.status})
