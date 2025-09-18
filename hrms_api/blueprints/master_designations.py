from flask import Blueprint, request
from sqlalchemy import or_
from hrms_api.extensions import db
from hrms_api.models.master import Designation, Department, Company

# add in files where you guard routes
from hrms_api.common.auth import requires_perms
from flask_jwt_extended import jwt_required

bp = Blueprint("master_designations", __name__, url_prefix="/api/v1/master/designations")

def _ok(data=None, status=200, **meta):
    from flask import jsonify
    payload = {"success": True, "data": data}
    if meta: payload["meta"] = meta
    return jsonify(payload), status

def _fail(msg, status=400):
    from flask import jsonify
    return jsonify({"success": False, "error": {"message": msg}}), status

def _page_limit():
    try:
        page  = max(int(request.args.get("page", 1)), 1)
        limit = min(max(int(request.args.get("limit", 20)), 1), 100)
    except Exception:
        page, limit = 1, 20
    return page, limit

def _row(x: Designation):
    return {
        "id": x.id,
        "department_id": x.department_id,
        "department_name": x.department.name if x.department else None,
        "company_id": x.department.company_id if x.department else None,
        "name": x.name,
        "is_active": x.is_active,
        "created_at": x.created_at.isoformat() if x.created_at else None,
    }

@bp.get("")
def list_designations():
    q = Designation.query.join(Department).join(Company)
    company_id = request.args.get("companyId", type=int)
    if company_id: q = q.filter(Department.company_id == company_id)
    department_id = request.args.get("departmentId", type=int)
    if department_id: q = q.filter(Designation.department_id == department_id)
    s = (request.args.get("q") or "").strip().lower()
    if s:
        like = f"%{s}%"
        q = q.filter(or_(Designation.name.ilike(like), Department.name.ilike(like)))
    is_active = request.args.get("isActive")
    if is_active is not None:
        v = (is_active or "").lower()
        if v in ("true","1"):  q = q.filter(Designation.is_active.is_(True))
        if v in ("false","0"): q = q.filter(Designation.is_active.is_(False))
    page, limit = _page_limit()
    total = q.count()
    items = q.order_by(Designation.id.desc()).offset((page-1)*limit).limit(limit).all()
    return _ok([_row(i) for i in items], page=page, limit=limit, total=total)

@bp.get("/<int:gid>")
def get_designation(gid: int):
    x = Designation.query.get(gid)
    if not x: return _fail("Designation not found", 404)
    return _ok(_row(x))

@bp.post("")
@requires_perms("master.designations.create")
def create_designation():
    data = request.get_json(silent=True, force=True) or {}
    department_id = data.get("department_id")
    name = (data.get("name") or "").strip()
    if not department_id or not name: return _fail("department_id and name are required", 422)
    if not Department.query.get(department_id): return _fail("Invalid department_id", 422)
    if Designation.query.filter_by(department_id=department_id, name=name).first():
        return _fail("Designation already exists for this department", 409)
    x = Designation(department_id=department_id, name=name, is_active=bool(data.get("is_active", True)))
    db.session.add(x); db.session.commit()
    return _ok(_row(x), status=201)

@bp.put("/<int:gid>")
@requires_perms("master.designations.update")
def update_designation(gid: int):
    x = Designation.query.get(gid)
    if not x: return _fail("Designation not found", 404)
    data = request.get_json(silent=True, force=True) or {}
    if "name" in data:
        new_name = (data["name"] or "").strip()
        if not new_name: return _fail("name cannot be empty", 422)
        dup = Designation.query.filter(
            Designation.id != gid,
            Designation.department_id == (data.get("department_id") or x.department_id),
            Designation.name == new_name
        ).first()
        if dup: return _fail("Designation already exists for this department", 409)
        x.name = new_name
    if "department_id" in data:
        did = data["department_id"]
        if not Department.query.get(did): return _fail("Invalid department_id", 422)
        x.department_id = did
    if "is_active" in data: x.is_active = bool(data["is_active"])
    db.session.commit()
    return _ok(_row(x))

@bp.delete("/<int:gid>")
@requires_perms("master.designations.delete")
def delete_designation(gid: int):
    x = Designation.query.get(gid)
    if not x: return _fail("Designation not found", 404)
    x.is_active = False
    db.session.commit()
    return _ok({"id": gid, "is_active": False})
