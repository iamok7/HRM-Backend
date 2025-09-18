from flask import Blueprint, request
from sqlalchemy import or_
from hrms_api.extensions import db
from hrms_api.models.master import Department, Company

# add in files where you guard routes
from hrms_api.common.auth import requires_perms
from flask_jwt_extended import jwt_required
from hrms_api.rbac import require_perm

bp = Blueprint("master_departments", __name__, url_prefix="/api/v1/master/departments")

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

def _row(x: Department):
    return {
        "id": x.id,
        "company_id": x.company_id,
        "company_name": x.company.name if x.company else None,
        "name": x.name,
        "is_active": x.is_active,
        "created_at": x.created_at.isoformat() if x.created_at else None,
    }

@bp.get("")
def list_departments():
    q = Department.query.join(Company)
    company_id = request.args.get("companyId", type=int)
    if company_id: q = q.filter(Department.company_id == company_id)
    s = (request.args.get("q") or "").strip().lower()
    if s:
        like = f"%{s}%"
        q = q.filter(or_(Department.name.ilike(like), Company.name.ilike(like)))
    is_active = request.args.get("isActive")
    if is_active is not None:
        v = (is_active or "").lower()
        if v in ("true","1"):  q = q.filter(Department.is_active.is_(True))
        if v in ("false","0"): q = q.filter(Department.is_active.is_(False))
    page, limit = _page_limit()
    total = q.count()
    items = q.order_by(Department.id.desc()).offset((page-1)*limit).limit(limit).all()
    return _ok([_row(i) for i in items], page=page, limit=limit, total=total)

@bp.get("/<int:did>")
def get_department(did: int):
    x = Department.query.get(did)
    if not x: return _fail("Department not found", 404)
    return _ok(_row(x))

@bp.post("")
@require_perm("master.departments.create")
def create_department():
    data = request.get_json(silent=True, force=True) or {}
    company_id = data.get("company_id")
    name = (data.get("name") or "").strip()
    if not company_id or not name: return _fail("company_id and name are required", 422)
    if not Company.query.get(company_id): return _fail("Invalid company_id", 422)
    if Department.query.filter_by(company_id=company_id, name=name).first():
        return _fail("Department already exists for this company", 409)
    x = Department(company_id=company_id, name=name, is_active=bool(data.get("is_active", True)))
    db.session.add(x); db.session.commit()
    return _ok(_row(x), status=201)

@bp.put("/<int:did>")
@require_perm("master.departments.update")
def update_department(did: int):
    x = Department.query.get(did)
    if not x: return _fail("Department not found", 404)
    data = request.get_json(silent=True, force=True) or {}
    if "name" in data:
        new_name = (data["name"] or "").strip()
        if not new_name: return _fail("name cannot be empty", 422)
        dup = Department.query.filter(
            Department.id != did,
            Department.company_id == (data.get("company_id") or x.company_id),
            Department.name == new_name
        ).first()
        if dup: return _fail("Department already exists for this company", 409)
        x.name = new_name
    if "company_id" in data:
        cid = data["company_id"]
        if not Company.query.get(cid): return _fail("Invalid company_id", 422)
        x.company_id = cid
    if "is_active" in data: x.is_active = bool(data["is_active"])
    db.session.commit()
    return _ok(_row(x))

@bp.delete("/<int:did>")
@require_perm("master.departments.delete")
def delete_department(did: int):
    x = Department.query.get(did)
    if not x: return _fail("Department not found", 404)
    x.is_active = False
    db.session.commit()
    return _ok({"id": did, "is_active": False})
