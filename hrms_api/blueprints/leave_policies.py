from flask import Blueprint, request, jsonify
from hrms_api.extensions import db
from hrms_api.common.auth import requires_perms
from hrms_api.models.leave import LeavePolicy, LeaveType
from hrms_api.models.master import Grade
from hrms_api.services.leave_policy_service import sync_balances_for_company_year
from datetime import datetime

bp = Blueprint("leave_policies", __name__, url_prefix="/api/v1/leave/policies")

def _ok(data=None, status=200, **meta):
    payload = {"success": True, "data": data}
    if meta: payload["meta"] = meta
    return jsonify(payload), status

def _fail(msg, status=400): return jsonify({"success": False, "error": {"message": msg}}), status

@bp.get("")
@requires_perms("leave.policies.view")
def list_policies():
    try:
        cid = int(request.args.get("company_id"))
    except (TypeError, ValueError):
        return _fail("company_id is required")
        
    year = request.args.get("year", type=int) or datetime.utcnow().year
    lt_id = request.args.get("leave_type_id", type=int)
    grade_id = request.args.get("grade_id", type=int)
    
    q = LeavePolicy.query.filter_by(company_id=cid, year=year, is_active=True)
    
    if lt_id:
        q = q.filter_by(leave_type_id=lt_id)
    if grade_id:
        q = q.filter_by(grade_id=grade_id)
        
    items = q.all()
    
    data = []
    for p in items:
        data.append({
            "id": p.id,
            "company_id": p.company_id,
            "company_name": p.company.name if p.company else None,
            "leave_type_id": p.leave_type_id,
            "leave_type_code": p.leave_type.code if p.leave_type else None,
            "leave_type_name": p.leave_type.name if p.leave_type else None,
            "grade_id": p.grade_id,
            "grade_name": p.grade.name if p.grade else None,
            "year": p.year,
            "entitlement_per_year": float(p.entitlement_per_year),
            "carry_forward_max": float(p.carry_forward_max) if p.carry_forward_max is not None else None,
            "allow_negative": p.allow_negative,
            "max_negative_balance": float(p.max_negative_balance) if p.max_negative_balance is not None else None,
            "accrual_pattern": p.accrual_pattern,
            "is_active": p.is_active
        })
        
    return _ok({"items": data, "meta": {"total": len(data)}})

@bp.post("")
@requires_perms("leave.policies.manage")
def create_policy():
    d = request.get_json(silent=True) or {}
    
    req = ["company_id", "leave_type_id", "year", "entitlement_per_year"]
    if any(k not in d for k in req):
        return _fail("Missing required fields")
        
    # Validate uniqueness
    exists = LeavePolicy.query.filter_by(
        company_id=d["company_id"],
        leave_type_id=d["leave_type_id"],
        grade_id=d.get("grade_id"), # None if not present
        year=d["year"]
    ).first()
    
    if exists:
        return _fail("Policy already exists for this combination", 409)
        
    # Validation
    if float(d["entitlement_per_year"]) < 0:
        return _fail("Entitlement cannot be negative")
        
    if not d.get("allow_negative", False):
        if d.get("max_negative_balance") and float(d["max_negative_balance"]) > 0:
             return _fail("Max negative balance must be <= 0")

    p = LeavePolicy(
        company_id=d["company_id"],
        leave_type_id=d["leave_type_id"],
        grade_id=d.get("grade_id"),
        year=d["year"],
        entitlement_per_year=d["entitlement_per_year"],
        carry_forward_max=d.get("carry_forward_max"),
        allow_negative=d.get("allow_negative", False),
        max_negative_balance=d.get("max_negative_balance"),
        accrual_pattern=d.get("accrual_pattern", "annual_fixed"),
        is_active=True
    )
    db.session.add(p)
    db.session.commit()
    
    return _ok({"id": p.id}, status=201)

@bp.put("/<int:pid>")
@requires_perms("leave.policies.manage")
def update_policy(pid):
    p = LeavePolicy.query.get_or_404(pid)
    d = request.get_json(silent=True) or {}
    
    if "entitlement_per_year" in d:
        p.entitlement_per_year = d["entitlement_per_year"]
    if "carry_forward_max" in d:
        p.carry_forward_max = d["carry_forward_max"]
    if "allow_negative" in d:
        p.allow_negative = d["allow_negative"]
    if "max_negative_balance" in d:
        p.max_negative_balance = d["max_negative_balance"]
    if "is_active" in d:
        p.is_active = d["is_active"]
        
    db.session.commit()
    return _ok({"id": p.id})

@bp.delete("/<int:pid>")
@requires_perms("leave.policies.manage")
def delete_policy(pid):
    p = LeavePolicy.query.get_or_404(pid)
    p.is_active = False
    db.session.commit()
    return _ok({"id": p.id, "status": "inactive"})

@bp.post("/sync-balances")
@requires_perms("leave.policies.sync_balances")
def sync_balances():
    d = request.get_json(silent=True) or {}
    cid = d.get("company_id")
    year = d.get("year")
    
    if not (cid and year):
        return _fail("company_id and year are required")
        
    res = sync_balances_for_company_year(
        company_id=cid,
        year=year,
        employee_ids=d.get("employee_ids")
    )
    
    return _ok(res)
