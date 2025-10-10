# hrms_api/blueprints/master_designations.py
from __future__ import annotations

from flask import Blueprint, request
from flask_jwt_extended import jwt_required
from sqlalchemy import or_, asc, desc

from hrms_api.extensions import db
from hrms_api.models.master import Designation, Company
from hrms_api.common.auth import requires_perms  # RBAC

bp = Blueprint("master_designations", __name__, url_prefix="/api/v1/master/designations")

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
def _row(x: Designation):
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
@requires_perms("master.designations.read")
def list_designations():
    qry = Designation.query.join(Company, Designation.company_id == Company.id)

    # filters
    company_id = request.args.get("company_id")
    if company_id:
        try:
            qry = qry.filter(Designation.company_id == int(company_id))
        except ValueError:
            return _fail("company_id must be integer", 422)

    is_active = request.args.get("is_active")
    if is_active is not None:
        v = (is_active or "").lower()
        if v in ("true", "1", "yes"):
            qry = qry.filter(Designation.is_active.is_(True))
        elif v in ("false", "0", "no"):
            qry = qry.filter(Designation.is_active.is_(False))
        else:
            return _fail("is_active must be true/false", 422)

    # text search on designation/company name
    s = _q_text()
    if s:
        like = f"%{s}%"
        qry = qry.filter(or_(Designation.name.ilike(like), Company.name.ilike(like)))

    # sorting
    allowed = {
        "id": Designation.id,
        "name": Designation.name,
        "company_id": Designation.company_id,
        "created_at": Designation.created_at,
        "updated_at": getattr(Designation, "updated_at", Designation.created_at),
        "is_active": Designation.is_active,
    }
    sorts = _sort_params(allowed)
    for col, asc_order in sorts:
        qry = qry.order_by(asc(col) if asc_order else desc(col))
    if not sorts:
        qry = qry.order_by(asc(Designation.name))  # default

    # paging
    page, size = _page_size()
    total = qry.count()
    items = qry.offset((page - 1) * size).limit(size).all()
    return _ok([_row(i) for i in items], page=page, size=size, total=total)


@bp.get("/<int:desig_id>")
@jwt_required()
@requires_perms("master.designations.read")
def get_designation(desig_id: int):
    x = Designation.query.get(desig_id)
    if not x:
        return _fail("Designation not found", 404)
    return _ok(_row(x))


@bp.post("")
@jwt_required()
@requires_perms("master.designations.create")
def create_designation():
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
    dup = Designation.query.filter(
        Designation.company_id == comp.id,
        db.func.lower(Designation.name) == name.lower()
    ).first()
    if dup:
        return _fail("Designation with same name already exists for this company", 409)

    obj = Designation(company_id=comp.id, name=name, is_active=bool(is_active))
    db.session.add(obj)
    db.session.commit()
    return _ok(_row(obj), 201)


@bp.put("/<int:desig_id>")
@jwt_required()
@requires_perms("master.designations.update")
def update_designation(desig_id: int):
    obj = Designation.query.get(desig_id)
    if not obj:
        return _fail("Designation not found", 404)

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

    # uniqueness re-check within company
    dup = Designation.query.filter(
        Designation.id != obj.id,
        Designation.company_id == new_company_id,
        db.func.lower(Designation.name) == new_name.lower(),
    ).first()
    if dup:
        return _fail("Designation with same name already exists for this company", 409)

    obj.company_id = new_company_id
    obj.name = new_name
    obj.is_active = new_is_active
    db.session.commit()
    return _ok(_row(obj))


@bp.delete("/<int:desig_id>")
@jwt_required()
@requires_perms("master.designations.delete")
def delete_designation(desig_id: int):
    obj = Designation.query.get(desig_id)
    if not obj:
        return _fail("Designation not found", 404)
    # Soft delete: mark inactive
    obj.is_active = False
    db.session.commit()
    return _ok({"id": desig_id, "is_active": False})
