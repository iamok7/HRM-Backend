from flask import Blueprint, request
from hrms_api.extensions import db
from hrms_api.models.master import CostCenter


from flask_jwt_extended import jwt_required
from hrms_api.common.auth import requires_roles 

bp = Blueprint("master_cost_centers", __name__, url_prefix="/api/v1/master/cost-centers")


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

def _row(x: CostCenter):
    return {"id": x.id, "code": x.code, "name": x.name, "is_active": x.is_active}

@bp.get("")
@jwt_required()
def list_cc():
    q = CostCenter.query
    qstr = (request.args.get("q") or "").strip().lower()
    if qstr:
        q = q.filter((CostCenter.code.ilike(f"%{qstr}%")) | (CostCenter.name.ilike(f"%{qstr}%")))
    is_active = request.args.get("isActive")
    if is_active is not None:
        v = (is_active or "").lower()
        if v in ("true","1"):  q = q.filter(CostCenter.is_active.is_(True))
        if v in ("false","0"): q = q.filter(CostCenter.is_active.is_(False))
    page, limit = _page_limit()
    total = q.count()
    items = q.order_by(CostCenter.code.asc()).offset((page-1)*limit).limit(limit).all()
    return _ok([_row(i) for i in items], page=page, limit=limit, total=total)

@bp.get("/<int:cid>")
@jwt_required()
def get_cc(cid: int):
    x = CostCenter.query.get(cid)
    if not x: return _fail("Cost center not found", 404)
    return _ok(_row(x))

@bp.post("")
@requires_roles("admin")
def create_cc():
    data = request.get_json(silent=True, force=True) or {}
    code = (data.get("code") or "").strip()
    name = (data.get("name") or "").strip()
    if not code or not name: return _fail("code and name are required", 422)
    if CostCenter.query.filter_by(code=code).first(): return _fail("code already exists", 409)
    x = CostCenter(code=code, name=name, is_active=bool(data.get("is_active", True)))
    db.session.add(x); db.session.commit()
    return _ok(_row(x), status=201)

@bp.put("/<int:cid>")
@requires_roles("admin")
def update_cc(cid: int):
    x = CostCenter.query.get(cid)
    if not x: return _fail("Cost center not found", 404)
    data = request.get_json(silent=True, force=True) or {}
    if "code" in data:
        new_code = (data["code"] or "").strip()
        if not new_code: return _fail("code cannot be empty", 422)
        if CostCenter.query.filter(CostCenter.id != cid, CostCenter.code == new_code).first():
            return _fail("code already exists", 409)
        x.code = new_code
    if "name" in data:
        new_name = (data["name"] or "").strip()
        if not new_name: return _fail("name cannot be empty", 422)
        x.name = new_name
    if "is_active" in data: x.is_active = bool(data["is_active"])
    db.session.commit()
    return _ok(_row(x))

@bp.delete("/<int:cid>")
@requires_roles("admin")
def delete_cc(cid: int):
    x = CostCenter.query.get(cid)
    if not x: return _fail("Cost center not found", 404)
    x.is_active = False
    db.session.commit()
    return _ok({"id": cid, "is_active": False})
