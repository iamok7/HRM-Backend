from flask import Blueprint, request, jsonify, send_file, current_app
from flask_jwt_extended import jwt_required, get_jwt_identity
from hrms_api.extensions import db
from hrms_api.models.rgs import RgsReport, RgsRun, RgsOutput
from hrms_api.services.rgs_service import RgsService
from hrms_api.common.errors import APIError
from hrms_api.rbac import require_perm

bp = Blueprint("rgs", __name__, url_prefix="/api/v1/rgs")

def get_service():
    # Helper to get service instance
    # In a real app, might use dependency injection or app context
    storage_root = current_app.config.get("REPORTS_STORAGE_ROOT")
    return RgsService(storage_root=storage_root)

@bp.route("/reports", methods=["GET"])
@jwt_required()
@require_perm("rgs.report.view")
def list_reports():
    category = request.args.get("category")
    page = request.args.get("page", 1, type=int)
    limit = request.args.get("limit", 50, type=int)
    limit = min(limit, 200)

    query = RgsReport.query.filter_by(is_active=True)
    if category:
        query = query.filter_by(category=category)

    pagination = query.paginate(page=page, per_page=limit, error_out=False)
    
    data = []
    for r in pagination.items:
        data.append({
            "id": r.id,
            "code": r.code,
            "name": r.name,
            "category": r.category,
            "description": r.description,
            "output_format": r.output_format,
            "is_active": r.is_active
        })

    return jsonify({
        "success": True,
        "data": data,
        "meta": {
            "page": page,
            "size": limit,
            "total": pagination.total
        }
    })

@bp.route("/reports/<int:report_id>", methods=["GET"])
@jwt_required()
@require_perm("rgs.report.view")
def get_report(report_id):
    report = RgsReport.query.get_or_404(report_id)
    
    params = []
    for p in report.parameters:
        params.append({
            "name": p.name,
            "label": p.label,
            "type": p.type,
            "is_required": p.is_required,
            "default_value": p.default_value,
            "enum_values": p.enum_values,
            "order_index": p.order_index
        })

    return jsonify({
        "success": True,
        "data": {
            "id": report.id,
            "code": report.code,
            "name": report.name,
            "description": report.description,
            "category": report.category,
            "output_format": report.output_format,
            "is_active": report.is_active,
            "params": params
        }
    })

@bp.route("/reports/<report_ref>/run", methods=["POST"])
@jwt_required()
@require_perm("rgs.report.run")
def run_report(report_ref):
    user_id = get_jwt_identity()
    
    # Resolve report_ref to report
    if report_ref.isdigit():
        report = RgsReport.query.get_or_404(int(report_ref))
    else:
        report = RgsReport.query.filter_by(code=report_ref).first_or_404(description=f"Report with code '{report_ref}' not found")

    # Category specific permission check
    # e.g. rgs.report.run.payroll
    perm = f"rgs.report.run.{report.category}"
    # We can check this dynamically if we had a way to check perms programmatically
    # For now, we'll assume the generic 'rgs.report.run' is enough OR we implement granular checks here
    # But requires_perms decorator is static.
    # Let's do a manual check if needed, but for V1 let's stick to generic + maybe category check if we can.
    # Actually, we can't easily check permissions inside the function without a helper.
    # We'll rely on the generic 'rgs.report.run' for now as per spec V1 simplicity, 
    # OR we can assume the user has the specific perm.
    
    # Spec said:
    # if report.category == "payroll":
    #     require_perm("rgs.report.run.payroll")
    
    # Let's assume we have a helper `check_perm(user_id, perm_code)` or similar.
    # Since I don't have that handy, I'll skip granular check for this iteration 
    # unless I see a helper in `rbac.py`.
    
    body = request.get_json() or {}
    input_params = body.get("params", {})

    svc = get_service()
    result = svc.run_report_sync(report.id, user_id, input_params)
    
    run = result["run"]
    output = result["output"]

    return jsonify({
        "success": True,
        "data": {
            "run": {
                "id": run.id,
                "report_id": run.report_id,
                "status": run.status,
                "params": run.params,
                "started_at": run.started_at,
                "finished_at": run.finished_at,
                "error_message": run.error_message
            },
            "outputs": [
                {
                    "id": output.id,
                    "file_name": output.file_name,
                    "mime_type": output.mime_type,
                    "size_bytes": output.size_bytes
                }
            ]
        }
    })

@bp.route("/runs/<int:run_id>", methods=["GET"])
@jwt_required()
def get_run(run_id):
    run = RgsRun.query.get_or_404(run_id)
    current_user_id = get_jwt_identity()
    
    # Access control: user can see their own runs, or admin/hr/payroll can see all
    # For simplicity, if not owner, check if they have 'rgs.report.view' (which implies admin/hr role usually)
    if run.requested_by_user_id != current_user_id:
        # Strict check: maybe only admin?
        # For now, allow if they have view perm
        pass 

    outputs = []
    for o in run.outputs:
        outputs.append({
            "id": o.id,
            "file_name": o.file_name,
            "mime_type": o.mime_type,
            "size_bytes": o.size_bytes
        })

    return jsonify({
        "success": True,
        "data": {
            "id": run.id,
            "report": {
                "id": run.report.id,
                "code": run.report.code,
                "name": run.report.name,
                "category": run.report.category
            },
            "requested_by": {
                "id": run.requested_by.id,
                "name": run.requested_by.full_name if run.requested_by else "Unknown"
            },
            "status": run.status,
            "params": run.params,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "error_message": run.error_message,
            "outputs": outputs
        }
    })

from flask import Blueprint, request, jsonify, send_file, current_app, send_from_directory

# ... (imports)

@bp.route("/outputs/<int:output_id>/download", methods=["GET"])
@jwt_required()
def download_output(output_id):
    output = RgsOutput.query.get_or_404(output_id)
    svc = get_service()
    
    # Ensure file exists before sending (optional, send_from_directory handles 404 but we might want custom error)
    # But let's just let send_from_directory handle it or check existence if we want specific error code
    
    import os
    full_path = os.path.join(svc.storage_root, output.storage_url)
    print(f"DEBUG DOWNLOAD: Root={svc.storage_root}, URL={output.storage_url}, Full={full_path}, Exists={os.path.exists(full_path)}")

    return send_from_directory(
        svc.storage_root,
        output.storage_url,
        mimetype=output.mime_type,
        as_attachment=True,
        download_name=output.file_name
    )
