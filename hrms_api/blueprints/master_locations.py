from flask import Blueprint, request, current_app
from sqlalchemy import or_
from hrms_api.extensions import db
from hrms_api.models.master import Location, Company

from flask_jwt_extended import jwt_required
from hrms_api.common.auth import requires_roles

from hrms_api.common.auth import requires_perms
from flask_jwt_extended import jwt_required

from hrms_api.rbac import require_perm

bp = Blueprint("master_locations", __name__, url_prefix="/api/v1/master/locations")

def _row(x: Location):
    return {
        "id": x.id,
        "company_id": x.company_id,
        "company_name": x.company.name if x.company else None,
        "name": x.name,
        "is_active": x.is_active,
        "created_at": x.created_at.isoformat() if x.created_at else None,
    }

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

@bp.get("")
@jwt_required()
def list_locations():
    q = Location.query.join(Company)
    # filters
    company_id = request.args.get("companyId", type=int)
    if company_id:
        q = q.filter(Location.company_id == company_id)
    is_active = request.args.get("isActive")
    if is_active is not None:
        v = (is_active or "").lower()
        if v in ("true","1"):  q = q.filter(Location.is_active.is_(True))
        if v in ("false","0"): q = q.filter(Location.is_active.is_(False))
    # search
    s = (request.args.get("q") or "").strip().lower()
    if s:
        like = f"%{s}%"
        q = q.filter(or_(Location.name.ilike(like), Company.name.ilike(like)))
    # paginate
    page, limit = _page_limit()
    total = q.count()
    items = q.order_by(Location.id.desc()).offset((page-1)*limit).limit(limit).all()
    return _ok([_row(i) for i in items], page=page, limit=limit, total=total)

@bp.get("/<int:lid>")
def get_location(lid: int):
    x = Location.query.get(lid)
    if not x: return _fail("Location not found", 404)
    return _ok(_row(x))

@bp.post("")
@require_perm("master.locations.create")
@requires_roles("admin")
def create_location():
    data = request.get_json(force=True,silent=True) or {}
    name = (data.get("name") or "").strip()
    company_id = data.get("company_id")
    if not company_id or not name:
        current_app.logger.warning("Bad payload for /locations: %r", request.get_data())
        return _fail("company_id and name are required", 422)
    if not Company.query.get(company_id):
        return _fail("Invalid company_id", 422)
    if Location.query.filter_by(company_id=company_id, name=name).first():
        return _fail("Location already exists for this company", 409)
    x = Location(company_id=company_id, name=name, is_active=bool(data.get("is_active", True)))
    db.session.add(x); db.session.commit()
    return _ok(_row(x), status=201)

@bp.put("/<int:lid>")
@require_perm("master.locations.update")
@requires_roles("admin")
def update_location(lid: int):
    x = Location.query.get(lid)
    if not x: return _fail("Location not found", 404)
    data = request.get_json(silent=True) or {}
    if "name" in data:
        new_name = (data["name"] or "").strip()
        if not new_name: return _fail("name cannot be empty", 422)
        dup = Location.query.filter(
            Location.id != lid,
            Location.company_id == (data.get("company_id") or x.company_id),
            Location.name == new_name
        ).first()
        if dup: return _fail("Location already exists for this company", 409)
        x.name = new_name
    if "company_id" in data:
        cid = data["company_id"]
        if not Company.query.get(cid): return _fail("Invalid company_id", 422)
        x.company_id = cid
    if "is_active" in data:
        x.is_active = bool(data["is_active"])
    db.session.commit()
    return _ok(_row(x))

@bp.delete("/<int:lid>")
@require_perm("master.locations.delete")
@requires_roles("admin")
def delete_location(lid: int):
    x = Location.query.get(lid)
    if not x: return _fail("Location not found", 404)
    x.is_active = False  # soft delete
    db.session.commit()
    return _ok({"id": lid, "is_active": False})
