# hrms_api/models/payroll/__init__.py
# Import order matters: components first, then cycles/policy/profiles/adjustments,
# then pay_run (which depends on components).
from hrms_api.extensions import db  # noqa

from .components import SalaryComponent, EmployeeSalary
component = db.relationship(SalaryComponent, lazy="joined")
from .cycle import PayCycle
from .policy import PayPolicy
from .pay_profile import EmployeePayProfile
from .adjustments import Adjustment
from .stat_config import StatConfig
from .compliance import ComplianceEvent
from .pay_run import PayRun, PayRunItem, PayRunItemLine

__all__ = [
    "SalaryComponent", "EmployeeSalary",
    "PayCycle", "PayPolicy", "EmployeePayProfile",
    "Adjustment", "StatConfig", "ComplianceEvent",
    "PayRun", "PayRunItem", "PayRunItemLine",
]
