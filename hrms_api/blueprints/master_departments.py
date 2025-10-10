# hrms_api/blueprints/master_departments.py
from __future__ import annotations

from flask import Blueprint, request
from flask_jwt_extended import jwt_required
from sqlalchemy import or_, asc, desc

from hrms_api.extensions import db
from hrms_api.models.master import Department, Company
from hrms_api.common.auth import requires_perms  # RBAC

bp = Blueprint("master_departments", __name__, url_prefix="/api/v1/master/departments")

# ---------- uniform envelopes ----------
def _ok(data=None, status=200, **meta):
    from flask import jsonify
    payload = {"success": True, "data": data}
    if meta:
        payload["meta"] = meta
    return jsonify(payload), status

def _fail(message, status=400, code=None, detail=None):
    from flask import jsonify
    err = {"message": message}
    if code: err["code"] = code
    if detail: err["detail"] = detail
    return jsonify({"success": False, "error": err}), status


# ---------- paging/sorting/search helpers ----------
DEFAULT_PAGE, DEFAULT_SIZE, MAX_SIZE = 1, 20, 100

def _page_size():
    # ?page & ?size  (standardized; earlier file used "limit")
    try:
        page = max(int(request.args.get("page", DEFAULT_PAGE)), 1)
    except Exception:
        page = DEFAULT_PAGE
    try:
        size = int(request.args.get("size", DEFAULT_SIZE))
        size = max(1, min(size, MAX_SIZE))
    except Exception:
        size = DEFAULT_SIZE
    return page, size

def _sort_params(allowed: dict[str, object]):
    # ?sort=name,-created_at
    raw = (request.args.get("sort") or "").strip()
    out = []
    if not raw:
        return out
    for part in [p.strip() for p in raw.split(",") if p.strip()]:
        asc_order = True
        key = part
        if part.startswith("-"):
            asc_order = False
            key = part[1:]
        col = allowed.get(key)
        if col is not None:
            out.append((col, asc_order))
    return out

def _q_text():
    q = (request.args.get("q") or "").strip()
    return q or None


# ---------- row shape ----------
def _row(x: Department):
    return {
        "id": x.id,
        "company_id": x.company_id,
        "company_name": x.company.name if x.company else None,
        "name": x.name,
        "is_active": x.is_active,
        "created_at": x.created_at.isoformat() if x.created_at else None,
        "updated_at": x.updated_at.isoformat() if getattr(x, "updated_at", None) else None,
    }


# ---------- routes ----------
@bp.get("")
@jwt_required()
@requires_perms("master.departments.read")
def list_departments():
    qry = Department.query.join(Company, Department.company_id == Company.id)

    # filters (standardized param names)
    company_id = request.args.get("company_id")
    if company_id:
        try:
            qry = qry.filter(Department.company_id == int(company_id))
        except ValueError:
            return _fail("company_id must be integer", 422)

    is_active = request.args.get("is_active")
    if is_active is not None:
        v = (is_active or "").lower()
        if v in ("true", "1", "yes"):
            qry = qry.filter(Department.is_active.is_(True))
        elif v in ("false", "0", "no"):
            qry = qry.filter(Department.is_active.is_(False))
        else:
            return _fail("is_active must be true/false", 422)

    # text search
    s = _q_text()
    if s:
        like = f"%{s}%"
        qry = qry.filter(or_(Department.name.ilike(like), Company.name.ilike(like)))

    # sorting
    allowed = {
        "id": Department.id,
        "name": Department.name,
        "company_id": Department.company_id,
        "created_at": Department.created_at,
        "updated_at": getattr(Department, "updated_at", Department.created_at),
        "is_active": Department.is_active,
    }
    sorts = _sort_params(allowed)
    for col, asc_order in sorts:
        qry = qry.order_by(asc(col) if asc_order else desc(col))
    if not sorts:
        qry = qry.order_by(asc(Department.name))  # default

    # paging
    page, size = _page_size()
    total = qry.count()
    items = qry.offset((page - 1) * size).limit(size).all()
    return _ok([_row(i) for i in items], page=page, size=size, total=total)


@bp.get("/<int:dep_id>")
@jwt_required()
@requires_perms("master.departments.read")
def get_department(dep_id: int):
    x = Department.query.get(dep_id)
    if not x:
        return _fail("Department not found", 404)
    return _ok(_row(x))


@bp.post("")
@jwt_required()
@requires_perms("master.departments.create")
def create_department():
    data = request.get_json(silent=True, force=True) or {}
    name = (data.get("name") or "").strip()
    company_id = data.get("company_id")
    is_active = data.get("is_active", True)

    if not name or not company_id:
        return _fail("company_id and name are required", 422)

    comp = Company.query.get(company_id)
    if not comp:
        return _fail("company_id not found", 404)

    # unique within company (case-insensitive)
    dup = Department.query.filter(
        Department.company_id == comp.id,
        db.func.lower(Department.name) == name.lower()
    ).first()
    if dup:
        return _fail("Department with same name already exists for this company", 409)

    obj = Department(company_id=comp.id, name=name, is_active=bool(is_active))
    db.session.add(obj)
    db.session.commit()
    return _ok(_row(obj), 201)


@bp.put("/<int:dep_id>")
@jwt_required()
@requires_perms("master.departments.update")
def update_department(dep_id: int):
    obj = Department.query.get(dep_id)
    if not obj:
        return _fail("Department not found", 404)

    data = request.get_json(silent=True, force=True) or {}

    new_name = obj.name
    new_company_id = obj.company_id
    new_is_active = obj.is_active

    if "name" in data:
        candidate = (data.get("name") or "").strip()
        if not candidate:
            return _fail("name cannot be empty", 422)
        new_name = candidate

    if "company_id" in data:
        cid = data.get("company_id")
        comp = Company.query.get(cid)
        if not comp:
            return _fail("company_id not found", 404)
        new_company_id = comp.id

    if "is_active" in data:
        new_is_active = bool(data.get("is_active"))

    # uniqueness re-check
    dup = Department.query.filter(
        Department.id != obj.id,
        Department.company_id == new_company_id,
        db.func.lower(Department.name) == new_name.lower(),
    ).first()
    if dup:
        return _fail("Department with same name already exists for this company", 409)

    obj.company_id = new_company_id
    obj.name = new_name
    obj.is_active = new_is_active
    db.session.commit()
    return _ok(_row(obj))


@bp.delete("/<int:dep_id>")
@jwt_required()
@requires_perms("master.departments.delete")
def delete_department(dep_id: int):
    obj = Department.query.get(dep_id)
    if not obj:
        return _fail("Department not found", 404)
    # Soft delete: mark inactive
    obj.is_active = False
    db.session.commit()
    return _ok({"id": dep_id, "is_active": False})
