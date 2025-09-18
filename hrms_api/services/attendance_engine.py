from __future__ import annotations
from datetime import datetime, date, time as _time, timedelta
from typing import Optional, List, Tuple
import logging

from hrms_api.extensions import db
from hrms_api.models.attendance_punch import AttendancePunch

log = logging.getLogger(__name__)

# ---- dynamic column detection ----
HAS_PUNCH_DT = hasattr(AttendancePunch, "punch_dt")
HAS_DIRECTION = hasattr(AttendancePunch, "direction")
TS_COL = getattr(AttendancePunch, "punch_dt", None) or getattr(AttendancePunch, "ts")
DIR_COL = getattr(AttendancePunch, "direction", None) or getattr(AttendancePunch, "kind", None)

def _dt(d: date, t: _time) -> datetime:
    return datetime.combine(d, t)

def _to_model_dir(direction: str) -> str | None:
    """Map 'IN'/'OUT' to model's column shape."""
    if not DIR_COL:
        return None
    d = (direction or "").strip().upper()
    if TS_COL.key == "ts" and DIR_COL.key == "kind":
        return "in" if d.startswith("I") else "out"
    # default (direction)
    return "IN" if d.startswith("I") else "OUT"

def _from_model_dir(v) -> str:
    """Normalize model value to 'IN'/'OUT' for pairing logic."""
    if DIR_COL and DIR_COL.key == "kind":
        return "IN" if str(v).lower().startswith("i") else "OUT"
    return "IN" if str(v).upper().startswith("I") else "OUT"

# ---------- public API ----------
def upsert_manual_punch(employee_id: int, work_date: date, direction: str, at_time: _time, source: str = "missed") -> int:
    if TS_COL is None:
        raise RuntimeError("AttendancePunch must have a datetime column (punch_dt or ts)")
    punch_dt = _dt(work_date, at_time)
    q = AttendancePunch.query.filter(AttendancePunch.employee_id == employee_id, TS_COL == punch_dt)
    if DIR_COL is not None:
        q = q.filter(DIR_COL == _to_model_dir(direction))
    row = q.first()
    if row:
        if hasattr(row, "source") and not getattr(row, "source", None):
            row.source = source
            db.session.commit()
        return row.id

    fields = {"employee_id": employee_id, TS_COL.key: punch_dt}
    if DIR_COL is not None:
        fields[DIR_COL.key] = _to_model_dir(direction)
    if hasattr(AttendancePunch, "source"):
        fields["source"] = source
    if hasattr(AttendancePunch, "is_manual"):
        fields["is_manual"] = True
    row = AttendancePunch(**fields)
    db.session.add(row); db.session.commit()
    return row.id

def recompute_daily(employee_id: int, work_date: date) -> dict:
    win_start = datetime.combine(work_date, datetime.min.time()) - timedelta(hours=6)
    win_end   = datetime.combine(work_date, datetime.max.time()) + timedelta(hours=6)

    # optional shift narrowing (best-effort)
    try:
        from hrms_api.models.attendance_assignment import EmployeeShiftAssignment as _Assign
        from hrms_api.models.attendance import Shift as _Shift
        rec = (db.session.query(_Assign, _Shift)
               .join(_Shift, _Shift.id == _Assign.shift_id)
               .filter(_Assign.employee_id == employee_id)
               .filter(_Assign.start_date <= work_date)
               .filter((_Assign.end_date == None) | (_Assign.end_date >= work_date))
               .order_by(_Assign.start_date.desc()).first())
        if rec:
            _, s = rec
            st = s.start_time; et = s.end_time
            if st and et:
                win_start = _dt(work_date, st) - timedelta(hours=3)
                et_dt = _dt(work_date, et)
                if s.is_night or et <= st:
                    et_dt += timedelta(days=1)
                win_end = et_dt + timedelta(hours=3)
    except Exception:
        pass

    qp = (AttendancePunch.query
          .filter(AttendancePunch.employee_id == employee_id)
          .filter(TS_COL >= win_start).filter(TS_COL <= win_end)
          .order_by(TS_COL.asc()))
    punches: List[AttendancePunch] = qp.all()

    events: List[Tuple[str, datetime]] = []
    for p in punches:
        dtv = getattr(p, TS_COL.key)
        dirv = getattr(p, DIR_COL.key) if DIR_COL is not None else "IN"
        events.append((_from_model_dir(dirv), dtv))

    work_secs = 0
    open_in: Optional[datetime] = None
    for dirv, dtv in events:
        if dirv == "IN":
            open_in = dtv
        else:
            if open_in and dtv > open_in:
                work_secs += (dtv - open_in).total_seconds()
            open_in = None

    work_mins = int(round(work_secs / 60.0))
    status = "Present" if work_mins > 0 else "Absent"

    # optional daily summary upsert (ignore if absent)
    updated = False
    try:
        from hrms_api.models.attendance import DailyStatus as _Daily
        row = (_Daily.query.filter(_Daily.employee_id == employee_id, _Daily.work_date == work_date).first())
        if not row:
            row = _Daily(employee_id=employee_id, work_date=work_date); db.session.add(row)
        if hasattr(row, "work_mins"): row.work_mins = work_mins
        if hasattr(row, "status"): row.status = status
        db.session.commit(); updated = True
    except Exception:
        log.info("[attendance.recompute_daily] daily upsert skipped")

    return {
        "employee_id": employee_id,
        "date": work_date.isoformat(),
        "work_mins": work_mins,
        "status": status,
        "daily_upserted": updated,
        "window": {"start": win_start.isoformat(), "end": win_end.isoformat()},
        "punch_count": len(events),
    }
