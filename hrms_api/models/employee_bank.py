from datetime import datetime
from hrms_api.extensions import db

class EmployeeBankAccount(db.Model):
    __tablename__ = "employee_bank_accounts"

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(
        db.Integer, db.ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True
    )
    bank_name = db.Column(db.String(80), nullable=False)
    ifsc = db.Column(db.String(20), nullable=False)
    account_number = db.Column(db.String(40), nullable=False)

    is_primary = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.Index("ix_empbank_primary", "employee_id", "is_primary"),
    )
