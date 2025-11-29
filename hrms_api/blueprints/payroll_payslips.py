from flask import Blueprint, request, jsonify, make_response, render_template
from hrms_api.extensions import db
from hrms_api.models.payroll.pay_run import PayRun, PayRunItem
from hrms_api.models.employee import Employee
from hrms_api.services.payslip_service import PayslipService
from hrms_api.services.payroll_common import get_pay_run_for_period
from hrms_api.blueprints.auth_decorators import token_required, role_required, permission_required

payroll_payslips_bp = Blueprint("payroll_payslips", __name__, url_prefix="/api/v1/payroll/payslips")
svc = PayslipService()

@payroll_payslips_bp.route("", methods=["GET"])
@token_required
@permission_required("payroll.payslips.view")
def list_payslips(current_user):
    """
    List payslips for a given run (company, year, month).
    """
    company_id = request.args.get("company_id")
    year = request.args.get("year")
    month = request.args.get("month")
    
    if not all([company_id, year, month]):
        return jsonify({"success": False, "error": "Missing required params: company_id, year, month"}), 400
        
    try:
        company_id = int(company_id)
        year = int(year)
        month = int(month)
    except ValueError:
        return jsonify({"success": False, "error": "Invalid params: company_id, year, month must be integers"}), 400

    target_run = get_pay_run_for_period(company_id, year, month)
            
    if not target_run:
        return jsonify({"success": False, "error": "No pay run found for this period"}), 404
        
    # Pagination
    page = int(request.args.get("page", 1))
    limit = int(request.args.get("limit", 50))
    
    # Fetch Items
    query = PayRunItem.query.filter_by(pay_run_id=target_run.id)
    
    # Optional employee filter
    emp_id = request.args.get("employee_id")
    if emp_id:
        query = query.filter_by(employee_id=emp_id)
        
    pagination = query.paginate(page=page, per_page=limit, error_out=False)
    
    items_data = []
    for item in pagination.items:
        emp = item.employee
        dept = emp.department
        desig = emp.designation
        
        items_data.append({
            "employee_id": emp.id,
            "emp_code": emp.code,
            "employee_name": f"{emp.first_name} {emp.last_name or ''}".strip(),
            "department": dept.name if dept else None,
            "designation": desig.name if desig else None,
            "net_pay": float(item.net or 0),
            "gross_pay": float(item.gross or 0),
            "days_worked": float(item.calc_meta.get("days_worked", 0)) if isinstance(item.calc_meta, dict) else 0
        })
        
    return jsonify({
        "success": True,
        "data": {
            "run": {
                "pay_run_id": target_run.id,
                "year": target_run.period_start.year,
                "month": target_run.period_start.month,
                "status": target_run.status,
                "company_id": target_run.company_id,
                "company_name": target_run.company.name
            },
            "items": items_data,
            "meta": {
                "page": page,
                "size": limit,
                "total": pagination.total
            }
        }
    })

@payroll_payslips_bp.route("/<int:employee_id>", methods=["GET"])
@token_required
@permission_required("payroll.payslips.view")
def get_payslip_dto(current_user, employee_id):
    """
    Get single payslip JSON DTO.
    """
    company_id = request.args.get("company_id")
    year = request.args.get("year")
    month = request.args.get("month")
    
    if not all([company_id, year, month]):
        return jsonify({"success": False, "error": "Missing required params: company_id, year, month"}), 400
        
    try:
        company_id = int(company_id)
        year = int(year)
        month = int(month)
    except ValueError:
        return jsonify({"success": False, "error": "Invalid params: company_id, year, month must be integers"}), 400

    target_run = get_pay_run_for_period(company_id, year, month)
            
    if not target_run:
        return jsonify({"success": False, "error": "No pay run found for this period"}), 404
        
    item = PayRunItem.query.filter_by(pay_run_id=target_run.id, employee_id=employee_id).first()
    if not item:
        return jsonify({"success": False, "error": "Payslip not found for this employee in this run"}), 404
        
    dto = svc.build_payslip_dto(item)
    return jsonify(dto)

@payroll_payslips_bp.route("/<int:employee_id>/download", methods=["GET"])
@token_required
@permission_required("payroll.payslips.download")
def download_payslip(current_user, employee_id):
    """
    Download payslip as HTML (or PDF if implemented).
    """
    company_id = request.args.get("company_id")
    year = request.args.get("year")
    month = request.args.get("month")
    fmt = request.args.get("format", "html")
    
    if not all([company_id, year, month]):
        return jsonify({"success": False, "error": "Missing required params: company_id, year, month"}), 400
        
    try:
        company_id = int(company_id)
        year = int(year)
        month = int(month)
    except ValueError:
        return jsonify({"success": False, "error": "Invalid params: company_id, year, month must be integers"}), 400

    target_run = get_pay_run_for_period(company_id, year, month)
            
    if not target_run:
        return jsonify({"success": False, "error": "No pay run found for this period"}), 404
        
    item = PayRunItem.query.filter_by(pay_run_id=target_run.id, employee_id=employee_id).first()
    if not item:
        return jsonify({"success": False, "error": "Payslip not found for this employee in this run"}), 404
        
    dto = svc.build_payslip_dto(item)
    
    if fmt == "pdf":
        # Placeholder for PDF generation
        return jsonify({"success": False, "error": "PDF generation not implemented yet. Use format=html"}), 501
    else:
        html_content = svc.render_payslip_html(dto)
        response = make_response(html_content)
        response.headers["Content-Type"] = "text/html"
        filename = f"PAYSLIP_{dto['employee']['code']}_{year}_{month:02d}.html"
        response.headers["Content-Disposition"] = f"attachment; filename={filename}"
        return response
