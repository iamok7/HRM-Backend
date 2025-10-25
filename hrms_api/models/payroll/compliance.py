from datetime import datetime
from hrms_api.extensions import db

class ComplianceEvent(db.Model):
    __tablename__ = "compliance_events"

    id = db.Column(db.Integer, primary_key=True)
    type = db.Column(db.Enum("PF", "ESIC", "PT", "LWF", name="compliance_type_enum"), nullable=False)
    period = db.Column(db.String(7), nullable=False)  # YYYY-MM
    file_path = db.Column(db.String(255))
    challan_ref = db.Column(db.String(100))
    status = db.Column(db.Enum("draft", "filed", "paid", name="compliance_status_enum"), default="draft")
    submitted_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    submitted_at = db.Column(db.DateTime)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
