# hrms_api/services/attendance_engine.py  (or where you keep it)
from __future__ import annotations

from datetime import datetime, date, time as _time, timedelta
from typing import Optional, List, Tuple
import logging

from hrms_api.extensions import db
from hrms_api.models.attendance_punch import AttendancePunch

log = logging.getLogger(__name__)

# ---- dynamic column detection ----
"""
We support both old and new schemas:

New (canonical) schema:
  - ts        : punch timestamp (tz-aware)
  - direction : 'in' | 'out'

Legacy schemas (for backwards-compat):
  - punch_dt  : punch timestamp
  - kind      : 'in' | 'out'
  - direction : 'IN' | 'OUT'
"""

HAS_PUNCH_DT = hasattr(AttendancePunch, "punch_dt")
HAS_DIRECTION = hasattr(AttendancePunch, "direction")

TS_COL = getattr(AttendancePunch, "punch_dt", None) or getattr(AttendancePunch, "ts")
DIR_COL = getattr(AttendancePunch, "direction", None) or getattr(AttendancePunch, "kind", None)


def _dt(d: date, t: _time) -> datetime:
    return datetime.combine(d, t)


def _to_model_dir(direction: str) -> str | None:
    """
    Map 'IN'/'OUT' (or 'in'/'out') to whatever the model column expects.

    For our new canonical model (ts + direction):
      - we always write lowercase 'in' / 'out'
    For very old schemas that used uppercase direction, we fall back to 'IN'/'OUT'.
    """
    if not DIR_COL:
        return None

    raw = (direction or "").strip()
    if not raw:
        return None

    upper = raw.upper()

    # canonical / semi-modern schema: ts + kind OR ts + direction
    if TS_COL is not None and TS_COL.key == "ts" and DIR_COL.key in ("kind", "direction"):
        # Use same normalization as model helper if present
        if hasattr(AttendancePunch, "normalize_direction"):
            return AttendancePunch.normalize_direction(raw) or None
        # fallback: simple begins-with
        return "in" if upper.startswith("I") else "out"

    # very old style where column itself stored 'IN'/'OUT'
    return "IN" if upper.startswith("I") else "OUT"


def _from_model_dir(v) -> str:
    """
    Normalize model value to 'IN' / 'OUT' for pairing logic.
    Model may store:
      - 'in' / 'out'
      - 'IN' / 'OUT'
      - 'i' / 'o'
    """
    if v is None:
        return "IN"

    s = str(v)

    # if column name is "kind" or "direction" we assume in/out semantics
    if DIR_COL and DIR_COL.key in ("kind", "direction"):
        return "IN" if s.lower().startswith("i") else "OUT"

    # generic fallback
    return "IN" if s.upper().startswith("I") else "OUT"


# ---------- public API ----------

def upsert_manual_punch(
    employee_id: int,
    work_date: date,
    direction: str,
    at_time: _time,
    source: str = "missed",
) -> int:
    """
    Idempotently create/update a single manual punch:

      - employee_id
      - work_date + at_time
      - direction : 'IN' or 'OUT' (or lowercase)

    For canonical AttendancePunch (ts + direction):
      - writes ts = work_date + at_time
      - writes direction = 'in' | 'out'

    Returns: punch row id.
    """
    if TS_COL is None:
        raise RuntimeError("AttendancePunch must have a datetime column (punch_dt or ts)")

    punch_dt = _dt(work_date, at_time)

    q = AttendancePunch.query.filter(
        AttendancePunch.employee_id == employee_id,
        TS_COL == punch_dt,
    )

    model_dir = _to_model_dir(direction)
    if DIR_COL is not None and model_dir is not None:
        q = q.filter(DIR_COL == model_dir)

    row = q.first()
    if row:
        # Optionally annotate 'source' field if present & empty
        if hasattr(row, "source") and not getattr(row, "source", None):
            row.source = source
            db.session.commit()
        return row.id

    fields = {
        "employee_id": employee_id,
        TS_COL.key: punch_dt,
    }
    if DIR_COL is not None and model_dir is not None:
        fields[DIR_COL.key] = model_dir

    # soft/optional feature flags for richer models
    if hasattr(AttendancePunch, "source"):
        fields["source"] = source
    if hasattr(AttendancePunch, "method") and not hasattr(AttendancePunch, "source"):
        # For canonical punch model, "method" may be better than "source"
        # We treat these manual upserts as "excel" or generic "manual"
        fields["method"] = source or "manual"
    if hasattr(AttendancePunch, "is_manual"):
        fields["is_manual"] = True

    row = AttendancePunch(**fields)
    db.session.add(row)
    db.session.commit()
    return row.id


def recompute_daily(employee_id: int, work_date: date) -> dict:
    """
    Recompute simple daily summary from punches:

    - Looks at punches in a window around the work_date (shift-aware if possible).
    - Pairs IN/OUT events (naive left-right pairing).
    - Computes:
        * work_mins (int)
        * status: "Present" if work_mins > 0 else "Absent"
    - If DailyStatus model exists, upserts:
        * work_mins
        * status

    Returns JSON-safe dict:

    {
      "employee_id": 123,
      "date": "2025-10-10",
      "work_mins": 480,
      "status": "Present",
      "daily_upserted": true,
      "window": {
        "start": "2025-10-10T00:00:00",
        "end": "2025-10-11T06:00:00"
      },
      "punch_count": 4
    }
    """
    # default wide window: 6h before and after the day
    win_start = datetime.combine(work_date, datetime.min.time()) - timedelta(hours=6)
    win_end = datetime.combine(work_date, datetime.max.time()) + timedelta(hours=6)

    # optional: narrower window based on assigned Shift (best-effort)
    try:
        from hrms_api.models.attendance_assignment import EmployeeShiftAssignment as _Assign
        from hrms_api.models.attendance import Shift as _Shift

        rec = (
            db.session.query(_Assign, _Shift)
            .join(_Shift, _Shift.id == _Assign.shift_id)
            .filter(_Assign.employee_id == employee_id)
            .filter(_Assign.start_date <= work_date)
            .filter((_Assign.end_date == None) | (_Assign.end_date >= work_date))  # noqa: E711
            .order_by(_Assign.start_date.desc())
            .first()
        )
        if rec:
            _, s = rec
            st = getattr(s, "start_time", None)
            et = getattr(s, "end_time", None)

            # shift window only makes sense when we have both times
            if st and et:
                # expand by ±3h around shift for safety
                win_start = _dt(work_date, st) - timedelta(hours=3)

                # compute scheduled end date/time (respect night shift)
                is_night = bool(getattr(s, "is_night", getattr(s, "is_night_shift", False)))
                et_day = work_date
                if is_night or et <= st:
                    et_day = work_date + timedelta(days=1)
                et_dt = _dt(et_day, et)

                win_end = et_dt + timedelta(hours=3)
    except Exception:
        # if any error, we just retain wider default window
        pass

    # fetch punches within window
    qp = (
        AttendancePunch.query.filter(AttendancePunch.employee_id == employee_id)
        .filter(TS_COL >= win_start)
        .filter(TS_COL <= win_end)
        .order_by(TS_COL.asc())
    )
    punches: List[AttendancePunch] = qp.all()

    events: List[Tuple[str, datetime]] = []
    for p in punches:
        dtv: datetime = getattr(p, TS_COL.key)
        if DIR_COL is not None:
            raw_dir = getattr(p, DIR_COL.key)
        else:
            raw_dir = "IN"
        dir_norm = _from_model_dir(raw_dir)
        events.append((dir_norm, dtv))

    work_secs = 0.0
    open_in: Optional[datetime] = None

    # Naive pairing: every IN opens, next OUT closes.
    for dirv, dtv in events:
        if dirv == "IN":
            open_in = dtv
        else:  # OUT
            if open_in and dtv > open_in:
                work_secs += (dtv - open_in).total_seconds()
            open_in = None

    work_mins = int(round(work_secs / 60.0))
    status = "Present" if work_mins > 0 else "Absent"

    # optional daily summary upsert (best-effort)
    updated = False
    try:
        from hrms_api.models.attendance import DailyStatus as _Daily

        row = (
            _Daily.query.filter(
                _Daily.employee_id == employee_id,
                _Daily.work_date == work_date,
            ).first()
        )
        if not row:
            row = _Daily(employee_id=employee_id, work_date=work_date)
            db.session.add(row)

        if hasattr(row, "work_mins"):
            row.work_mins = work_mins
        if hasattr(row, "status"):
            row.status = status

        db.session.commit()
        updated = True
    except Exception:
        log.info("[attendance.recompute_daily] daily upsert skipped", exc_info=True)

    return {
        "employee_id": employee_id,
        "date": work_date.isoformat(),
        "work_mins": work_mins,
        "status": status,
        "daily_upserted": updated,
        "window": {
            "start": win_start.isoformat(),
            "end": win_end.isoformat(),
        },
        "punch_count": len(events),
    }
