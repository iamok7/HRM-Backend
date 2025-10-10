from __future__ import annotations

from flask import Blueprint, request, jsonify
from sqlalchemy import or_
from datetime import datetime, date
from hrms_api.extensions import db
from hrms_api.models.employee import Employee
from hrms_api.models.master import Company, Location, Department, Designation, Grade, CostCenter
from flask_jwt_extended import jwt_required
from hrms_api.common.auth import requires_roles, requires_perms

bp = Blueprint("employees", __name__, url_prefix="/api/v1/employees")

# ---------- envelopes ----------
def _ok(data=None, status=200, **meta):
    payload = {"success": True, "data": data}
    if meta: payload["meta"] = meta
    return jsonify(payload), status

def _fail(msg, status=400, code=None, detail=None):
    err = {"message": msg}
    if code: err["code"] = code
    if detail: err["detail"] = detail
    return jsonify({"success": False, "error": err}), status

# ---------- helpers ----------
def _int_arg(*names: str):
    """
    Return first present arg among names (camelCase/snake_case),
    cast to int. If present but invalid -> ValueError (422).
    """
    for n in names:
        if n in request.args:
            v = request.args.get(n)
            if v in (None, "", "null"): return None
            try:
                return int(v)
            except Exception:
                raise ValueError(f"{n} must be integer")
    return None

def _bool_arg(*names: str):
    for n in names:
        if n in request.args:
            v = (request.args.get(n) or "").lower()
            if v in ("true","1","yes"):  return True
            if v in ("false","0","no"):  return False
            raise ValueError(f"{n} must be true/false")
    return None

def _as_int(val, field_name):
    if val is None or val == "":
        return None
    try:
        return int(val)
    except Exception:
        raise ValueError(f"{field_name} must be integer")

def _page_limit():
    """
    Backward compatible:
      - page (default 1)
      - limit (default 20)  [original]
      - size (alias for limit)
    Clamped to [1,100]
    """
    # page
    try:
        page = max(int(request.args.get("page", 1)), 1)
    except Exception:
        page = 1
    # limit / size
    raw_limit = request.args.get("limit", None)
    raw_size  = request.args.get("size", None)
    use = raw_limit if raw_limit is not None else raw_size
    try:
        limit = int(use) if use is not None else 20
        limit = min(max(limit, 1), 100)
    except Exception:
        limit = 20
    return page, limit

def _parse_date(val):
    if not val: return None
    if isinstance(val, (date,)): return val
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d"):
        try: return datetime.strptime(val, fmt).date()
        except Exception: pass
    return None

def _row(x: Employee):
    return {
        "id": x.id,
        "code": x.code,
        "email": x.email,
        "first_name": x.first_name,
        "last_name": x.last_name,
        "phone": x.phone,

        "company_id": x.company_id,
        "company_name": x.company.name if x.company else None,
        "location_id": x.location_id,
        "location_name": x.location.name if x.location else None,
        "department_id": x.department_id,
        "department_name": x.department.name if x.department else None,
        "designation_id": x.designation_id,
        "designation_name": x.designation.name if x.designation else None,
        "grade_id": x.grade_id,
        "grade_name": x.grade.name if x.grade else None,
        "cost_center_id": x.cost_center_id,
        "cost_center_code": x.cost_center.code if x.cost_center else None,

        "manager_id": x.manager_id,
        "manager_name": (
            (x.manager.first_name or "")
            + ((" " + x.manager.last_name) if x.manager and x.manager.last_name else "")
        ) if x.manager else None,

        "employment_type": x.employment_type,
        "status": x.status,
        "doj": x.doj.isoformat() if x.doj else None,
        "dol": x.dol.isoformat() if x.dol else None,
        "created_at": x.created_at.isoformat() if x.created_at else None,
    }

# ---------- routes ----------

# List (JWT only; no perm — matches your current working code)
@bp.get("")
@jwt_required()
def list_employees():
    q = Employee.query

    # filters (camelCase + snake_case aliases)
    try:
        cid = _int_arg("companyId", "company_id")
        did = _int_arg("deptId", "department_id")
        loc = _int_arg("locationId", "location_id")
        des = _int_arg("designationId", "designation_id")
        grd = _int_arg("gradeId", "grade_id")
    except ValueError as ex:
        return _fail(str(ex), 422)

    if cid: q = q.filter(Employee.company_id == cid)
    if did: q = q.filter(Employee.department_id == did)
    if loc: q = q.filter(Employee.location_id == loc)
    if des: q = q.filter(Employee.designation_id == des)
    if grd: q = q.filter(Employee.grade_id == grd)

    # status (string)
    status = (request.args.get("status") or "").strip()
    if status:
        q = q.filter(Employee.status == status.lower())

    # is_active (optional bool) — supports both `is_active` and `isActive`
    try:
        is_act = _bool_arg("is_active", "isActive")
        if is_act is True:
            q = q.filter(Employee.is_active.is_(True))
        elif is_act is False:
            q = q.filter(Employee.is_active.is_(False))
    except ValueError as ex:
        return _fail(str(ex), 422)

    # search
    s = (request.args.get("q") or "").strip()
    if s:
        like = f"%{s}%"
        q = q.filter(or_(Employee.code.ilike(like),
                         Employee.email.ilike(like),
                         Employee.first_name.ilike(like),
                         Employee.last_name.ilike(like)))

    page, limit = _page_limit()
    total = q.count()
    items = q.order_by(Employee.id.desc()).offset((page-1)*limit).limit(limit).all()
    return _ok([_row(i) for i in items], page=page, limit=limit, total=total)

# Get (JWT only)
@bp.get("/<int:eid>")
@jwt_required()
def get_employee(eid: int):
    x = Employee.query.get(eid)
    if not x: return _fail("Employee not found", 404)
    return _ok(_row(x))

# Create (perm + role: admin)  — keep your existing RBAC shape
# ------- Create (patched)
@bp.post("")
@requires_perms("employee.create")
@requires_roles("admin")
def create_employee():
    d = request.get_json(silent=True, force=True) or {}
    try:
        cid  = _as_int(d.get("company_id"),    "company_id")
        lid  = _as_int(d.get("location_id"),   "location_id")
        did  = _as_int(d.get("department_id"), "department_id")
        dsid = _as_int(d.get("designation_id"),"designation_id")
        gid  = _as_int(d.get("grade_id"),      "grade_id")
        ccid = _as_int(d.get("cost_center_id"),"cost_center_id")
        mid  = _as_int(d.get("manager_id"),    "manager_id")
    except ValueError as ex:
        return _fail(str(ex), 422)

    code  = (d.get("code") or "").strip()
    email = (d.get("email") or "").strip().lower()
    first = (d.get("first_name") or "").strip()

    if not (code and email and first and cid):
        return _fail("company_id, code, first_name, email are required", 422)

    # FK checks (now using proper ints)
    if not Company.query.get(cid): return _fail("Invalid company_id", 422)
    for fk, model, name in [
        (lid,  Location,    "location_id"),
        (did,  Department,  "department_id"),
        (dsid, Designation, "designation_id"),
        (gid,  Grade,       "grade_id"),
        (ccid, CostCenter,  "cost_center_id"),
        (mid,  Employee,    "manager_id"),
    ]:
        if fk and not model.query.get(fk): return _fail(f"Invalid {name}", 422)

    # Uniques (cid is int now, so Postgres sees INTEGER = INTEGER)
    if Employee.query.filter_by(company_id=cid, code=code).first():
        return _fail("Employee code already exists for this company", 409)
    if Employee.query.filter_by(email=email).first():
        return _fail("Email already exists", 409)

    x = Employee(
        company_id=cid,
        location_id=lid,
        department_id=did,
        designation_id=dsid,
        grade_id=gid,
        cost_center_id=ccid,
        manager_id=mid,
        code=code,
        email=email,
        first_name=first,
        last_name=(d.get("last_name") or "").strip() or None,
        phone=(d.get("phone") or "").strip() or None,
        employment_type=(d.get("employment_type") or "fulltime").lower(),
        status=(d.get("status") or "active").lower(),
        doj=_parse_date(d.get("doj")),
        dol=_parse_date(d.get("dol")),
    )
    db.session.add(x); db.session.commit()
    return _ok(_row(x), status=201)


# Update (perm + role: admin)
# ------- Update (patched)
@bp.put("/<int:eid>")
@requires_perms("employee.update")
@requires_roles("admin")
def update_employee(eid: int):
    x = Employee.query.get(eid)
    if not x: return _fail("Employee not found", 404)
    d = request.get_json(silent=True, force=True) or {}

    # company_id (coerce to int if present)
    if "company_id" in d:
        try:
            cid = _as_int(d.get("company_id"), "company_id")
        except ValueError as ex:
            return _fail(str(ex), 422)
        if not Company.query.get(cid): return _fail("Invalid company_id", 422)
        x.company_id = cid

    # code (uniqueness within company)
    if "code" in d:
        new_code = (d["code"] or "").strip()
        if not new_code: return _fail("code cannot be empty", 422)
        company_for_check = x.company_id
        if "company_id" in d:
            try:
                company_for_check = _as_int(d.get("company_id"), "company_id")
            except ValueError as ex:
                return _fail(str(ex), 422)
        if Employee.query.filter(
            Employee.id != eid,
            Employee.company_id == company_for_check,
            Employee.code == new_code
        ).first():
            return _fail("Employee code already exists for this company", 409)
        x.code = new_code

    # email
    if "email" in d:
        new_email = (d["email"] or "").strip().lower()
        if not new_email: return _fail("email cannot be empty", 422)
        if Employee.query.filter(Employee.id != eid, Employee.email == new_email).first():
            return _fail("Email already exists", 409)
        x.email = new_email

    # FK fields (coerce to ints)
    for key, model in [
        ("location_id",    Location),
        ("department_id",  Department),
        ("designation_id", Designation),
        ("grade_id",       Grade),
        ("cost_center_id", CostCenter),
        ("manager_id",     Employee),
    ]:
        if key in d:
            try:
                val = _as_int(d.get(key), key) if d.get(key) is not None else None
            except ValueError as ex:
                return _fail(str(ex), 422)
            if val and not model.query.get(val): return _fail(f"Invalid {key}", 422)
            setattr(x, key, val)

    for key in ("first_name", "last_name", "phone", "employment_type", "status"):
        if key in d: setattr(x, key, (d[key] or "").strip() or None)
    if "doj" in d: x.doj = _parse_date(d["doj"])
    if "dol" in d: x.dol = _parse_date(d["dol"])

    db.session.commit()
    return _ok(_row(x))


# Delete (soft) (perm + role: admin)
@bp.delete("/<int:eid>")
@requires_perms("employee.delete")
@requires_roles("admin")
def delete_employee(eid: int):
    x = Employee.query.get(eid)
    if not x: return _fail("Employee not found", 404)
    x.status = "inactive"
    if not x.dol: x.dol = date.today()
    db.session.commit()
    return _ok({"id": eid, "status": "inactive", "dol": x.dol.isoformat()})
