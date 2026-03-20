from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from datetime import datetime
from app.core.database import get_db, ObjectState, CDMWarning
from app.models.schemas import TelemetryRequest, TelemetryResponse
import logging

logger = logging.getLogger("ACM.telemetry")
router = APIRouter()

@router.post("/telemetry", response_model=TelemetryResponse)
def ingest_telemetry(payload: TelemetryRequest, db: Session = Depends(get_db)):
    ts = datetime.fromisoformat(payload.timestamp.replace("Z", "+00:00"))
    processed = 0

    for obj in payload.objects:
        obj_type = obj.type.upper()
        existing = db.query(ObjectState).filter_by(id=obj.id).first()
        if existing:
            existing.timestamp = ts
            existing.pos_x, existing.pos_y, existing.pos_z = obj.r.x, obj.r.y, obj.r.z
            existing.vel_x, existing.vel_y, existing.vel_z = obj.v.x, obj.v.y, obj.v.z
        else:
            db.add(ObjectState(
                id=obj.id, obj_type=obj_type, timestamp=ts,
                pos_x=obj.r.x, pos_y=obj.r.y, pos_z=obj.r.z,
                vel_x=obj.v.x, vel_y=obj.v.y, vel_z=obj.v.z,
                fuel_kg=50.0  if obj_type == "SATELLITE" else None,
                mass_kg=550.0 if obj_type == "SATELLITE" else None,
                status="NOMINAL" if obj_type == "SATELLITE" else None,
            ))
        processed += 1

    db.commit()
    active_cdms = db.query(CDMWarning).filter_by(resolved=0).count()
    logger.info(f"[TELEMETRY] Processed {processed} objects | Active CDMs: {active_cdms}")
    return TelemetryResponse(status="ACK", processed_count=processed, active_cdm_warnings=active_cdms)
