# hrms_api/blueprints/master_cost_centers.py
from __future__ import annotations

from flask import Blueprint, request
from flask_jwt_extended import jwt_required
from sqlalchemy import or_, asc, desc

from hrms_api.extensions import db
from hrms_api.models.master import CostCenter, Company
from hrms_api.common.auth import requires_perms  # RBAC

bp = Blueprint("master_cost_centers", __name__, url_prefix="/api/v1/master/cost-centers")

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
def _row(x: CostCenter):
    return {
        "id": x.id,
        "company_id": x.company_id,
        "company_name": x.company.name if x.company else None,
        "name": x.name,
        "code": getattr(x, "code", None),
        "is_active": x.is_active,
        "created_at": x.created_at.isoformat() if x.created_at else None,
        "updated_at": x.updated_at.isoformat() if getattr(x, "updated_at", None) else None,
    }


# ---------- routes ----------
@bp.get("")
@jwt_required()
@requires_perms("master.cost_centers.read")
def list_cost_centers():
    qry = CostCenter.query.join(Company, CostCenter.company_id == Company.id)

    # filters
    company_id = request.args.get("company_id")
    if company_id:
        try:
            qry = qry.filter(CostCenter.company_id == int(company_id))
        except ValueError:
            return _fail("company_id must be integer", 422)

    is_active = request.args.get("is_active")
    if is_active is not None:
        v = (is_active or "").lower()
        if v in ("true", "1", "yes"):
            qry = qry.filter(CostCenter.is_active.is_(True))
        elif v in ("false", "0", "no"):
            qry = qry.filter(CostCenter.is_active.is_(False))
        else:
            return _fail("is_active must be true/false", 422)

    # text search on cc name/code/company
    s = _q_text()
    if s:
        like = f"%{s}%"
        conds = [CostCenter.name.ilike(like), Company.name.ilike(like)]
        if hasattr(CostCenter, "code"):
            conds.append(CostCenter.code.ilike(like))
        qry = qry.filter(or_(*conds))

    # sorting
    allowed = {
        "id": CostCenter.id,
        "name": CostCenter.name,
        "company_id": CostCenter.company_id,
        "created_at": CostCenter.created_at,
        "updated_at": getattr(CostCenter, "updated_at", CostCenter.created_at),
        "is_active": CostCenter.is_active,
    }
    if hasattr(CostCenter, "code"):
        allowed["code"] = CostCenter.code

    sorts = _sort_params(allowed)
    for col, asc_order in sorts:
        qry = qry.order_by(asc(col) if asc_order else desc(col))
    if not sorts:
        # default: name, then code (if exists)
        qry = qry.order_by(asc(CostCenter.name))
        if hasattr(CostCenter, "code"):
            qry = qry.order_by(asc(CostCenter.code))

    # paging
    page, size = _page_size()
    total = qry.count()
    items = qry.offset((page - 1) * size).limit(size).all()
    return _ok([_row(i) for i in items], page=page, size=size, total=total)


@bp.get("/<int:cc_id>")
@jwt_required()
@requires_perms("master.cost_centers.read")
def get_cost_center(cc_id: int):
    x = CostCenter.query.get(cc_id)
    if not x:
        return _fail("Cost center not found", 404)
    return _ok(_row(x))


@bp.post("")
@jwt_required()
@requires_perms("master.cost_centers.create")
def create_cost_center():
    data = request.get_json(silent=True, force=True) or {}
    name = (data.get("name") or "").strip()
    company_id = data.get("company_id")
    is_active = data.get("is_active", True)
    code = (data.get("code") or "").strip() if "code" in data else None

    if not name or not company_id:
        return _fail("company_id and name are required", 422)

    comp = Company.query.get(company_id)
    if not comp:
        return _fail("company_id not found", 404)

    # uniqueness within company
    if hasattr(CostCenter, "code") and code:
        dup = CostCenter.query.filter(
            CostCenter.company_id == comp.id,
            db.func.lower(CostCenter.code) == code.lower()
        ).first()
        if dup:
            return _fail("Cost center code already exists for this company", 409)

    dup_name = CostCenter.query.filter(
        CostCenter.company_id == comp.id,
        db.func.lower(CostCenter.name) == name.lower()
    ).first()
    if dup_name:
        return _fail("Cost center with same name already exists for this company", 409)

    obj = CostCenter(company_id=comp.id, name=name, is_active=bool(is_active))
    if hasattr(CostCenter, "code"):
        obj.code = code or None

    db.session.add(obj)
    db.session.commit()
    return _ok(_row(obj), 201)


@bp.put("/<int:cc_id>")
@jwt_required()
@requires_perms("master.cost_centers.update")
def update_cost_center(cc_id: int):
    obj = CostCenter.query.get(cc_id)
    if not obj:
        return _fail("Cost center not found", 404)

    data = request.get_json(silent=True, force=True) or {}

    new_name = obj.name
    new_company_id = obj.company_id
    new_is_active = obj.is_active
    new_code = getattr(obj, "code", None)

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

    if hasattr(CostCenter, "code") and "code" in data:
        new_code = (data.get("code") or "").strip() or None

    # uniqueness re-check within company
    if hasattr(CostCenter, "code") and new_code:
        dup_code = CostCenter.query.filter(
            CostCenter.id != obj.id,
            CostCenter.company_id == new_company_id,
            db.func.lower(CostCenter.code) == new_code.lower()
        ).first()
        if dup_code:
            return _fail("Cost center code already exists for this company", 409)

    dup_name = CostCenter.query.filter(
        CostCenter.id != obj.id,
        CostCenter.company_id == new_company_id,
        db.func.lower(CostCenter.name) == new_name.lower()
    ).first()
    if dup_name:
        return _fail("Cost center with same name already exists for this company", 409)

    obj.company_id = new_company_id
    obj.name = new_name
    obj.is_active = new_is_active
    if hasattr(CostCenter, "code"):
        obj.code = new_code

    db.session.commit()
    return _ok(_row(obj))


@bp.delete("/<int:cc_id>")
@jwt_required()
@requires_perms("master.cost_centers.delete")
def delete_cost_center(cc_id: int):
    obj = CostCenter.query.get(cc_id)
    if not obj:
        return _fail("Cost center not found", 404)
    # Soft delete: mark inactive
    obj.is_active = False
    db.session.commit()
    return _ok({"id": cc_id, "is_active": False})
