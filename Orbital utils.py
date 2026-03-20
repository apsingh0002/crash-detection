"""
orbital_utils.py — Complete physics engine
- RK4 + J2 orbital propagation
- K-D tree conjunction detection (O(N log N), not O(N²))
- RTN frame maneuver calculation
- Tsiolkovsky fuel model
- Ground station LOS check
"""
import math
import numpy as np
from datetime import datetime, timezone
from typing import Tuple, List, Dict, Optional
from scipy.spatial import KDTree

# ── Constants ────────────────────────────────────────────────────────────────
MU         = 398600.4418   # km³/s²
RE         = 6378.137      # km
J2         = 1.08263e-3
G0         = 9.80665       # m/s²
ISP        = 300.0         # s
DRY_MASS   = 500.0         # kg
FUEL_INIT  = 50.0          # kg
DV_MAX     = 0.015         # km/s (15 m/s)
COOLDOWN   = 600           # s
CRIT_DIST  = 0.100         # km (100 m conjunction threshold)
WARN_DIST  = 5.0           # km yellow warning
BOX_RADIUS = 10.0          # km station-keeping

GROUND_STATIONS = [
    {"id":"GS-001","lat": 13.0333,"lon": 77.5167,"alt_km":0.820,"min_el":5.0},
    {"id":"GS-002","lat": 78.2297,"lon": 15.4077,"alt_km":0.400,"min_el":5.0},
    {"id":"GS-003","lat": 35.4266,"lon":-116.890,"alt_km":1.000,"min_el":10.0},
    {"id":"GS-004","lat":-53.1500,"lon": -70.917,"alt_km":0.030,"min_el":5.0},
    {"id":"GS-005","lat": 28.5450,"lon":  77.193,"alt_km":0.225,"min_el":15.0},
    {"id":"GS-006","lat":-77.8463,"lon": 166.668,"alt_km":0.010,"min_el":5.0},
]


# ── EOM with J2 ──────────────────────────────────────────────────────────────
def _eom_j2(state: np.ndarray) -> np.ndarray:
    """Equations of motion with J2 perturbation. state = [x,y,z,vx,vy,vz]"""
    x, y, z, vx, vy, vz = state
    r = math.sqrt(x*x + y*y + z*z)
    r3 = r**3
    r5 = r**5
    factor = 1.5 * J2 * MU * RE**2 / r5
    z2_r2  = (z/r)**2

    ax = -MU*x/r3 + factor * x * (5*z2_r2 - 1)
    ay = -MU*y/r3 + factor * y * (5*z2_r2 - 1)
    az = -MU*z/r3 + factor * z * (5*z2_r2 - 3)

    return np.array([vx, vy, vz, ax, ay, az])


def rk4_step(state: np.ndarray, dt: float) -> np.ndarray:
    """Single RK4 integration step."""
    k1 = _eom_j2(state)
    k2 = _eom_j2(state + 0.5*dt*k1)
    k3 = _eom_j2(state + 0.5*dt*k2)
    k4 = _eom_j2(state + dt*k3)
    return state + (dt/6.0) * (k1 + 2*k2 + 2*k3 + k4)


def propagate_state(pos: Tuple, vel: Tuple, dt_seconds: float,
                    substeps: int = 10) -> Tuple[Tuple, Tuple]:
    """
    Propagate a single object forward by dt_seconds using RK4+J2.
    Uses substeps for accuracy on longer time windows.
    Returns (new_pos_km, new_vel_km_s).
    """
    state = np.array([pos[0], pos[1], pos[2], vel[0], vel[1], vel[2]])
    sub_dt = dt_seconds / substeps
    for _ in range(substeps):
        state = rk4_step(state, sub_dt)
    return (state[0], state[1], state[2]), (state[3], state[4], state[5])


def propagate_all_objects(objects: list, dt_seconds: float):
    """Propagate all ObjectState rows in-place using RK4+J2."""
    substeps = max(1, int(dt_seconds / 60))  # ~1 substep per minute
    for obj in objects:
        new_pos, new_vel = propagate_state(
            (obj.pos_x, obj.pos_y, obj.pos_z),
            (obj.vel_x, obj.vel_y, obj.vel_z),
            dt_seconds, substeps
        )
        obj.pos_x, obj.pos_y, obj.pos_z = new_pos
        obj.vel_x, obj.vel_y, obj.vel_z = new_vel


# ── K-D Tree Conjunction Detection ───────────────────────────────────────────
def find_conjunctions_kdtree(satellites: list, debris: list,
                              horizon_s: float = 3600,
                              time_steps: int = 6) -> List[Dict]:
    """
    O(N log N) conjunction detection using K-D tree spatial index.
    Propagates both sats and debris forward in time steps and checks proximity.

    Returns list of CDM dicts: {satellite_id, debris_id, tca_offset_s, miss_distance_km, risk_level}
    """
    if not satellites or not debris:
        return []

    results = []
    seen    = set()
    dt      = horizon_s / time_steps

    # Copy states so we don't mutate the originals
    sat_states  = [(s.id, np.array([s.pos_x,s.pos_y,s.pos_z,s.vel_x,s.vel_y,s.vel_z])) for s in satellites]
    deb_states  = [(d.id, np.array([d.pos_x,d.pos_y,d.pos_z,d.vel_x,d.vel_y,d.vel_z])) for d in debris]

    for step in range(time_steps):
        t_offset = (step + 1) * dt

        # Propagate all to this time offset
        sat_pos = []
        for sid, state in sat_states:
            new_state = state.copy()
            for _ in range(10):
                new_state = rk4_step(new_state, dt/10)
            sat_pos.append((sid, new_state[:3]))

        deb_pos = []
        for did, state in deb_states:
            new_state = state.copy()
            for _ in range(10):
                new_state = rk4_step(new_state, dt/10)
            deb_pos.append((did, new_state[:3]))

        # Build K-D tree from debris positions
        deb_coords = np.array([p for _, p in deb_pos])
        tree = KDTree(deb_coords)

        # Query: for each satellite, find debris within 50km (coarse filter)
        for sat_id, sp in sat_pos:
            indices = tree.query_ball_point(sp, r=50.0)
            for idx in indices:
                deb_id = deb_pos[idx][0]
                dp     = deb_pos[idx][1]
                dist   = float(np.linalg.norm(sp - dp))
                key    = (sat_id, deb_id)

                if key in seen:
                    continue

                if dist < CRIT_DIST:
                    risk = "CRITICAL"
                elif dist < 1.0:
                    risk = "CRITICAL"
                elif dist < WARN_DIST:
                    risk = "WARNING"
                else:
                    continue

                seen.add(key)
                results.append({
                    "satellite_id":    sat_id,
                    "debris_id":       deb_id,
                    "tca_offset_s":    t_offset,
                    "miss_distance_km": round(dist, 4),
                    "risk_level":      risk,
                })

    return results


# ── RTN Frame Maneuver Calculator ─────────────────────────────────────────────
def compute_evasion_dv_rtn(pos: Tuple, vel: Tuple,
                            debris_pos: Tuple, debris_vel: Tuple,
                            tca_seconds: float) -> Tuple[float, float, float]:
    """
    Calculate optimal evasion ΔV in RTN frame, then convert to ECI.
    Uses prograde/retrograde burn (T-direction) — most fuel efficient.

    Returns ΔV vector in ECI (km/s).
    """
    r = np.array(pos)
    v = np.array(vel)
    r_deb = np.array(debris_pos)
    v_deb = np.array(debris_vel)

    # Build RTN unit vectors
    R_hat = r / np.linalg.norm(r)
    N_hat = np.cross(r, v); N_hat /= np.linalg.norm(N_hat)
    T_hat = np.cross(N_hat, R_hat)

    # Relative position to debris
    rel = r_deb - r
    # Determine approach direction — burn away
    closing = np.dot(rel, T_hat)

    # Required standoff: push satellite ~0.5 km from debris trajectory
    standoff_km = 0.5
    dv_magnitude = min(standoff_km / max(tca_seconds, 1.0), DV_MAX * 0.8)

    # Burn retrograde if debris ahead, prograde if behind
    direction = -1.0 if closing > 0 else 1.0
    dv_rtn = np.array([0.0, direction * dv_magnitude, 0.0])

    # RTN → ECI rotation matrix
    rot = np.column_stack([R_hat, T_hat, N_hat])
    dv_eci = rot @ dv_rtn

    return tuple(float(v) for v in dv_eci)


def compute_recovery_dv(pos: Tuple, vel: Tuple,
                         slot_pos: Tuple) -> Tuple[float, float, float]:
    """
    Compute return-to-slot burn. Simple Hohmann-like phasing.
    Returns ΔV in ECI (km/s).
    """
    r     = np.array(pos)
    v     = np.array(vel)
    r_slot = np.array(slot_pos)
    drift  = r_slot - r

    R_hat = r / np.linalg.norm(r)
    N_hat = np.cross(r, v); N_hat /= np.linalg.norm(N_hat)
    T_hat = np.cross(N_hat, R_hat)

    # Project drift onto transverse direction for phasing
    drift_T = np.dot(drift, T_hat)
    dv_T    = np.clip(drift_T * 0.001, -DV_MAX * 0.5, DV_MAX * 0.5)

    rot    = np.column_stack([R_hat, T_hat, N_hat])
    dv_rtn = np.array([0.0, dv_T, 0.0])
    dv_eci = rot @ dv_rtn
    return tuple(float(v) for v in dv_eci)


# ── Fuel / Mass ───────────────────────────────────────────────────────────────
def fuel_consumed(dv_kmps: float, current_mass_kg: float) -> float:
    """Tsiolkovsky rocket equation → propellant mass consumed (kg)."""
    dv_mps = dv_kmps * 1000.0
    return current_mass_kg * (1.0 - math.exp(-dv_mps / (ISP * G0)))

def dv_magnitude(dv: dict) -> float:
    return math.sqrt(dv["x"]**2 + dv["y"]**2 + dv["z"]**2)


# ── Ground Station LOS ────────────────────────────────────────────────────────
def has_ground_station_los(sat_lat: float, sat_lon: float, sat_alt_km: float) -> bool:
    for gs in GROUND_STATIONS:
        el = _elevation_angle(gs["lat"], gs["lon"], gs["alt_km"], sat_lat, sat_lon, sat_alt_km)
        if el >= gs["min_el"]:
            return True
    return False

def _elevation_angle(gs_lat, gs_lon, gs_alt_km, sat_lat, sat_lon, sat_alt_km) -> float:
    gs_lat_r  = math.radians(gs_lat)
    sat_lat_r = math.radians(sat_lat)
    dlon      = math.radians(sat_lon - gs_lon)
    cos_ca    = (math.sin(gs_lat_r)*math.sin(sat_lat_r) +
                 math.cos(gs_lat_r)*math.cos(sat_lat_r)*math.cos(dlon))
    cos_ca    = max(-1.0, min(1.0, cos_ca))
    ca        = math.acos(cos_ca)
    r_gs      = RE + gs_alt_km
    r_sat     = RE + sat_alt_km
    denom     = math.sqrt(r_sat**2 + r_gs**2 - 2*r_sat*r_gs*math.cos(ca) + 1e-9)
    sin_el    = (r_sat*math.cos(ca) - r_gs) / denom
    return math.degrees(math.asin(max(-1.0, min(1.0, sin_el))))


# ── ECI ↔ Geodetic ────────────────────────────────────────────────────────────
def eci_to_geodetic(x, y, z, t: datetime) -> Tuple[float, float, float]:
    gmst      = _gmst(t)
    cos_g, sin_g = math.cos(gmst), math.sin(gmst)
    x_ecef    =  x*cos_g + y*sin_g
    y_ecef    = -x*sin_g + y*cos_g
    lon_rad   = math.atan2(y_ecef, x_ecef)
    p         = math.sqrt(x_ecef**2 + y_ecef**2)
    lat_rad   = math.atan2(z, p)
    alt_km    = math.sqrt(x**2 + y**2 + z**2) - RE
    return math.degrees(lat_rad), math.degrees(lon_rad), alt_km

def _gmst(t: datetime) -> float:
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    J2000   = datetime(2000, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    d       = (t - J2000).total_seconds() / 86400.0
    gmst_deg = (280.46061837 + 360.98564736629*d) % 360
    return math.radians(gmst_deg)

def is_in_station_box(pos: Tuple, slot: Tuple) -> bool:
    return math.sqrt(sum((a-b)**2 for a,b in zip(pos,slot))) <= BOX_RADIUS
