from pydantic import BaseModel, Field
from typing import List, Optional

class Vec3(BaseModel):
    x: float
    y: float
    z: float

# ── Telemetry ──────────────────────────────
class TelemetryObject(BaseModel):
    id: str
    type: str
    r: Vec3
    v: Vec3

class TelemetryRequest(BaseModel):
    timestamp: str
    objects: List[TelemetryObject]

class TelemetryResponse(BaseModel):
    status: str = "ACK"
    processed_count: int
    active_cdm_warnings: int

# ── Maneuver ───────────────────────────────
class BurnCommand(BaseModel):
    burn_id: str
    burnTime: str
    deltaV_vector: Vec3

class ManeuverRequest(BaseModel):
    satelliteId: str
    maneuver_sequence: List[BurnCommand]

class ManeuverValidation(BaseModel):
    ground_station_los: bool
    sufficient_fuel: bool
    projected_mass_remaining_kg: float

class ManeuverResponse(BaseModel):
    status: str
    validation: ManeuverValidation

# ── Simulation ─────────────────────────────
class StepRequest(BaseModel):
    step_seconds: int = Field(..., gt=0)

class StepResponse(BaseModel):
    status: str = "STEP_COMPLETE"
    new_timestamp: str
    collisions_detected: int
    maneuvers_executed: int

# ── Visualization ──────────────────────────
class SatSnapshot(BaseModel):
    id: str
    lat: float
    lon: float
    fuel_kg: float
    status: str

class SnapshotResponse(BaseModel):
    timestamp: str
    satellites: List[SatSnapshot]
    debris_cloud: List[List]
    active_cdms: Optional[List[dict]] = []
