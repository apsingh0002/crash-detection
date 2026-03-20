"""
POST /api/simulate/step
Full simulation tick:
1. Execute due maneuvers (apply ΔV, deduct fuel)
2. RK4+J2 propagate all objects
3. K-D tree conjunction detection
4. Autonomous COLA — auto-schedule evasion+recovery burns
5. EOL graveyard management
6. Collision detection
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from datetime import datetime, timezone, timedelta
from app.core.database import get_db, ObjectState, ManeuverRecord, CDMWarning
from app.core.sim_clock import get_sim_time, advance_sim_time
from app.core.orbital_utils import (
    propagate_all_objects, find_conjunctions_kdtree,
    compute_evasion_dv_rtn, compute_recovery_dv,
    fuel_consumed, dv_magnitude, CRIT_DIST, DRY_MASS, COOLDOWN
)
from app.models.schemas import StepRequest, StepResponse
import math, uuid, logging

logger = logging.getLogger("ACM.simulation")
router = APIRouter()


@router.post("/simulate/step", response_model=StepResponse)
def simulate_step(payload: StepRequest, db: Session = Depends(get_db)):
    sim_now  = get_sim_time(db)
    step_end = sim_now + timedelta(seconds=payload.step_seconds)

    maneuvers_executed  = 0
    collisions_detected = 0
    all_objects         = db.query(ObjectState).all()
    states              = {obj.id: obj for obj in all_objects}

    # ── 1. Execute due burns ──────────────────────────────────────────────────
    due_burns = (
        db.query(ManeuverRecord)
        .filter(ManeuverRecord.status == "SCHEDULED",
                ManeuverRecord.burn_time >= sim_now,
                ManeuverRecord.burn_time <= step_end)
        .order_by(ManeuverRecord.burn_time).all()
    )

    for burn in due_burns:
        sat = states.get(burn.satellite_id)
        if not sat:
            burn.status = "FAILED"; continue

        dv_dict = {"x": burn.dv_x, "y": burn.dv_y, "z": burn.dv_z}
        dv_mag  = dv_magnitude(dv_dict)
        dm      = fuel_consumed(dv_mag, sat.mass_kg or 550.0)

        if (sat.fuel_kg or 0) < dm:
            burn.status = "FAILED"
            logger.warning(f"[EXEC] {burn.burn_id} FAILED — insufficient fuel on {sat.id}")
            continue

        sat.vel_x += burn.dv_x
        sat.vel_y += burn.dv_y
        sat.vel_z += burn.dv_z
        sat.fuel_kg = (sat.fuel_kg or 0) - dm
        sat.mass_kg = (sat.mass_kg or DRY_MASS) - dm
        sat.last_burn_time = burn.burn_time

        if sat.fuel_kg < (50.0 * 0.05):
            sat.status = "GRAVEYARD"
            logger.info(f"[EOL] {sat.id} → GRAVEYARD (fuel critical: {sat.fuel_kg:.2f} kg)")

        burn.status           = "EXECUTED"
        burn.fuel_consumed_kg = round(dm, 4)
        maneuvers_executed   += 1
        logger.info(f"[EXEC] {burn.burn_id} on {sat.id} | ΔV={dv_mag*1000:.2f} m/s | Fuel left: {sat.fuel_kg:.2f} kg")

    # ── 2. RK4+J2 Propagate all objects ──────────────────────────────────────
    propagate_all_objects(list(states.values()), payload.step_seconds)

    # ── 3. K-D tree conjunction detection ────────────────────────────────────
    satellites = [o for o in states.values() if o.obj_type == "SATELLITE" and o.status != "GRAVEYARD"]
    debris     = [o for o in states.values() if o.obj_type == "DEBRIS"]

    cdm_results = find_conjunctions_kdtree(satellites, debris, horizon_s=86400, time_steps=12)

    # Resolve existing CDMs for satellites that are now safe
    sat_ids_with_new_cdm = {c["satellite_id"] for c in cdm_results}
    db.query(CDMWarning).filter(
        CDMWarning.resolved == 0,
        ~CDMWarning.satellite_id.in_(sat_ids_with_new_cdm)
    ).update({"resolved": 1}, synchronize_session=False)

    for cdm in cdm_results:
        existing = (db.query(CDMWarning)
                    .filter_by(satellite_id=cdm["satellite_id"],
                               debris_id=cdm["debris_id"], resolved=0)
                    .first())
        tca_dt = step_end + timedelta(seconds=cdm["tca_offset_s"])
        if not existing:
            db.add(CDMWarning(
                satellite_id=cdm["satellite_id"],
                debris_id=cdm["debris_id"],
                tca=tca_dt,
                miss_distance_km=cdm["miss_distance_km"],
                risk_level=cdm["risk_level"],
            ))
            logger.warning(f"[CDM] {cdm['risk_level']} | {cdm['satellite_id']} ↔ {cdm['debris_id']} | dist={cdm['miss_distance_km']:.3f} km | TCA in {cdm['tca_offset_s']:.0f}s")

    # ── 4. Autonomous COLA ────────────────────────────────────────────────────
    _autonomous_cola(cdm_results, states, step_end, db)

    # ── 5. Collision detection (post-propagation, current positions) ──────────
    for sat in satellites:
        r_sat = (sat.pos_x, sat.pos_y, sat.pos_z)
        for deb in debris:
            r_deb = (deb.pos_x, deb.pos_y, deb.pos_z)
            dist  = math.sqrt(sum((a-b)**2 for a,b in zip(r_sat, r_deb)))
            if dist < CRIT_DIST:
                collisions_detected += 1
                logger.error(f"[COLLISION] {sat.id} ↔ {deb.id} | dist={dist*1000:.1f}m")

    # ── 6. Persist and advance clock ─────────────────────────────────────────
    for obj in states.values():
        db.merge(obj)

    new_time = advance_sim_time(db, payload.step_seconds)
    db.commit()

    logger.info(f"[TICK] +{payload.step_seconds}s | maneuvers={maneuvers_executed} | collisions={collisions_detected} | CDMs={len(cdm_results)}")
    return StepResponse(
        status="STEP_COMPLETE",
        new_timestamp=new_time.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        collisions_detected=collisions_detected,
        maneuvers_executed=maneuvers_executed,
    )


def _autonomous_cola(cdm_results: list, states: dict, sim_now: datetime, db: Session):
    """
    Auto-schedule evasion + recovery burns for CRITICAL conjunctions
    that don't already have a scheduled burn.
    Priority: most critical first, then by fuel remaining (preserve fuel-rich sats).
    """
    critical = [c for c in cdm_results if c["risk_level"] == "CRITICAL"]
    # Sort: smallest miss distance first (most urgent), then most fuel remaining (preserve scarce sats last)
    critical.sort(key=lambda c: (
        c["miss_distance_km"],
        -(states[c["satellite_id"]].fuel_kg or 0) if c["satellite_id"] in states else 0
    ))

    for cdm in critical:
        sat = states.get(cdm["satellite_id"])
        deb = states.get(cdm["debris_id"])
        if not sat or not deb:
            continue
        if sat.status == "GRAVEYARD":
            continue

        # Check if a burn is already scheduled for this satellite
        existing_burn = (db.query(ManeuverRecord)
                         .filter_by(satellite_id=sat.id, status="SCHEDULED")
                         .first())
        if existing_burn:
            continue

        # Check cooldown
        if sat.last_burn_time:
            gap = (sim_now - sat.last_burn_time.replace(tzinfo=sim_now.tzinfo
                   if sim_now.tzinfo else None)).total_seconds()
            if gap < COOLDOWN:
                logger.info(f"[COLA] {sat.id} in cooldown — skipping auto-schedule")
                continue

        # Calculate evasion ΔV in RTN → ECI
        try:
            dv_evade = compute_evasion_dv_rtn(
                (sat.pos_x, sat.pos_y, sat.pos_z),
                (sat.vel_x, sat.vel_y, sat.vel_z),
                (deb.pos_x, deb.pos_y, deb.pos_z),
                (deb.vel_x, deb.vel_y, deb.vel_z),
                cdm["tca_offset_s"],
            )
        except Exception as e:
            logger.error(f"[COLA] Evasion calc failed for {sat.id}: {e}")
            continue

        evasion_time  = sim_now + timedelta(seconds=15)
        recovery_time = evasion_time + timedelta(seconds=COOLDOWN + 60)

        # Recovery burn — return to nominal slot
        slot = (sat.slot_x or sat.pos_x, sat.slot_y or sat.pos_y, sat.slot_z or sat.pos_z)
        dv_recover = compute_recovery_dv(
            (sat.pos_x, sat.pos_y, sat.pos_z),
            (sat.vel_x, sat.vel_y, sat.vel_z),
            slot,
        )

        eid = f"AUTO_EVADE_{sat.id}_{uuid.uuid4().hex[:6].upper()}"
        rid = f"AUTO_RECOVER_{sat.id}_{uuid.uuid4().hex[:6].upper()}"

        db.add(ManeuverRecord(
            burn_id=eid, satellite_id=sat.id, burn_time=evasion_time,
            dv_x=dv_evade[0], dv_y=dv_evade[1], dv_z=dv_evade[2], status="SCHEDULED",
        ))
        db.add(ManeuverRecord(
            burn_id=rid, satellite_id=sat.id, burn_time=recovery_time,
            dv_x=dv_recover[0], dv_y=dv_recover[1], dv_z=dv_recover[2], status="SCHEDULED",
        ))

        sat.status = "EVADING"
        logger.info(f"[COLA] AUTO-SCHEDULED evasion+recovery for {sat.id} | miss={cdm['miss_distance_km']:.3f} km")
