# apps/backend/hrms_api/blueprints/master_companies.py
from __future__ import annotations

from flask import Blueprint, jsonify, request
from sqlalchemy.sql import func

from hrms_api.extensions import db
from hrms_api.common.listing import get_page_limit, apply_q_search
from hrms_api.rbac import require_perm

from hrms_api.models.master import Company
# If you later switch back to hard delete w/ FK checks, uncomment these:
# from hrms_api.models.master import Location, Department
# from hrms_api.models.employee import Employee

bp = Blueprint("master_companies", __name__, url_prefix="/api/v1/master/companies")


# ---------- helpers ----------

def _row(c: Company) -> dict:
    """Serialize a Company model to API shape."""
    return {
        "id": c.id,
        "code": c.code,
        "name": c.name,
        "is_active": bool(getattr(c, "is_active", True)),
        "created_at": getattr(c, "created_at", None).isoformat()
        if getattr(c, "created_at", None) else None,
        # include deleted_at if you track soft deletes
        "deleted_at": getattr(c, "deleted_at", None).isoformat()
        if getattr(c, "deleted_at", None) else None,
    }


def _soft_delete_company(c: Company) -> None:
    """
    Soft-delete that works even if Company doesn't implement soft_delete().
    Prefer using the model's soft_delete() if present.
    """
    if hasattr(c, "soft_delete") and callable(c.soft_delete):
        c.soft_delete()
        return
    # Fallback: mark inactive + timestamp if column exists
    if hasattr(c, "is_active"):
        c.is_active = False
    if hasattr(c, "deleted_at"):
        c.deleted_at = func.now()


# ---------- routes ----------

@bp.get("")
@require_perm("master.companies.read")
def companies_list():
    q = Company.query
    q = apply_q_search(q, Company.code, Company.name)

    page, limit = get_page_limit()
    total = q.count()
    items = (
        q.order_by(Company.id.asc())
         .offset((page - 1) * limit)
         .limit(limit)
         .all()
    )

    return jsonify({
        "success": True,
        "data": [_row(c) for c in items],
        "meta": {"page": page, "limit": limit, "total": total},
    })


@bp.get("/<int:cid>")
@require_perm("master.companies.read")
def companies_get(cid: int):
    c = Company.query.get_or_404(cid)
    return jsonify({"success": True, "data": _row(c)})


@bp.post("")
@require_perm("master.companies.create")
def companies_create():
    data = request.get_json(force=True) or {}
    code = (data.get("code") or "").strip()
    name = (data.get("name") or "").strip()

    if not code or not name:
        return jsonify({
            "success": False,
            "error": {"message": "code and name are required"}
        }), 422

    if Company.query.filter_by(code=code).first():
        return jsonify({
            "success": False,
            "error": {"message": "code already exists"}
        }), 409

    c = Company(
        code=code,
        name=name,
        is_active=bool(data.get("is_active", True))
    )
    db.session.add(c)
    db.session.commit()
    return jsonify({"success": True, "data": _row(c)}), 201


@bp.put("/<int:cid>")
@require_perm("master.companies.update")
def companies_update(cid: int):
    c = Company.query.get_or_404(cid)
    data = request.get_json(force=True) or {}

    if "code" in data:
        new_code = (data.get("code") or "").strip()
        if not new_code:
            return jsonify({"success": False, "error": {"message": "code cannot be empty"}}), 422
        # ensure uniqueness
        if Company.query.filter(Company.id != cid, Company.code == new_code).first():
            return jsonify({"success": False, "error": {"message": "code already exists"}}), 409
        c.code = new_code

    if "name" in data:
        new_name = (data.get("name") or "").strip()
        if not new_name:
            return jsonify({"success": False, "error": {"message": "name cannot be empty"}}), 422
        c.name = new_name

    if "is_active" in data:
        c.is_active = bool(data.get("is_active"))

    db.session.commit()
    return jsonify({"success": True, "data": _row(c)})


# Soft delete variant (keeps history, avoids FK explosions)
@bp.delete("/<int:company_id>")
@require_perm("master.companies.delete")
def companies_delete(company_id: int):
    c = Company.query.get_or_404(company_id)
    _soft_delete_company(c)
    db.session.commit()
    return jsonify({"success": True, "data": {"id": company_id, "soft_deleted": True}})
