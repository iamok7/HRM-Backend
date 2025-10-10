# hrms_api/blueprints/master_companies.py
from __future__ import annotations

from flask import Blueprint, request
from flask_jwt_extended import jwt_required
from sqlalchemy import asc, desc, or_
from hrms_api.extensions import db
from hrms_api.models.master import Company
from hrms_api.common.auth import requires_perms  # RBAC

bp = Blueprint("master_companies", __name__, url_prefix="/api/v1/master/companies")

# -------- uniform envelopes --------
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

# -------- paging/sorting/search helpers --------
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

# -------- row shape --------
def _row(x: Company):
    # Optional fields handled safely via getattr (e.g., code, gst_no, address)
    return {
        "id": x.id,
        "name": x.name,
        "code": getattr(x, "code", None),
        "gst_no": getattr(x, "gst_no", None),
        "address": getattr(x, "address", None),
        "is_active": getattr(x, "is_active", True),
        "created_at": x.created_at.isoformat() if getattr(x, "created_at", None) else None,
        "updated_at": x.updated_at.isoformat() if getattr(x, "updated_at", None) else None,
    }

# -------- routes --------
@bp.get("")
@jwt_required()
@requires_perms("master.companies.read")
def list_companies():
    qry = Company.query

    # filter: is_active
    is_active = request.args.get("is_active")
    if is_active is not None:
        v = (is_active or "").lower()
        if v in ("true", "1", "yes"):
            qry = qry.filter(Company.is_active.is_(True))
        elif v in ("false", "0", "no"):
            qry = qry.filter(Company.is_active.is_(False))
        else:
            return _fail("is_active must be true/false", 422)

    # text search on name (+ optional code/gst_no)
    s = _q_text()
    if s:
        like = f"%{s}%"
        conds = [Company.name.ilike(like)]
        if hasattr(Company, "code"):
            conds.append(Company.code.ilike(like))
        if hasattr(Company, "gst_no"):
            conds.append(Company.gst_no.ilike(like))
        qry = qry.filter(or_(*conds))

    # sorting
    allowed = {
        "id": Company.id,
        "name": Company.name,
        "created_at": getattr(Company, "created_at", Company.id),
        "updated_at": getattr(Company, "updated_at", Company.id),
        "is_active": getattr(Company, "is_active", None),
    }
    if hasattr(Company, "code"):       allowed["code"] = Company.code
    if hasattr(Company, "gst_no"):     allowed["gst_no"] = Company.gst_no

    sorts = _sort_params(allowed)
    for col, asc_order in sorts:
        qry = qry.order_by(asc(col) if asc_order else desc(col))
    if not sorts:
        qry = qry.order_by(asc(Company.name))  # default

    # paging
    page, size = _page_size()
    total = qry.count()
    items = qry.offset((page - 1) * size).limit(size).all()
    return _ok([_row(i) for i in items], page=page, size=size, total=total)

@bp.get("/<int:comp_id>")
@jwt_required()
@requires_perms("master.companies.read")
def get_company(comp_id: int):
    x = Company.query.get(comp_id)
    if not x:
        return _fail("Company not found", 404)
    return _ok(_row(x))

@bp.post("")
@jwt_required()
@requires_perms("master.companies.create")
def create_company():
    data = request.get_json(silent=True, force=True) or {}
    name = (data.get("name") or "").strip()
    is_active = data.get("is_active", True)

    if not name:
        return _fail("name is required", 422)

    # global uniqueness on name (case-insensitive)
    dup = Company.query.filter(db.func.lower(Company.name) == name.lower()).first()
    if dup:
        return _fail("Company with same name already exists", 409)

    obj = Company(name=name)
    if hasattr(Company, "is_active"): obj.is_active = bool(is_active)
    # optional fields
    if hasattr(Company, "code") and data.get("code"):
        obj.code = (data.get("code") or "").strip()
    if hasattr(Company, "gst_no") and data.get("gst_no"):
        obj.gst_no = (data.get("gst_no") or "").strip()
    if hasattr(Company, "address") and data.get("address"):
        obj.address = (data.get("address") or "").strip()

    db.session.add(obj)
    db.session.commit()
    return _ok(_row(obj), 201)

@bp.put("/<int:comp_id>")
@jwt_required()
@requires_perms("master.companies.update")
def update_company(comp_id: int):
    obj = Company.query.get(comp_id)
    if not obj:
        return _fail("Company not found", 404)

    data = request.get_json(silent=True, force=True) or {}

    # name
    if "name" in data:
        candidate = (data.get("name") or "").strip()
        if not candidate:
            return _fail("name cannot be empty", 422)
        # check uniqueness (case-insensitive)
        dup = Company.query.filter(
            Company.id != obj.id,
            db.func.lower(Company.name) == candidate.lower()
        ).first()
        if dup:
            return _fail("Company with same name already exists", 409)
        obj.name = candidate

    # is_active
    if "is_active" in data and hasattr(Company, "is_active"):
        obj.is_active = bool(data.get("is_active"))

    # optional fields
    if hasattr(Company, "code") and "code" in data:
        val = (data.get("code") or "").strip()
        obj.code = val or None
    if hasattr(Company, "gst_no") and "gst_no" in data:
        val = (data.get("gst_no") or "").strip()
        obj.gst_no = val or None
    if hasattr(Company, "address") and "address" in data:
        val = (data.get("address") or "").strip()
        obj.address = val or None

    db.session.commit()
    return _ok(_row(obj))

@bp.delete("/<int:comp_id>")
@jwt_required()
@requires_perms("master.companies.delete")
def delete_company(comp_id: int):
    obj = Company.query.get(comp_id)
    if not obj:
        return _fail("Company not found", 404)

    # Soft delete: mark inactive if field exists; else hard delete fallback
    if hasattr(Company, "is_active"):
        obj.is_active = False
        db.session.commit()
        return _ok({"id": comp_id, "is_active": False})
    else:
        try:
            db.session.delete(obj)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            return _fail("Cannot delete: referenced by other records", 409, detail=str(e))
        return _ok({"deleted": True, "id": comp_id})
