from flask import Blueprint, request, jsonify
from datetime import datetime, date, timedelta
from sqlalchemy import func, desc, or_, and_
from hrms_api.extensions import db
from hrms_api.common.auth import requires_perms
from hrms_api.models.employee import Employee
from hrms_api.models.attendance_punch import AttendancePunch
from hrms_api.models.leave import LeaveRequest
from hrms_api.models.payroll.pay_run import PayRun
from hrms_api.models.face_profile import EmployeeFaceProfile
from hrms_api.models.rgs import RgsRun, RgsReport, RgsOutput
from hrms_api.models.master import Company, Location, Department

bp = Blueprint("hr_dashboard", __name__, url_prefix="/api/v1/hr")

def _ok(data=None, status=200, **meta):
    payload = {"success": True, "data": data}
    if meta:
        payload["meta"] = meta
    return jsonify(payload), status

def _fail(msg, status=400):
    return jsonify({"success": False, "error": {"message": msg}}), status

@bp.route("/dashboard", methods=["GET"])
@requires_perms("hr.dashboard.view")
def get_dashboard():
    """
    Get HR/Admin Dashboard data.
    """
    try:
        company_id = int(request.args.get("company_id"))
    except (TypeError, ValueError):
        return _fail("company_id is required")
        
    loc_id = request.args.get("location_id", type=int)
    date_str = request.args.get("date")
    
    if date_str:
        try:
            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return _fail("Invalid date format YYYY-MM-DD")
    else:
        target_date = date.today()
        
    # 1. Context
    comp = Company.query.get(company_id)
    loc_name = None
    if loc_id:
        loc = Location.query.get(loc_id)
        loc_name = loc.name if loc else None
        
    context = {
        "company_id": company_id,
        "company_name": comp.name if comp else None,
        "location_id": loc_id,
        "location_name": loc_name,
        "date": target_date.isoformat(),
        "generated_at": datetime.utcnow().isoformat()
    }
    
    # 2. Headcount
    emp_q = Employee.query.filter_by(company_id=company_id)
    if loc_id:
        emp_q = emp_q.filter_by(location_id=loc_id)
        
    total_emps = emp_q.count()
    active_emps = emp_q.filter_by(status="active").count()
    inactive_emps = total_emps - active_emps
    
    month_start = date(target_date.year, target_date.month, 1)
    # new joins
    new_joins = emp_q.filter(Employee.doj >= month_start, Employee.doj <= target_date).count()
    # exits
    exits = emp_q.filter(Employee.dol >= month_start, Employee.dol <= target_date).count()
    
    # by emp type
    by_type = db.session.query(Employee.employment_type, func.count(Employee.id)).filter(
        Employee.company_id == company_id
    )
    if loc_id:
        by_type = by_type.filter(Employee.location_id == loc_id)
    by_type = by_type.group_by(Employee.employment_type).all()
    
    # by location (if no loc filter)
    by_loc = []
    if not loc_id:
        loc_counts = db.session.query(Location.id, Location.name, func.count(Employee.id)).join(
            Employee, Employee.location_id == Location.id
        ).filter(Employee.company_id == company_id).group_by(Location.id, Location.name).all()
        for lid, lname, c in loc_counts:
            by_loc.append({"location_id": lid, "location_name": lname, "count": c})
            
    headcount = {
        "total_employees": total_emps,
        "active_employees": active_emps,
        "inactive_employees": inactive_emps,
        "new_joins_this_month": new_joins,
        "exits_this_month": exits,
        "by_employment_type": [{"employment_type": t, "count": c} for t, c in by_type],
        "by_location": by_loc
    }
    
    # 3. Today Attendance
    # Simplified logic: Count distinct employees with punches today
    # Ideally needs shift logic for 'absent', 'not_in_yet' etc.
    # For MVP, we'll approximate using punches.
    
    punch_q = AttendancePunch.query.filter(
        AttendancePunch.company_id == company_id,
        func.date(AttendancePunch.ts) == target_date
    )
    if loc_id:
        # Punch location might be null or different, strictly we should filter by employee location
        # But let's join employee
        punch_q = punch_q.join(Employee).filter(Employee.location_id == loc_id)
        
    present_count = punch_q.with_entities(AttendancePunch.employee_id).distinct().count()
    
    # On Leave
    leave_q = LeaveRequest.query.join(Employee).filter(
        Employee.company_id == company_id,
        LeaveRequest.status == 'approved',
        LeaveRequest.start_date <= target_date,
        LeaveRequest.end_date >= target_date
    )
    if loc_id:
        leave_q = leave_q.filter(Employee.location_id == loc_id)
    on_leave_count = leave_q.count()
    
    # Absent/Not In Yet approximation
    # absent = active - present - on_leave (ignoring WO/Holiday for now)
    absent_approx = active_emps - present_count - on_leave_count
    if absent_approx < 0: absent_approx = 0
    
    today_att = {
        "date": target_date.isoformat(),
        "summary": {
            "present": present_count,
            "not_in_yet": 0, # Hard to compute without shift
            "on_leave": on_leave_count,
            "weekly_off": 0, # TODO
            "holiday": 0, # TODO
            "absent": absent_approx,
            "missing_punch": 0 # TODO
        },
        "by_department": [], # TODO
        "late_arrivals": {"count": 0, "threshold_minutes": 10}
    }
    
    # 4. Face Attendance
    face_enrolled = EmployeeFaceProfile.query.join(Employee).filter(
        Employee.company_id == company_id,
        EmployeeFaceProfile.is_active == True
    )
    if loc_id:
        face_enrolled = face_enrolled.filter(Employee.location_id == loc_id)
    enrolled_count = face_enrolled.count()
    
    face_punches = punch_q.filter(AttendancePunch.method == 'face').count()
    bio_punches = punch_q.filter(AttendancePunch.method == 'machine').count() # Assuming machine=biometric
    
    face_att = {
        "enrolled_employees": enrolled_count,
        "not_enrolled_employees": active_emps - enrolled_count,
        "enrollment_coverage_percent": round((enrolled_count / active_emps * 100), 1) if active_emps else 0,
        "today_face_punches": face_punches,
        "today_biometric_punches": bio_punches,
        "today_manual_entries": 0 # TODO
    }
    
    # 5. Leave & Exceptions
    pending_leaves = LeaveRequest.query.join(Employee).filter(
        Employee.company_id == company_id,
        LeaveRequest.status == 'pending'
    )
    if loc_id:
        pending_leaves = pending_leaves.filter(Employee.location_id == loc_id)
        
    pending_leave_count = pending_leaves.count()
    pending_leave_sample = []
    for r in pending_leaves.order_by(LeaveRequest.created_at.desc()).limit(5).all():
        pending_leave_sample.append({
            "id": r.id,
            "employee_id": r.employee_id,
            "emp_code": r.employee.code,
            "employee_name": f"{r.employee.first_name} {r.employee.last_name}",
            "leave_type_code": r.leave_type.code,
            "start_date": r.start_date.isoformat(),
            "end_date": r.end_date.isoformat(),
            "total_days": float(r.total_days),
            "applied_on": r.created_at.isoformat()
        })
        
    leave_exc = {
        "pending_leave_requests_count": pending_leave_count,
        "pending_leave_requests_sample": pending_leave_sample,
        "pending_attendance_exceptions_count": 0,
        "pending_attendance_exceptions_sample": []
    }
    
    # 6. Payroll
    pay_run = PayRun.query.filter(
        PayRun.company_id == company_id,
        func.extract('year', PayRun.period_start) == target_date.year,
        func.extract('month', PayRun.period_start) == target_date.month
    ).first()
    
    payroll = {
        "year": target_date.year,
        "month": target_date.month,
        "pay_run_id": pay_run.id if pay_run else None,
        "status": pay_run.status if pay_run else "not_created",
        "processed_employees": 0, # TODO: Count items
        "expected_employees": active_emps,
        "total_gross_pay": "0.00",
        "total_net_pay": "0.00",
        "last_run_at": pay_run.updated_at.isoformat() if pay_run else None
    }
    
    # 7. Trends (Mock for now or simple agg)
    trends = {
        "attendance_last_7_days": [],
        "headcount_last_6_months": []
    }
    
    # 8. Latest Reports
    reports = {}
    for code in ["ATTENDANCE_MONTHLY", "PAYROLL_REGISTER"]:
        run = RgsRun.query.join(RgsReport).filter(
            RgsReport.code == code,
            RgsRun.status == 'SUCCESS',
            # RgsRun.params['company_id'] == company_id # JSON filtering might be tricky in pure SQLA without cast
        ).order_by(RgsRun.started_at.desc()).first()
        
        # Check params manually if needed, or assume latest is relevant
        if run:
            out = RgsOutput.query.filter_by(run_id=run.id).first()
            reports[code.lower()] = {
                "last_run_at": run.started_at.isoformat() if run.started_at else None,
                "last_run_id": run.id,
                "last_output_file_name": out.file_name if out else None,
                "download_url": f"/api/v1/rgs/outputs/{out.id}/download" if out else None
            }
            
    return _ok({
        "context": context,
        "headcount": headcount,
        "today_attendance": today_att,
        "face_attendance": face_att,
        "leave_and_exceptions": leave_exc,
        "payroll": payroll,
        "trends": trends,
        "latest_reports": reports
    })
