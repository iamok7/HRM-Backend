from datetime import datetime
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from hrms_api.extensions import db
from hrms_api.services.face_attendance import FaceAttendanceService
from hrms_api.models.face_profile import EmployeeFaceProfile
from hrms_api.models.face_log import FaceAttendanceLog
from hrms_api.common.auth import requires_roles, requires_perms
from hrms_api.models.employee import Employee
from hrms_api.models.user import User

bp = Blueprint("attendance_face", __name__, url_prefix="/api/v1/attendance/face")

def _fail(msg, status=400):
    return jsonify({"success": False, "error": {"message": msg}}), status

def _ok(data=None, **kwargs):
    res = {"success": True}
    if data is not None:
        res["data"] = data
    res.update(kwargs)
    return jsonify(res)

# --- HR Endpoints ---

@bp.post("/enroll")
@requires_roles("admin", "hr") 
def enroll_face():
    # multipart/form-data
    # employee_id, image, label
    if "image" not in request.files:
        return _fail("No image file provided")
    
    f = request.files["image"]
    if not f.filename:
        return _fail("Empty filename")
        
    emp_id = request.form.get("employee_id")
    if not emp_id:
        return _fail("employee_id is required")
        
    label = request.form.get("label")
    
    try:
        res = FaceAttendanceService.enroll_face(int(emp_id), f, label)
        if not res["success"]:
            return _fail(res["error"])
        return _ok(res)
    except Exception as e:
        return _fail(str(e), 500)

@bp.get("/profiles")
@requires_roles("admin", "hr")
def list_profiles():
    emp_id = request.args.get("employee_id", type=int)
    if not emp_id:
        return _fail("employee_id is required")
        
    profiles = EmployeeFaceProfile.query.filter_by(employee_id=emp_id).all()
    data = []
    for p in profiles:
        data.append({
            "id": p.id,
            "image_url": p.image_url,
            "label": p.label,
            "is_active": p.is_active,
            "created_at": p.created_at.isoformat()
        })
    return _ok(data)

@bp.post("/profiles/<int:pid>/deactivate")
@requires_roles("admin", "hr")
def deactivate_profile(pid):
    p = EmployeeFaceProfile.query.get_or_404(pid)
    p.is_active = False
    db.session.commit()
    return _ok({"id": pid, "is_active": False})

@bp.post("/verify-match")
@requires_roles("admin", "hr")
def verify_match():
    if "image" not in request.files:
        return _fail("No image file provided")
    
    f = request.files["image"]
    if not f.filename:
        return _fail("Empty filename")
        
    try:
        res = FaceAttendanceService.verify_face_match(f)
        if not res["success"]:
            return _fail(res.get("error", "Verification failed"))
        return _ok(res)
    except Exception as e:
        return _fail(str(e), 500)

@bp.get("/logs")
@requires_roles("admin", "hr")
def list_logs():
    q = FaceAttendanceLog.query
    
    emp_id = request.args.get("employee_id", type=int)
    if emp_id:
        q = q.filter(FaceAttendanceLog.employee_id == emp_id)
        
    date_str = request.args.get("date")
    if date_str:
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d").date()
            q = q.filter(db.func.date(FaceAttendanceLog.created_at) == d)
        except:
            pass
            
    status = request.args.get("status") # MARKED, REJECTED
    if status:
        q = q.filter(FaceAttendanceLog.result == status)
        
    page = request.args.get("page", 1, type=int)
    limit = request.args.get("limit", 50, type=int)
    
    pagination = q.order_by(FaceAttendanceLog.created_at.desc()).paginate(page=page, per_page=limit, error_out=False)
    
    items = []
    for log in pagination.items:
        items.append({
            "id": log.id,
            "employee_id": log.employee_id,
            "employee_name": f"{log.employee.first_name} {log.employee.last_name}" if log.employee else None,
            "at": log.created_at.isoformat(),
            "result": log.result,
            "face_status": log.face_status,
            "location_status": log.location_status,
            "distance_m": log.distance_m,
            "punch_id": log.punch_id,
            "error_message": log.error_message
        })
        
    return _ok(items, meta={"page": page, "limit": limit, "total": pagination.total})

# --- Employee Endpoints ---

@bp.post("/self-punch")
@jwt_required()
def self_punch():
    identity = get_jwt_identity()
    
    # Try to resolve user
    user = None
    try:
        # If identity is int (user_id)
        uid = int(identity)
        user = User.query.get(uid)
    except:
        # If identity is string (email)
        user = User.query.filter_by(email=identity).first()
        
    if not user:
        return _fail("User not found", 401)
        
    # Find employee by email
    emp = Employee.query.filter_by(email=user.email).first()
    if not emp:
        return _fail("No employee record linked to this user", 403)
        
    if "image" not in request.files:
        return _fail("No image provided")
        
    lat = request.form.get("lat", type=float)
    lng = request.form.get("lng", type=float)
    punch_type = request.form.get("punch_type", "AUTO")
    device_id = request.form.get("device_id")
    
    if lat is None or lng is None:
        return _fail("lat and lng are required")
        
    try:
        res = FaceAttendanceService.process_self_punch(
            employee_id=emp.id,
            image_file=request.files["image"],
            lat=lat,
            lon=lng,
            punch_type=punch_type,
            device_id=device_id
        )
        if not res["success"]:
            return _fail(res["error"])
        return _ok(res)
    except Exception as e:
        return _fail(str(e), 500)
