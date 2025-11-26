# hrms_api/models/attendance_punch.py
from __future__ import annotations

from datetime import datetime
from typing import Optional, Dict, Any

from sqlalchemy import (
    Index,
    UniqueConstraint,
    CheckConstraint,
    ForeignKey,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from hrms_api.extensions import db


class AttendancePunch(db.Model):
    """
    Canonical punch row. A single 'event' (usually an IN/OUT) captured from:
      - machine  : device dump / integrator feed
      - excel    : backfilled from spreadsheet upload
      - selfie   : user-submitted with geo + photo proof

    New columns in this revision:
      method       -> 'machine' | 'excel' | 'selfie'
      device_id    -> machine ID / integrator ID (nullable for excel/selfie)
      lat, lon     -> decimal degrees (nullable; present for selfie, optional for machine)
      accuracy_m   -> reported GPS accuracy (meters)
      photo_url    -> selfie photo location (S3/Blob/local path)
      face_score   -> reserved for later face-match confidence (0..1)
      source_meta  -> raw metadata payload (JSON) kept for audit/forensics
    """

    __tablename__ = "attendance_punches"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # tenancy & subject
    company_id: Mapped[int] = mapped_column(index=True, nullable=False)
    employee_id: Mapped[int] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE"), index=True, nullable=False
    )

    # when & what
    ts: Mapped[datetime] = mapped_column(index=True, nullable=False)  # timezone-aware in DB
    direction: Mapped[str] = mapped_column(
        db.String(8), nullable=False
    )  # 'in' | 'out' (normalized by routes/services)

    # provenance
    method: Mapped[Optional[str]] = mapped_column(
        db.String(16), index=True, nullable=True
    )  # 'machine' | 'excel' | 'selfie'
    device_id: Mapped[Optional[str]] = mapped_column(
        db.String(64), index=True, nullable=True
    )

    # geo (mainly for selfie; optional for machine if integrator provides)
    lat: Mapped[Optional[float]] = mapped_column(db.Numeric(9, 6), nullable=True)
    lon: Mapped[Optional[float]] = mapped_column(db.Numeric(9, 6), nullable=True)
    location_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("locations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    accuracy_m: Mapped[Optional[float]] = mapped_column(db.Numeric(6, 2), nullable=True)

    # selfie evidence
    photo_url: Mapped[Optional[str]] = mapped_column(db.Text, nullable=True)
    face_score: Mapped[Optional[float]] = mapped_column(db.Numeric(4, 3), nullable=True)

    # misc / audit
    note: Mapped[Optional[str]] = mapped_column(db.String(255), nullable=True)
    source_meta: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # relationships (keep lightweight here; join when needed)
    employee = relationship("Employee", lazy="joined")

    __table_args__ = (
        # soft domain checks
        CheckConstraint("direction in ('in','out')", name="ck_punch_direction"),
        CheckConstraint(
            "(method is null) or (method in ('machine','excel','selfie'))",
            name="ck_punch_method",
        ),
        # idempotency per source (partial uniques emulate by WHERE through indexes below)
        # NOTE: Postgres partial unique indexes are created in Alembic migration for clarity.
        # Here we still keep a defensive general unique across (employee_id, ts, direction) to
        # prevent exact duplicates regardless of method; relax/remove if your data allows same-direction re-entries.
        UniqueConstraint(
            "employee_id", "ts", "direction",
            name="uq_punch_employee_ts_dir"
        ),
        # performance indexes (some duplicated as explicit Index() below to set DESC, etc.)
        Index("ix_punch_company_ts", "company_id", "ts"),
        Index("ix_punch_employee_ts", "employee_id", "ts"),
        Index("ix_punch_method_ts", "method", "ts"),
    )

    # ---- convenience dumps ----
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "company_id": self.company_id,
            "employee_id": self.employee_id,
            "ts": self.ts.isoformat(),
            "direction": self.direction,
            "method": self.method,
            "device_id": self.device_id,
            "lat": float(self.lat) if self.lat is not None else None,
            "lon": float(self.lon) if self.lon is not None else None,
            "accuracy_m": float(self.accuracy_m) if self.accuracy_m is not None else None,
            "photo_url": self.photo_url,
            "face_score": float(self.face_score) if self.face_score is not None else None,
            "note": self.note,
            "source_meta": self.source_meta,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    # ---- normalization helpers (used by routes/services) ----
    @staticmethod
    def normalize_direction(raw: str) -> str:
        if raw is None:
            return raw
        s = str(raw).strip().lower()
        if s in ("1", "in", "i", "enter", "entry"):
            return "in"
        if s in ("0", "out", "o", "exit", "leave"):
            return "out"
        # fallback to original; CheckConstraint will guard invalids on commit
        return s
