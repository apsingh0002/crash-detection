from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.core.database import get_db, ObjectState, CDMWarning
from app.core.orbital_utils import eci_to_geodetic
from app.core.sim_clock import get_sim_time
from app.models.schemas import SnapshotResponse, SatSnapshot

router = APIRouter()

@router.get("/visualization/snapshot", response_model=SnapshotResponse)
def get_snapshot(db: Session = Depends(get_db)):
    sim_time    = get_sim_time(db)
    all_objs    = db.query(ObjectState).all()
    active_cdms = (db.query(CDMWarning).filter_by(resolved=0)
                   .order_by(CDMWarning.miss_distance_km).limit(50).all())

    satellites   = []
    debris_cloud = []

    for obj in all_objs:
        lat, lon, alt = eci_to_geodetic(obj.pos_x, obj.pos_y, obj.pos_z, sim_time)
        if obj.obj_type == "SATELLITE":
            satellites.append(SatSnapshot(
                id=obj.id,
                lat=round(lat, 4),
                lon=round(lon, 4),
                fuel_kg=round(obj.fuel_kg or 0.0, 2),
                status=obj.status or "NOMINAL",
            ))
        else:
            debris_cloud.append([obj.id, round(lat, 3), round(lon, 3), round(alt, 1)])

    cdm_list = [{
        "satellite_id":    c.satellite_id,
        "debris_id":       c.debris_id,
        "miss_distance_km": c.miss_distance_km,
        "risk_level":      c.risk_level,
    } for c in active_cdms]

    return SnapshotResponse(
        timestamp=sim_time.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        satellites=satellites,
        debris_cloud=debris_cloud,
        active_cdms=cdm_list,
    )
