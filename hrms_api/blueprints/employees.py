from flask import Blueprint, request, jsonify
from sqlalchemy import or_
from datetime import datetime, date
from hrms_api.extensions import db
from hrms_api.models.employee import Employee
from hrms_api.models.master import Company, Location, Department, Designation, Grade, CostCenter
from flask_jwt_extended import jwt_required
from hrms_api.common.auth import requires_roles


from hrms_api.common.auth import requires_perms
from flask_jwt_extended import jwt_required

bp = Blueprint("employees", __name__, url_prefix="/api/v1/employees")

def _ok(data=None, status=200, **meta):
    payload = {"success": True, "data": data}
    if meta: payload["meta"] = meta
    return jsonify(payload), status

def _fail(msg, status=400):
    return jsonify({"success": False, "error": {"message": msg}}), status

def _page_limit():
    try:
        page  = max(int(request.args.get("page", 1)), 1)
        limit = min(max(int(request.args.get("limit", 20)), 1), 100)
    except Exception:
        page, limit = 1, 20
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
        "manager_name": (x.manager.first_name + (" " + x.manager.last_name if x.manager and x.manager.last_name else "")) if x.manager else None,
        "employment_type": x.employment_type,
        "status": x.status,
        "doj": x.doj.isoformat() if x.doj else None,
        "dol": x.dol.isoformat() if x.dol else None,
        "created_at": x.created_at.isoformat() if x.created_at else None,
    }

# ------- List
@bp.get("")
@jwt_required()
def list_employees():
    q = Employee.query
    # filters
    cid = request.args.get("companyId", type=int)
    if cid: q = q.filter(Employee.company_id == cid)
    did = request.args.get("deptId", type=int)
    if did: q = q.filter(Employee.department_id == did)
    loc = request.args.get("locationId", type=int)
    if loc: q = q.filter(Employee.location_id == loc)
    des = request.args.get("designationId", type=int)
    if des: q = q.filter(Employee.designation_id == des)
    grd = request.args.get("gradeId", type=int)
    if grd: q = q.filter(Employee.grade_id == grd)
    status = request.args.get("status")
    if status: q = q.filter(Employee.status == status.lower())
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

# ------- Get
@bp.get("/<int:eid>")
@jwt_required()
def get_employee(eid: int):
    x = Employee.query.get(eid)
    if not x: return _fail("Employee not found", 404)
    return _ok(_row(x))

# ------- Create
@bp.post("")
@requires_perms("employee.create")
@requires_roles("admin")
def create_employee():
    d = request.get_json(silent=True, force=True) or {}
    code = (d.get("code") or "").strip()
    email = (d.get("email") or "").strip().lower()
    first = (d.get("first_name") or "").strip()
    cid = d.get("company_id")
    if not (code and email and first and cid):
        return _fail("company_id, code, first_name, email are required", 422)

    # FK checks
    if not Company.query.get(cid): return _fail("Invalid company_id", 422)
    for fk, model, name in [
        (d.get("location_id"),    Location,    "location_id"),
        (d.get("department_id"),  Department,  "department_id"),
        (d.get("designation_id"), Designation, "designation_id"),
        (d.get("grade_id"),       Grade,       "grade_id"),
        (d.get("cost_center_id"), CostCenter,  "cost_center_id"),
        (d.get("manager_id"),     Employee,    "manager_id"),
    ]:
        if fk and not model.query.get(fk): return _fail(f"Invalid {name}", 422)

    # Uniques
    if Employee.query.filter_by(company_id=cid, code=code).first():
        return _fail("Employee code already exists for this company", 409)
    if Employee.query.filter_by(email=email).first():
        return _fail("Email already exists", 409)

    x = Employee(
        company_id=cid,
        location_id=d.get("location_id"),
        department_id=d.get("department_id"),
        designation_id=d.get("designation_id"),
        grade_id=d.get("grade_id"),
        cost_center_id=d.get("cost_center_id"),
        manager_id=d.get("manager_id"),
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

# ------- Update
@bp.put("/<int:eid>")
@requires_perms("employee.update")
@requires_roles("admin")
def update_employee(eid: int):
    x = Employee.query.get(eid)
    if not x: return _fail("Employee not found", 404)
    d = request.get_json(silent=True, force=True) or {}

    if "company_id" in d:
        cid = d["company_id"]
        if not Company.query.get(cid): return _fail("Invalid company_id", 422)
        x.company_id = cid
    if "code" in d:
        new_code = (d["code"] or "").strip()
        if not new_code: return _fail("code cannot be empty", 422)
        if Employee.query.filter(Employee.id != eid, Employee.company_id == (d.get("company_id") or x.company_id), Employee.code == new_code).first():
            return _fail("Employee code already exists for this company", 409)
        x.code = new_code
    if "email" in d:
        new_email = (d["email"] or "").strip().lower()
        if not new_email: return _fail("email cannot be empty", 422)
        if Employee.query.filter(Employee.id != eid, Employee.email == new_email).first():
            return _fail("Email already exists", 409)
        x.email = new_email

    for attr, model, key in [
        ("location_id",    Location,    "location_id"),
        ("department_id",  Department,  "department_id"),
        ("designation_id", Designation, "designation_id"),
        ("grade_id",       Grade,       "grade_id"),
        ("cost_center_id", CostCenter,  "cost_center_id"),
        ("manager_id",     Employee,    "manager_id"),
    ]:
        if key in d:
            val = d[key]
            if val and not model.query.get(val): return _fail(f"Invalid {key}", 422)
            setattr(x, key, val)

    for key in ("first_name", "last_name", "phone", "employment_type", "status"):
        if key in d: setattr(x, key, (d[key] or "").strip() or None)
    if "doj" in d: x.doj = _parse_date(d["doj"])
    if "dol" in d: x.dol = _parse_date(d["dol"])

    db.session.commit()
    return _ok(_row(x))

# ------- Delete (soft)
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
