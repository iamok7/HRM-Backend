from datetime import datetime, date
from flask import Blueprint, request, jsonify, current_app
from flask_jwt_extended import jwt_required, get_jwt_identity
from sqlalchemy import and_, or_, desc
from hrms_api.extensions import db
from hrms_api.common.auth import requires_perms

from hrms_api.models.employee import Employee
from hrms_api.models.leave import LeaveType, EmployeeLeaveBalance, LeaveRequest, LeaveApprovalAction, CompOffCredit
from hrms_api.models.user import User

bp = Blueprint("leave", __name__, url_prefix="/api/v1/leave")

def _ok(data=None, status=200): return jsonify({"success": True, "data": data}), status
def _fail(msg, status=400, code=None): return jsonify({"success": False, "error": {"message": msg, "code": code}}), status

def _parse_date(s):
    if not s: return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y"):
        try: return datetime.strptime(s, fmt).date()
        except Exception: pass
    return None

def _get_current_year():
    return datetime.utcnow().year

# ---------- Leave Types ----------
@bp.get("/types")
@jwt_required()
def list_types():
    items = LeaveType.query.filter_by(is_active=True).order_by(LeaveType.code.asc()).all()
    return _ok([{
        "id": t.id, "code": t.code, "name": t.name,
        "is_paid": t.is_paid, "is_comp_off": t.is_comp_off,
        "allow_half_day": t.allow_half_day,
        "requires_document": t.requires_document
    } for t in items])

# ---------- Balances ----------
@bp.get("/balances")
@jwt_required()
def get_balances():
    emp_id = request.args.get("employee_id", type=int)
    year = request.args.get("year", type=int) or _get_current_year()
    
    if not emp_id:
        # If not provided, try to infer from current user if employee
        user_id = get_jwt_identity()
        # This assumes we can resolve user to employee. 
        # For now, require employee_id explicitly or implement resolution logic.
        # Let's require it for simplicity or check if user is employee.
        return _fail("employee_id is required", 422)

    rows = (EmployeeLeaveBalance.query
            .filter_by(employee_id=emp_id, year=year)
            .join(LeaveType)
            .order_by(LeaveType.code.asc())
            .all())
    
    data = []
    for r in rows:
        lt = LeaveType.query.get(r.leave_type_id)
        data.append({
            "leave_type": {"id": lt.id, "code": lt.code, "name": lt.name},
            "year": r.year,
            "opening": float(r.opening_balance),
            "accrued": float(r.accrued),
            "used": float(r.used),
            "adjusted": float(r.adjusted),
            "available": r.available
        })
    return _ok(data)

# ---------- Requests ----------
@bp.post("/requests")
@jwt_required()
def apply_leave():
    d = request.get_json(silent=True) or {}
    user_id = get_jwt_identity()
    
    # Validate required fields
    req_fields = ("employee_id", "leave_type_id", "start_date", "end_date", "reason")
    if any(k not in d for k in req_fields):
        return _fail("Missing required fields", 422)

    emp_id = d["employee_id"]
    lt_id = d["leave_type_id"]
    sd = _parse_date(d["start_date"])
    ed = _parse_date(d["end_date"])
    reason = d["reason"]
    is_half_day = bool(d.get("is_half_day", False))
    
    if not (sd and ed): return _fail("Invalid dates", 422)
    if sd > ed: return _fail("Start date cannot be after end date", 422)
    
    # Calculate days
    days = (ed - sd).days + 1
    if is_half_day:
        if days != 1: return _fail("Half day is only allowed for single day leaves", 422)
        total_days = 0.5
    else:
        total_days = float(days)

    # Check leave type
    lt = LeaveType.query.get(lt_id)
    if not lt or not lt.is_active: return _fail("Invalid leave type", 404)
    
    if is_half_day and not lt.allow_half_day:
        return _fail("Half day not allowed for this leave type", 422)

    # Check balance (if not comp-off, or handle comp-off logic)
    # For v1, we just check balance.
    year = sd.year
    bal = EmployeeLeaveBalance.query.filter_by(employee_id=emp_id, leave_type_id=lt_id, year=year).first()
    
    # If no balance record, assume 0 available
    available = bal.available if bal else 0.0
    
    # TODO: Check if negative balance allowed? Schema removed that flag.
    # Assuming strict check for now unless configured otherwise.
    if available < total_days:
        return _fail(f"Insufficient balance. Available: {available}, Requested: {total_days}", 422)

    # Create request
    lr = LeaveRequest(
        employee_id=emp_id,
        leave_type_id=lt_id,
        company_id=1, # TODO: Resolve from employee
        start_date=sd,
        end_date=ed,
        is_half_day=is_half_day,
        total_days=total_days,
        reason=reason,
        status="pending",
        applied_by_user_id=int(user_id) if str(user_id).isdigit() else None
    )
    
    # Resolve company_id from employee
    emp = Employee.query.get(emp_id)
    if emp:
        lr.company_id = emp.company_id
        
    db.session.add(lr)
    db.session.commit()
    
    return _ok({"id": lr.id, "status": lr.status, "message": "Leave request submitted"})

@bp.get("/requests")
@jwt_required()
def list_requests():
    emp_id = request.args.get("employee_id", type=int)
    status = request.args.get("status")
    
    q = LeaveRequest.query
    if emp_id:
        q = q.filter(LeaveRequest.employee_id == emp_id)
    if status:
        q = q.filter(LeaveRequest.status == status)
        
    items = q.order_by(LeaveRequest.created_at.desc()).all()
    
    data = []
    for r in items:
        data.append({
            "id": r.id,
            "employee_id": r.employee_id,
            "leave_type": r.leave_type.name if r.leave_type else None,
            "start_date": r.start_date.isoformat(),
            "end_date": r.end_date.isoformat(),
            "total_days": float(r.total_days),
            "status": r.status,
            "reason": r.reason,
            "created_at": r.created_at.isoformat()
        })
    return _ok(data)

@bp.post("/requests/<int:rid>/approve")
@jwt_required()
@requires_perms("leave.request.approve")
def approve_request(rid):
    user_id = get_jwt_identity()
    lr = LeaveRequest.query.get_or_404(rid)
    
    if lr.status != "pending":
        return _fail(f"Cannot approve request in '{lr.status}' status", 409)
        
    # Deduct balance
    year = lr.start_date.year
    bal = EmployeeLeaveBalance.query.filter_by(employee_id=lr.employee_id, leave_type_id=lr.leave_type_id, year=year).first()
    
    if not bal:
        # Create balance record if missing (shouldn't happen if we checked at apply, but race conditions)
        bal = EmployeeLeaveBalance(
            employee_id=lr.employee_id,
            leave_type_id=lr.leave_type_id,
            year=year,
            opening_balance=0,
            accrued=0,
            used=0,
            adjusted=0
        )
        db.session.add(bal)
    
    # Check balance again
    if bal.available < float(lr.total_days):
        return _fail("Insufficient balance to approve", 409)
        
    bal.used = float(bal.used) + float(lr.total_days)
    bal.updated_at = datetime.utcnow()
    
    lr.status = "approved"
    lr.approved_by_user_id = int(user_id) if str(user_id).isdigit() else None
    lr.updated_at = datetime.utcnow()
    
    # Log action
    action = LeaveApprovalAction(
        leave_request_id=lr.id,
        action="approve",
        acted_by_user_id=lr.approved_by_user_id,
        acted_at=datetime.utcnow()
    )
    db.session.add(action)
    
    db.session.commit()
    return _ok({"id": lr.id, "status": "approved"})

@bp.post("/requests/<int:rid>/reject")
@jwt_required()
@requires_perms("leave.request.approve")
def reject_request(rid):
    user_id = get_jwt_identity()
    d = request.get_json(silent=True) or {}
    reason = d.get("reason")
    
    lr = LeaveRequest.query.get_or_404(rid)
    
    if lr.status != "pending":
        return _fail(f"Cannot reject request in '{lr.status}' status", 409)
        
    lr.status = "rejected"
    lr.rejection_reason = reason
    lr.updated_at = datetime.utcnow()
    
    # Log action
    action = LeaveApprovalAction(
        leave_request_id=lr.id,
        action="reject",
        comment=reason,
        acted_by_user_id=int(user_id) if str(user_id).isdigit() else None,
        acted_at=datetime.utcnow()
    )
    db.session.add(action)
    
    db.session.commit()
    return _ok({"id": lr.id, "status": "rejected"})

@bp.post("/requests/<int:rid>/cancel")
@jwt_required()
def cancel_request(rid):
    user_id = get_jwt_identity()
    lr = LeaveRequest.query.get_or_404(rid)
    
    # Only owner can cancel? Or admin?
    # Assuming owner for now.
    # TODO: Verify user_id matches employee's user_id
    
    if lr.status not in ("pending", "approved"):
        return _fail(f"Cannot cancel request in '{lr.status}' status", 409)
        
    if lr.status == "approved":
        # Refund balance
        year = lr.start_date.year
        bal = EmployeeLeaveBalance.query.filter_by(employee_id=lr.employee_id, leave_type_id=lr.leave_type_id, year=year).first()
        if bal:
            bal.used = float(bal.used) - float(lr.total_days)
            bal.updated_at = datetime.utcnow()
            
    lr.status = "cancelled"
    lr.updated_at = datetime.utcnow()
    
    db.session.commit()
    return _ok({"id": lr.id, "status": "cancelled"})

# ---------- Comp-Off Credits ----------
@bp.get("/comp-off/credits")
@jwt_required()
def list_comp_off_credits():
    emp_id = request.args.get("employee_id", type=int)
    if not emp_id: return _fail("employee_id is required", 422)
    
    items = CompOffCredit.query.filter_by(employee_id=emp_id).order_by(CompOffCredit.date_earned.desc()).all()
    return _ok([{
        "id": c.id,
        "date_earned": c.date_earned.isoformat(),
        "hours_or_days": float(c.hours_or_days),
        "status": c.status,
        "reason": c.reason
    } for c in items])

@bp.post("/comp-off/credits")
@jwt_required()
@requires_perms("leave.comp_off.manage")
def grant_comp_off_credit():
    d = request.get_json(silent=True) or {}
    emp_id = d.get("employee_id")
    date_earned = _parse_date(d.get("date_earned"))
    amount = d.get("amount") # hours or days
    reason = d.get("reason")
    
    if not (emp_id and date_earned and amount):
        return _fail("employee_id, date_earned, amount are required", 422)
        
    emp = Employee.query.get(emp_id)
    if not emp: return _fail("Employee not found", 404)
    
    credit = CompOffCredit(
        employee_id=emp_id,
        company_id=emp.company_id,
        date_earned=date_earned,
        hours_or_days=float(amount),
        reason=reason,
        status="approved" # Direct grant
    )
    db.session.add(credit)
    
    # Also add to leave balance?
    # Spec says: "Comp-Off Accrual: Manual (HR/System via API)"
    # And "Approved comp-off credit adds to the 'Comp-Off' leave balance."
    # So we need to find the 'Comp-Off' leave type and add to accrued.
    
    co_type = LeaveType.query.filter_by(company_id=emp.company_id, is_comp_off=True).first()
    if co_type:
        year = date_earned.year
        bal = EmployeeLeaveBalance.query.filter_by(employee_id=emp_id, leave_type_id=co_type.id, year=year).first()
        if not bal:
            bal = EmployeeLeaveBalance(
                employee_id=emp_id,
                leave_type_id=co_type.id,
                year=year,
                opening_balance=0,
                accrued=0,
                used=0,
                adjusted=0
            )
            db.session.add(bal)
        
        bal.accrued = float(bal.accrued) + float(amount) # Assuming amount is in days if leave type is days
        # TODO: Handle unit conversion if needed.
        
    db.session.commit()
    return _ok({"id": credit.id, "status": "approved"})
