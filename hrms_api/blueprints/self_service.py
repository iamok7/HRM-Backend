from flask import Blueprint, request, jsonify, make_response
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
        # Assuming postgres extract function or similar, but let's do python filter if volume low
        # Or better, use sqlalchemy extract
        from sqlalchemy import extract
        query = query.filter(extract('year', PayRun.period_start) == int(year))
        
    if month:
        from sqlalchemy import extract
        query = query.filter(extract('month', PayRun.period_start) == int(month))
        
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
            "net_pay": float(item.net),
            "status": run.status
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
        
    # Find Item directly via Join
    from sqlalchemy import extract
    item = PayRunItem.query.join(PayRun).filter(
        PayRunItem.employee_id == current_user.employee_id,
        extract('year', PayRun.period_start) == int(year),
        extract('month', PayRun.period_start) == int(month)
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
        filename = f"PAYSLIP_{dto['employee']['code']}_{year}_{month}.html"
        response.headers["Content-Disposition"] = f"attachment; filename={filename}"
        return response
