# apps/backend/hrms_api/seed_rbac.py  (if you keep it at package root)
# If you move it to hrms_api/scripts/seed_rbac.py, only the CLI import path will change (step 2).

from hrms_api.extensions import db
from hrms_api.models.security import Role, Permission, RolePermission
from hrms_api.models.user import User

DEFAULT_ROLES = [
    ("admin", "Administrator"),
    ("hr", "HR"),
    ("manager", "Manager"),
    ("employee", "Employee"),
]

DEFAULT_PERMS = [
    # Masters
    "master.companies.read","master.companies.create","master.companies.update","master.companies.delete",
    "master.departments.read","master.designations.read","master.grades.read","master.locations.read",

    # Employees
    "employee.read","employee.create","employee.update","employee.delete",
    "employee.extra.read","employee.extra.update",

    # Attendance
    "attendance.punch.read","attendance.punch.create",
    "attendance.missed.read","attendance.missed.approve",
    "attendance.assign.read","attendance.assign.create",
    "attendance.calendar.read","attendance.monthly.read",

    # Leave
    "leave.types.read","leave.types.manage","leave.balance.read","leave.balance.adjust",
    "leave.request.create","leave.request.approve",
    # Leave Policies
    "leave.policies.view", "leave.policies.manage", "leave.policies.sync_balances",

    # Reports (RGS)
    "rgs.report.view",
    "rgs.report.run",
    "rgs.report.run.attendance",
    "rgs.report.run.payroll",
    "rgs.report.run.compliance",

    # Payroll (MVP placeholder)
    "payroll.run","payroll.view",
]

ROLE_PERM_MAP = {
    "admin": DEFAULT_PERMS,
    "hr": DEFAULT_PERMS,
    "manager": [
        "employee.read",
        "attendance.punch.read","attendance.missed.read","attendance.missed.approve",
        "attendance.calendar.read","attendance.monthly.read",
        "leave.request.approve",
        "rgs.report.view", "rgs.report.run", "rgs.report.run.attendance",
    ],
    "employee": [
        "employee.read",
        "attendance.punch.read","attendance.punch.create",
        "attendance.missed.read",
        "leave.request.create","leave.balance.read",
        "rgs.report.view", "rgs.report.run", "rgs.report.run.attendance",
    ],
}

def _ensure_roles():
    code_to_role = {}
    for code, name in DEFAULT_ROLES:
        r = Role.query.filter_by(code=code).first()
        if not r:
            r = Role(code=code)
            db.session.add(r)
            db.session.flush()
        code_to_role[code] = r
    return code_to_role

def _ensure_permissions():
    code_to_perm = {}
    for code in DEFAULT_PERMS:
        p = Permission.query.filter_by(code=code).first()
        if not p:
            p = Permission(code=code, name=code.replace(".", " ").title())
            db.session.add(p)
            db.session.flush()
        code_to_perm[code] = p
    return code_to_perm

def _map_role_perms(code_to_role, code_to_perm):
    for rcode, perms in ROLE_PERM_MAP.items():
        r = code_to_role[rcode]
        # existing mapping cache (avoid duplicates)
        existing = {(rp.role_id, rp.permission_id) for rp in r.permissions}
        for pcode in perms:
            p = code_to_perm[pcode]
            key = (r.id, p.id)
            if key not in existing:
                db.session.add(RolePermission(role_id=r.id, permission_id=p.id))

def _assign_admin_role():
    # optional convenience: attach 'admin' role to a known admin user if present
    admin_user = User.query.filter(User.email.in_([
        "admin@hrms.local", "admin@bizhrs.com", "admin@yourcompany.com"
    ])).first()
    if not admin_user:
        return
    from hrms_api.models.security import UserRole
    has_admin = any(ur.role.code == "admin" for ur in admin_user.user_roles)
    if not has_admin:
        admin_role = Role.query.filter_by(code="admin").first()
        if admin_role:
            db.session.add(UserRole(user_id=admin_user.id, role_id=admin_role.id))

def run():
    code_to_role = _ensure_roles()
    code_to_perm = _ensure_permissions()
    _map_role_perms(code_to_role, code_to_perm)
    _assign_admin_role()
    db.session.commit()
    return {"ok": True, "roles": len(DEFAULT_ROLES), "perms": len(DEFAULT_PERMS)}
