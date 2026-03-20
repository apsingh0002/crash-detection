"""
Global simulation clock stored in SQLite.
All time-dependent logic reads/writes through here.
"""

from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from app.core.database import SimulationState

CLOCK_KEY     = "sim_timestamp"
DEFAULT_START = "2026-03-12T08:00:00.000Z"


def ensure_clock_seeded(db: Session):
    """Insert the default start time if not already set."""
    existing = db.query(SimulationState).filter_by(key=CLOCK_KEY).first()
    if not existing:
        db.add(SimulationState(key=CLOCK_KEY, value=DEFAULT_START))
        db.commit()


def get_sim_time(db: Session) -> datetime:
    """Return current simulation time as a timezone-aware datetime."""
    row = db.query(SimulationState).filter_by(key=CLOCK_KEY).first()
    val = row.value if row else DEFAULT_START
    return datetime.fromisoformat(val.replace("Z", "+00:00"))


def advance_sim_time(db: Session, seconds: int) -> datetime:
    """Advance the simulation clock by `seconds` and persist it."""
    current  = get_sim_time(db)
    new_time = current + timedelta(seconds=seconds)
    iso_str  = new_time.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    row = db.query(SimulationState).filter_by(key=CLOCK_KEY).first()
    if row:
        row.value = iso_str
    else:
        db.add(SimulationState(key=CLOCK_KEY, value=iso_str))
    db.commit()
    return new_time
