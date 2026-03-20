# Autonomous Constellation Manager (ACM)
### National Space Hackathon 2026 — IIT Delhi

---

## 🚀 Quick Start

### Option A — Docker (recommended, handles everything)
```bash
docker-compose up --build
```
- Backend API → http://localhost:8000
- Frontend UI → http://localhost:3000
- API Docs    → http://localhost:8000/docs

### Option B — Manual
```bash
# Backend
cd backend
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Frontend (open in browser directly)
open frontend/index.html
```

---

## 📁 Project Structure

```
acm-full/
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app/
│       ├── main.py                  # FastAPI entry point
│       ├── api/
│       │   ├── telemetry.py         # POST /api/telemetry
│       │   ├── maneuver.py          # POST /api/maneuver/schedule
│       │   ├── simulation.py        # POST /api/simulate/step  ← core logic
│       │   └── visualization.py     # GET  /api/visualization/snapshot
│       ├── core/
│       │   ├── database.py          # SQLite tables + seeded demo data
│       │   ├── sim_clock.py         # Global simulation time
│       │   └── orbital_utils.py     # RK4+J2, K-D tree, RTN burns, fuel
│       └── models/
│           └── schemas.py           # Pydantic request/response models
├── frontend/
│   └── index.html                   # Complete dashboard (no build step)
└── docker-compose.yml
```

---

## 📡 API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/telemetry` | Ingest ECI state vectors |
| POST | `/api/maneuver/schedule` | Schedule burn sequence |
| POST | `/api/simulate/step` | Advance simulation |
| GET  | `/api/visualization/snapshot` | Frontend data |

---

## 🧠 What Makes This Stand Out

### 1. K-D Tree Conjunction Detection (O(N log N))
In `orbital_utils.py → find_conjunctions_kdtree()`:
- Builds a K-D tree from all debris positions
- Queries only satellites' nearby debris (50km coarse filter)
- Avoids O(N²) — critical for 50 sats × 10,000 debris at scale

### 2. RTN Frame Evasion Burns
In `orbital_utils.py → compute_evasion_dv_rtn()`:
- Burns in the Transverse direction (prograde/retrograde)
- Most fuel-efficient evasion strategy
- Automatic RTN → ECI conversion

### 3. Autonomous COLA
In `simulation.py → _autonomous_cola()`:
- Auto-detects CRITICAL conjunctions
- Priority queue: smallest miss distance first
- Preserves fuel-rich satellites
- Schedules both evasion AND recovery burns automatically

### 4. Tsiolkovsky Fuel Tracking
- Every burn deducts exact fuel mass (dynamic — accounts for decreasing mass)
- EOL at 5% fuel → auto-graveyard status
- Pre-flight validation rejects impossible burn sequences

### 5. Full Frontend
- 3D WebGL globe (Three.js) with satellite markers
- Ground track 2D map with terminator line
- Bullseye conjunction plot (polar)
- Fuel heatmap grid
- Maneuver timeline
- Live 5-second polling

---

## 🤝 Team Responsibilities

| Member | File(s) |
|--------|---------|
| **You (API + DB)** | `api/`, `core/database.py`, `core/sim_clock.py` |
| **Algorithm** | `core/orbital_utils.py` — can upgrade RK4 substeps, improve COLA logic |
| **Frontend** | `frontend/index.html` — add more panels, tune visuals |
| **Docker** | `Dockerfile`, `docker-compose.yml` |
