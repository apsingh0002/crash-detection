from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from app.core.database import get_db, ObjectState, ManeuverRecord
from app.core.orbital_utils import fuel_consumed, dv_magnitude, has_ground_station_los, eci_to_geodetic, DV_MAX, COOLDOWN
from app.core.sim_clock import get_sim_time
from app.models.schemas import ManeuverRequest, ManeuverResponse, ManeuverValidation
import logging

logger = logging.getLogger("ACM.maneuver")
router = APIRouter()

@router.post("/maneuver/schedule", response_model=ManeuverResponse)
def schedule_maneuver(payload: ManeuverRequest, db: Session = Depends(get_db)):
    sat = db.query(ObjectState).filter_by(id=payload.satelliteId).first()
    if not sat:
        raise HTTPException(404, f"Satellite {payload.satelliteId} not found")

    sim_now       = get_sim_time(db)
    min_burn_time = sim_now + timedelta(seconds=10)
    current_mass  = sat.mass_kg or 550.0
    current_fuel  = sat.fuel_kg or 50.0
    last_burn_dt  = sat.last_burn_time

    lat, lon, alt = eci_to_geodetic(sat.pos_x, sat.pos_y, sat.pos_z, sim_now)
    los_ok        = has_ground_station_los(lat, lon, alt)

    total_dm      = 0.0
    burns         = []

    for burn in payload.maneuver_sequence:
        burn_dt = datetime.fromisoformat(burn.burnTime.replace("Z", "+00:00"))

        if burn_dt < min_burn_time:
            raise HTTPException(400, f"Burn {burn.burn_id}: too soon (< 10s latency)")

        dv_dict = {"x": burn.deltaV_vector.x, "y": burn.deltaV_vector.y, "z": burn.deltaV_vector.z}
        dv_mag  = dv_magnitude(dv_dict)

        if dv_mag > DV_MAX:
            raise HTTPException(400, f"Burn {burn.burn_id}: |ΔV|={dv_mag*1000:.1f} m/s > 15 m/s limit")

        if last_burn_dt:
            gap = (burn_dt - last_burn_dt).total_seconds()
            if gap < COOLDOWN:
                raise HTTPException(400, f"Burn {burn.burn_id}: {gap:.0f}s < 600s cooldown")

        dm           = fuel_consumed(dv_mag, current_mass)
        total_dm    += dm
        current_mass -= dm
        current_fuel -= dm
        last_burn_dt  = burn_dt
        min_burn_time = burn_dt + timedelta(seconds=COOLDOWN)

        burns.append(ManeuverRecord(
            burn_id=burn.burn_id,
            satellite_id=payload.satelliteId,
            burn_time=burn_dt,
            dv_x=burn.deltaV_vector.x,
            dv_y=burn.deltaV_vector.y,
            dv_z=burn.deltaV_vector.z,
            status="SCHEDULED",
        ))

    fuel_ok = current_fuel >= 0.0

    if fuel_ok and los_ok:
        for b in burns:
            db.merge(b)
        db.commit()
        status = "SCHEDULED"
        logger.info(f"[MANEUVER] {payload.satelliteId} | {len(burns)} burns scheduled | ΔFuel: {total_dm:.3f} kg | LOS: {los_ok}")
    else:
        status = "REJECTED"
        logger.warning(f"[MANEUVER] {payload.satelliteId} REJECTED | fuel_ok={fuel_ok} los_ok={los_ok}")

    return ManeuverResponse(
        status=status,
        validation=ManeuverValidation(
            ground_station_los=los_ok,
            sufficient_fuel=fuel_ok,
            projected_mass_remaining_kg=round((sat.mass_kg or 550.0) - total_dm, 3),
        )
    )
