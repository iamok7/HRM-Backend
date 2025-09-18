from flask import Blueprint, request
from hrms_api.extensions import db
from hrms_api.models.master import Grade

from hrms_api.common.auth import requires_perms
from flask_jwt_extended import jwt_required

bp = Blueprint("master_grades", __name__, url_prefix="/api/v1/master/grades")

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

def _row(x: Grade):
    return {"id": x.id, "name": x.name, "is_active": x.is_active}

@bp.get("")
def list_grades():
    q = Grade.query
    s = (request.args.get("q") or "").strip().lower()
    if s: q = q.filter(Grade.name.ilike(f"%{s}%"))
    is_active = request.args.get("isActive")
    if is_active is not None:
        v = (is_active or "").lower()
        if v in ("true","1"):  q = q.filter(Grade.is_active.is_(True))
        if v in ("false","0"): q = q.filter(Grade.is_active.is_(False))
    page, limit = _page_limit()
    total = q.count()
    items = q.order_by(Grade.id.asc()).offset((page-1)*limit).limit(limit).all()
    return _ok([_row(i) for i in items], page=page, limit=limit, total=total)

@bp.get("/<int:gid>")
def get_grade(gid: int):
    x = Grade.query.get(gid)
    if not x: return _fail("Grade not found", 404)
    return _ok(_row(x))

@bp.post("")
@requires_perms("master.grades.create")
def create_grade():
    data = request.get_json(silent=True, force=True) or {}
    name = (data.get("name") or "").strip()
    if not name: return _fail("name is required", 422)
    if Grade.query.filter_by(name=name).first(): return _fail("Grade already exists", 409)
    x = Grade(name=name, is_active=bool(data.get("is_active", True)))
    db.session.add(x); db.session.commit()
    return _ok(_row(x), status=201)

@bp.put("/<int:gid>")
@requires_perms("master.grades.update")
def update_grade(gid: int):
    x = Grade.query.get(gid)
    if not x: return _fail("Grade not found", 404)
    data = request.get_json(silent=True, force=True) or {}
    if "name" in data:
        new_name = (data["name"] or "").strip()
        if not new_name: return _fail("name cannot be empty", 422)
        if Grade.query.filter(Grade.id != gid, Grade.name == new_name).first():
            return _fail("Grade already exists", 409)
        x.name = new_name
    if "is_active" in data: x.is_active = bool(data["is_active"])
    db.session.commit()
    return _ok(_row(x))

@bp.delete("/<int:gid>")
@requires_perms("master.grades.delete")
def delete_grade(gid: int):
    x = Grade.query.get(gid)
    if not x: return _fail("Grade not found", 404)
    x.is_active = False
    db.session.commit()
    return _ok({"id": gid, "is_active": False})
