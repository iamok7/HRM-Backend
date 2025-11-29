from sqlalchemy import extract, case
from hrms_api.extensions import db
from hrms_api.models.payroll.pay_run import PayRun

def get_pay_run_for_period(company_id: int, year: int, month: int) -> PayRun | None:
    """
    Returns the latest finalized PayRun for this company and month/year.
    Priority by status: locked > approved > calculated > others (draft).
    Then by id desc.
    """
    status_order = case(
        (PayRun.status == "locked", 3),
        (PayRun.status == "approved", 2),
        (PayRun.status == "calculated", 1),
        else_=0,
    )

    return (
        PayRun.query
        .filter(
            PayRun.company_id == company_id,
            extract("year", PayRun.period_start) == year,
            extract("month", PayRun.period_start) == month,
        )
        .order_by(status_order.desc(), PayRun.id.desc())
        .first()
    )
