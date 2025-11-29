from hrms_api.extensions import db
from hrms_api.models.leave import LeavePolicy, EmployeeLeaveBalance, LeaveType
from hrms_api.models.employee import Employee
from sqlalchemy import and_

def get_effective_leave_policy(company_id, employee, leave_type_id, year):
    """
    1) Try exact match: company + leave_type + employee.grade_id + year
    2) Fallback: company + leave_type + grade_id=NULL + year
    3) If none found -> return None
    """
    # 1. Grade specific
    if employee.grade_id:
        policy = LeavePolicy.query.filter_by(
            company_id=company_id,
            leave_type_id=leave_type_id,
            grade_id=employee.grade_id,
            year=year,
            is_active=True
        ).first()
        if policy:
            return policy

    # 2. Company default (grade_id is NULL)
    policy = LeavePolicy.query.filter(
        LeavePolicy.company_id == company_id,
        LeavePolicy.leave_type_id == leave_type_id,
        LeavePolicy.grade_id.is_(None),
        LeavePolicy.year == year,
        LeavePolicy.is_active == True
    ).first()
    
    return policy

def ensure_balances_for_employee_year(company_id, employee, year):
    """
    For the given employee & year, and for every active LeaveType in that company:
    - Find effective LeavePolicy (grade-level then company-level).
    - If no policy -> skip that leave type.
    - If EmployeeLeaveBalance row does NOT exist -> create with:
        opening_balance = policy.entitlement_per_year  (for accrual_pattern='annual_fixed')
        accrued = 0
        used = 0
        adjusted = 0
    - If row already exists:
        - DO NOT override used.
        - Optionally, if HR edits the policy later, we can:
            - If opening_balance == 0 and used == 0 -> set opening_balance.
            - BUT to keep it safe in v1, you can just leave existing rows unchanged.
    """
    # Get all active leave types for company
    leave_types = LeaveType.query.filter_by(company_id=company_id, is_active=True).all()
    
    created_count = 0
    for lt in leave_types:
        policy = get_effective_leave_policy(company_id, employee, lt.id, year)
        if not policy:
            continue
            
        # Check if balance exists
        bal = EmployeeLeaveBalance.query.filter_by(
            employee_id=employee.id,
            leave_type_id=lt.id,
            year=year
        ).first()
        
        if not bal:
            # Create new balance
            opening = 0
            if policy.accrual_pattern == "annual_fixed":
                opening = policy.entitlement_per_year
                
            bal = EmployeeLeaveBalance(
                employee_id=employee.id,
                leave_type_id=lt.id,
                year=year,
                opening_balance=opening,
                accrued=0,
                used=0,
                adjusted=0
            )
            db.session.add(bal)
            created_count += 1
            
    return created_count

def sync_balances_for_company_year(company_id, year, employee_ids=None):
    """
    - Query all active employees in that company (or only given employee_ids).
    - For each employee, call ensure_balances_for_employee_year.
    """
    q = Employee.query.filter_by(company_id=company_id, status="active")
    
    if employee_ids:
        q = q.filter(Employee.id.in_(employee_ids))
        
    employees = q.all()
    
    total_created = 0
    processed_employees = 0
    
    for emp in employees:
        created = ensure_balances_for_employee_year(company_id, emp, year)
        total_created += created
        processed_employees += 1
        
    db.session.commit()
    
    return {
        "company_id": company_id,
        "year": year,
        "employees_processed": processed_employees,
        "balances_created": total_created
    }
