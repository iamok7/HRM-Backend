from flask import Blueprint, request, jsonify, make_response
from sqlalchemy import extract
from hrms_api.extensions import db
from hrms_api.models.payroll.pay_run import PayRun, PayRunItem
from hrms_api.services.payslip_service import PayslipService
from hrms_api.blueprints.auth_decorators import token_required

self_service_bp = Blueprint("self_service", __name__, url_prefix="/api/v1/self")
svc = PayslipService()

@self_service_bp.route("/payslips", methods=["GET"])
@token_required
def list_own_payslips(current_user):
    """
    List payslips for the logged-in employee.
    """
    if not current_user.employee_id:
        return jsonify({"success": False, "error": "User is not linked to an employee record"}), 403
        
    # Optional filters
    year = request.args.get("year")
    month = request.args.get("month")
    
    query = PayRunItem.query.filter_by(employee_id=current_user.employee_id)
    
    # To filter by year/month, we need to join with PayRun
    query = query.join(PayRun)
    
    if year:
        try:
            query = query.filter(extract('year', PayRun.period_start) == int(year))
        except ValueError:
            pass # Ignore invalid year
        
    if month:
        try:
            query = query.filter(extract('month', PayRun.period_start) == int(month))
        except ValueError:
            pass # Ignore invalid month
        
    # Order by latest first
    query = query.order_by(PayRun.period_start.desc())
    
    # Pagination
    page = int(request.args.get("page", 1))
    limit = int(request.args.get("limit", 12)) # Default to 1 year
    
    pagination = query.paginate(page=page, per_page=limit, error_out=False)
    
    items_data = []
    for item in pagination.items:
        run = item.pay_run
        items_data.append({
            "pay_run_id": run.id,
            "year": run.period_start.year,
            "month": run.period_start.month,
            "net_pay": float(item.net or 0),
            "status": run.status,
            "company_id": run.company_id,
            "company_name": run.company.name
        })
        
    return jsonify({
        "success": True,
        "data": {
            "items": items_data,
            "meta": {
                "page": page,
                "size": limit,
                "total": pagination.total
            }
        }
    })

@self_service_bp.route("/payslips/download", methods=["GET"])
@token_required
def download_own_payslip(current_user):
    """
    Download own payslip.
    """
    if not current_user.employee_id:
        return jsonify({"success": False, "error": "User is not linked to an employee record"}), 403
        
    year = request.args.get("year")
    month = request.args.get("month")
    fmt = request.args.get("format", "html")
    
    if not all([year, month]):
        return jsonify({"success": False, "error": "Missing required params: year, month"}), 400
        
    try:
        year = int(year)
        month = int(month)
    except ValueError:
        return jsonify({"success": False, "error": "Invalid params: year, month must be integers"}), 400
        
    # Find Item directly via Join
    item = PayRunItem.query.join(PayRun).filter(
        PayRunItem.employee_id == current_user.employee_id,
        extract('year', PayRun.period_start) == year,
        extract('month', PayRun.period_start) == month
    ).first()
    
    if not item:
        return jsonify({"success": False, "error": "Payslip not found for this period"}), 404
        
    dto = svc.build_payslip_dto(item)
    
    if fmt == "pdf":
        return jsonify({"success": False, "error": "PDF generation not implemented yet. Use format=html"}), 501
    else:
        html_content = svc.render_payslip_html(dto)
        response = make_response(html_content)
        response.headers["Content-Type"] = "text/html"
        filename = f"PAYSLIP_{dto['employee']['code']}_{year}_{month:02d}.html"
        response.headers["Content-Disposition"] = f"attachment; filename={filename}"
        return response
        filename = f"PAYSLIP_{dto['employee']['code']}_{year}_{month:02d}.html"
        response.headers["Content-Disposition"] = f"attachment; filename={filename}"
        return response

@self_service_bp.route("/dashboard", methods=["GET"])
@token_required
def get_dashboard(current_user):
    """
    Get Employee Dashboard (ESS) data.
    """
    if not current_user.employee_id:
        return jsonify({"success": False, "error": "User is not linked to an employee record"}), 403
    
    from datetime import datetime, date, timedelta
    from sqlalchemy import func, desc, or_
    from hrms_api.models.employee import Employee
    from hrms_api.models.attendance_punch import AttendancePunch
    from hrms_api.models.attendance_rollup import AttendanceRollup
    from hrms_api.models.leave import EmployeeLeaveBalance, LeaveType, LeaveRequest
    from hrms_api.models.face_profile import EmployeeFaceProfile
    from hrms_api.models.attendance import Holiday, Shift
    from hrms_api.models.attendance_assignment import EmployeeShiftAssignment
    
    emp_id = current_user.employee_id
    emp = Employee.query.get(emp_id)
    if not emp:
        return jsonify({"success": False, "error": "Employee not found"}), 404
        
    today = date.today()
    
    # 1. Profile
    profile = {
        "employee_id": emp.id,
        "emp_code": emp.code,
        "name": f"{emp.first_name} {emp.last_name or ''}".strip(),
        "company_id": emp.company_id,
        "company_name": emp.company.name if emp.company else None,
        "department": emp.department.name if emp.department else None,
        "designation": emp.designation.name if emp.designation else None,
        "location": emp.location.name if emp.location else None,
        "grade": emp.grade.name if emp.grade else None,
        "date_of_joining": emp.doj.isoformat() if emp.doj else None,
        "employment_type": emp.employment_type,
        "status": emp.status
    }
    
    # 2. Today Attendance
    # Fetch shift
    shift_q = (
        db.session.query(EmployeeShiftAssignment, Shift)
        .join(Shift, EmployeeShiftAssignment.shift_id == Shift.id)
        .filter(
            EmployeeShiftAssignment.employee_id == emp_id,
            or_(
                EmployeeShiftAssignment.end_date.is_(None),
                EmployeeShiftAssignment.end_date >= today,
            ),
            EmployeeShiftAssignment.start_date <= today,
        )
        .order_by(EmployeeShiftAssignment.start_date.desc())
        .first()
    )
    
    shift_data = None
    if shift_q:
        _, s = shift_q
        shift_data = {
            "code": s.code,
            "name": s.name,
            "start_time": s.start_time.strftime("%H:%M") if s.start_time else None,
            "end_time": s.end_time.strftime("%H:%M") if s.end_time else None
        }

    # Fetch punches
    punches = AttendancePunch.query.filter(
        AttendancePunch.employee_id == emp_id,
        func.date(AttendancePunch.ts) == today
    ).order_by(AttendancePunch.ts.asc()).all()
    
    first_in = next((p for p in punches if p.direction == 'in'), None)
    last_out = next((p for p in reversed(punches) if p.direction == 'out'), None)
    
    # Check Leave/Holiday/WO (Simplified for now, ideally reuse calendar logic)
    # For now, just check punches
    status = "absent"
    if first_in:
        status = "present"
        if not last_out and datetime.now().time() > (s.end_time if shift_data and s.end_time else datetime.max.time()):
             # Simple missing punch logic
             pass 
    elif datetime.now().time() < (s.start_time if shift_data and s.start_time else datetime.min.time()):
        status = "not_in_yet"
        
    today_att = {
        "date": today.isoformat(),
        "shift": shift_data,
        "status": status,
        "check_in_time": first_in.ts.strftime("%H:%M") if first_in else None,
        "check_in_source": first_in.method if first_in else None,
        "check_out_time": last_out.ts.strftime("%H:%M") if last_out else None,
        "check_out_source": last_out.method if last_out else None,
        "late_by_minutes": 0, # TODO: Compute
        "early_exit_minutes": 0, # TODO: Compute
        "is_weekly_off": False, # TODO: Check rules
        "is_holiday": False, # TODO: Check holiday
        "on_approved_leave": False, # TODO: Check leave
        "has_missing_punch": False # TODO: Compute
    }
    
    # 3. Month Attendance
    rollup = AttendanceRollup.query.filter_by(
        employee_id=emp_id,
        year=today.year,
        month=today.month
    ).first()
    
    month_att = {
        "year": today.year,
        "month": today.month,
        "from_date": date(today.year, today.month, 1).isoformat(),
        "to_date": today.isoformat(), # Or end of month
        "present_days": rollup.present_days if rollup else 0,
        "leave_days": rollup.leave_days if rollup else 0,
        "weekly_off_days": rollup.weekly_off_days if rollup else 0,
        "holiday_days": rollup.holiday_days if rollup else 0,
        "absent_days": rollup.absent_days if rollup else 0,
        "lop_days": rollup.lop_days if rollup else 0,
        "ot_hours": rollup.ot_hours if rollup else 0,
        "late_marks": 0,
        "half_days": 0
    }
    
    # 4. Leave Balances
    balances = EmployeeLeaveBalance.query.filter_by(
        employee_id=emp_id,
        year=today.year
    ).join(LeaveType).all()
    
    bal_list = []
    for b in balances:
        avail = float(b.opening_balance) + float(b.accrued) + float(b.adjusted) - float(b.used)
        bal_list.append({
            "leave_type_id": b.leave_type_id,
            "leave_type_code": b.leave_type.code,
            "leave_type_name": b.leave_type.name,
            "year": b.year,
            "opening_balance": float(b.opening_balance),
            "accrued": float(b.accrued),
            "used": float(b.used),
            "adjusted": float(b.adjusted),
            "available": avail,
            "is_comp_off": b.leave_type.is_comp_off,
            "is_paid": b.leave_type.is_paid
        })
        
    # 5. Recent Leave Requests
    reqs = LeaveRequest.query.filter_by(employee_id=emp_id).order_by(LeaveRequest.created_at.desc()).limit(5).all()
    req_list = []
    for r in reqs:
        req_list.append({
            "id": r.id,
            "leave_type_code": r.leave_type.code,
            "leave_type_name": r.leave_type.name,
            "start_date": r.start_date.isoformat(),
            "end_date": r.end_date.isoformat(),
            "is_half_day": r.is_half_day,
            "total_days": float(r.total_days),
            "status": r.status,
            "applied_on": r.created_at.isoformat(),
            "approved_on": r.updated_at.isoformat() if r.status == 'approved' else None
        })
        
    # 6. Recent Payslips
    # Reusing PayRunItem query logic
    payslips = PayRunItem.query.join(PayRun).filter(
        PayRunItem.employee_id == emp_id
    ).order_by(PayRun.period_start.desc()).limit(3).all()
    
    ps_list = []
    for p in payslips:
        run = p.pay_run
        ps_list.append({
            "year": run.period_start.year,
            "month": run.period_start.month,
            "pay_run_id": run.id,
            "status": run.status,
            "net_pay": str(p.net),
            "download_url": f"/api/v1/self/payslips/download?year={run.period_start.year}&month={run.period_start.month}"
        })
        
    # 7. Face Profile
    face_prof = EmployeeFaceProfile.query.filter_by(employee_id=emp_id, is_active=True).first()
    face_punches = AttendancePunch.query.filter(
        AttendancePunch.employee_id == emp_id,
        AttendancePunch.method == 'face',
        func.date(AttendancePunch.ts) == today
    ).count()
    
    face_data = {
        "is_enrolled": bool(face_prof),
        "enrolled_images_count": 1 if face_prof else 0, # Simplified
        "last_enrolled_at": face_prof.created_at.isoformat() if face_prof else None,
        "last_self_punch_at": None, # TODO
        "last_device_id": None # TODO
    }
    
    # 8. Upcoming Holidays
    hols = Holiday.query.filter(
        Holiday.company_id == emp.company_id,
        Holiday.date >= today,
        or_(Holiday.location_id == emp.location_id, Holiday.location_id.is_(None))
    ).order_by(Holiday.date.asc()).limit(3).all()
    
    hol_list = []
    for h in hols:
        hol_list.append({
            "date": h.date.isoformat(),
            "name": h.name,
            "location": h.location.name if h.location else "All"
        })
        
    return jsonify({
        "success": True,
        "data": {
            "profile": profile,
            "today_attendance": today_att,
            "month_attendance": month_att,
            "leave_balances": bal_list,
            "recent_leave_requests": req_list,
            "recent_payslips": ps_list,
            "face_profile": face_data,
            "upcoming_holidays": hol_list
        }
    })
