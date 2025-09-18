from flask import Blueprint, request, jsonify
from hrms_api.extensions import db
from hrms_api.models.employee import Employee
from hrms_api.models.employee_address import EmployeeAddress
from hrms_api.models.employee_bank import EmployeeBankAccount
from flask_jwt_extended import jwt_required
from hrms_api.common.auth import requires_roles

from hrms_api.common.auth import requires_perms
from flask_jwt_extended import jwt_required

bp = Blueprint("employee_extras", __name__, url_prefix="/api/v1/employees")

def _ok(data=None, status=200, **meta):
    payload = {"success": True, "data": data}
    if meta: payload["meta"] = meta
    return jsonify(payload), status

def _fail(msg, status=400):
    return jsonify({"success": False, "error": {"message": msg}}), status

def _ensure_employee(eid:int):
    e = Employee.query.get(eid)
    if not e: return None, _fail("Employee not found", 404)
    return e, None

# ---------- Addresses ----------
def _addr_row(a: EmployeeAddress):
    return {
        "id": a.id, "employee_id": a.employee_id, "type": a.type,
        "line1": a.line1, "line2": a.line2, "city": a.city, "state": a.state,
        "pincode": a.pincode, "country": a.country,
        "is_primary": a.is_primary,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }

@bp.get("/<int:eid>/addresses")
@jwt_required()
def list_addresses(eid):
    e, err = _ensure_employee(eid);  # type: ignore
    if err: return err
    items = EmployeeAddress.query.filter_by(employee_id=eid).order_by(EmployeeAddress.id.desc()).all()
    return _ok([_addr_row(i) for i in items])

@bp.post("/<int:eid>/addresses")
@requires_roles("admin")
def create_address(eid):
    e, err = _ensure_employee(eid);  # type: ignore
    if err: return err
    d = request.get_json(silent=True, force=True) or {}
    atype = (d.get("type") or "").strip().lower()
    if atype not in ("current", "permanent"):
        return _fail("type must be 'current' or 'permanent'", 422)
    for req in ("line1", "city"):
        if not (d.get(req) or "").strip():
            return _fail(f"{req} is required", 422)

    a = EmployeeAddress(
        employee_id=eid, type=atype,
        line1=d.get("line1").strip(), line2=(d.get("line2") or "").strip() or None,
        city=d.get("city").strip(), state=(d.get("state") or "").strip() or None,
        pincode=(d.get("pincode") or "").strip() or None,
        country=(d.get("country") or "India").strip(),
        is_primary=bool(d.get("is_primary", False))
    )
    if a.is_primary:
        EmployeeAddress.query.filter_by(employee_id=eid, is_primary=True).update({"is_primary": False})
    db.session.add(a); db.session.commit()
    return _ok(_addr_row(a), status=201)

@bp.put("/<int:eid>/addresses/<int:aid>")
@requires_perms("employee.extra.update")
@requires_roles("admin")
def update_address(eid, aid):
    e, err = _ensure_employee(eid);  # type: ignore
    if err: return err
    a = EmployeeAddress.query.filter_by(employee_id=eid, id=aid).first()
    if not a: return _fail("Address not found", 404)
    d = request.get_json(silent=True, force=True) or {}
    if "type" in d:
        atype = (d.get("type") or "").strip().lower()
        if atype not in ("current", "permanent"):
            return _fail("type must be 'current' or 'permanent'", 422)
        a.type = atype
    for k in ("line1","line2","city","state","pincode","country"):
        if k in d: setattr(a, k, (d[k] or "").strip() or None)
    if "is_primary" in d:
        isp = bool(d["is_primary"])
        if isp:
            EmployeeAddress.query.filter(EmployeeAddress.employee_id==eid, EmployeeAddress.id!=aid, EmployeeAddress.is_primary==True).update({"is_primary": False})  # noqa: E712
        a.is_primary = isp
    db.session.commit()
    return _ok(_addr_row(a))

@bp.delete("/<int:eid>/addresses/<int:aid>")
@requires_roles("admin")
def delete_address(eid, aid):
    a = EmployeeAddress.query.filter_by(employee_id=eid, id=aid).first()
    if not a: return _fail("Address not found", 404)
    db.session.delete(a); db.session.commit()
    return _ok({"id": aid, "deleted": True})

# ---------- Bank Accounts ----------
def _mask(num: str | None):
    if not num: return None
    n = str(num)
    return ("*" * max(len(n)-4, 0)) + n[-4:]

def _bank_row(b: EmployeeBankAccount):
    return {
        "id": b.id, "employee_id": b.employee_id,
        "bank_name": b.bank_name, "ifsc": b.ifsc,
        "account_number": _mask(b.account_number),
        "is_primary": b.is_primary,
        "created_at": b.created_at.isoformat() if b.created_at else None,
    }

@bp.get("/<int:eid>/banks")
@jwt_required()
def list_banks(eid):
    e, err = _ensure_employee(eid);  # type: ignore
    if err: return err
    items = EmployeeBankAccount.query.filter_by(employee_id=eid).order_by(EmployeeBankAccount.id.desc()).all()
    return _ok([_bank_row(i) for i in items])

@bp.post("/<int:eid>/banks")
@requires_roles("admin")
def create_bank(eid):
    e, err = _ensure_employee(eid);  # type: ignore
    if err: return err
    d = request.get_json(silent=True, force=True) or {}
    for req in ("bank_name","ifsc","account_number"):
        if not (d.get(req) or "").strip():
            return _fail(f"{req} is required", 422)
    b = EmployeeBankAccount(
        employee_id=eid,
        bank_name=d["bank_name"].strip(),
        ifsc=d["ifsc"].strip().upper(),
        account_number=d["account_number"].strip(),
        is_primary=bool(d.get("is_primary", False))
    )
    if b.is_primary:
        EmployeeBankAccount.query.filter_by(employee_id=eid, is_primary=True).update({"is_primary": False})
    db.session.add(b); db.session.commit()
    return _ok(_bank_row(b), status=201)

@bp.put("/<int:eid>/banks/<int:bid>")
@requires_roles("admin")
def update_bank(eid, bid):
    e, err = _ensure_employee(eid);  # type: ignore
    if err: return err
    b = EmployeeBankAccount.query.filter_by(employee_id=eid, id=bid).first()
    if not b: return _fail("Bank account not found", 404)
    d = request.get_json(silent=True, force=True) or {}
    for k in ("bank_name","ifsc","account_number"):
        if k in d and d[k] is not None:
            val = str(d[k]).strip()
            if k == "ifsc": val = val.upper()
            setattr(b, k, val)
    if "is_primary" in d:
        isp = bool(d["is_primary"])
        if isp:
            EmployeeBankAccount.query.filter(EmployeeBankAccount.employee_id==eid, EmployeeBankAccount.id!=bid, EmployeeBankAccount.is_primary==True).update({"is_primary": False})  # noqa: E712
        b.is_primary = isp
    db.session.commit()
    return _ok(_bank_row(b))

@bp.delete("/<int:eid>/banks/<int:bid>")
@requires_roles("admin")
def delete_bank(eid, bid):
    b = EmployeeBankAccount.query.filter_by(employee_id=eid, id=bid).first()
    if not b: return _fail("Bank account not found", 404)
    db.session.delete(b); db.session.commit()
    return _ok({"id": bid, "deleted": True})
