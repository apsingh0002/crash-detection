"""
Autonomous Constellation Manager (ACM)
National Space Hackathon 2026 - IIT Delhi
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from app.core.database import init_db
from app.api import telemetry, maneuver, simulation, visualization

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    print("[ACM] Database initialized. Ready on port 8000.")
    yield

app = FastAPI(title="Autonomous Constellation Manager", version="1.0.0", lifespan=lifespan)

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

app.include_router(telemetry.router,     prefix="/api", tags=["Telemetry"])
app.include_router(maneuver.router,      prefix="/api", tags=["Maneuver"])
app.include_router(simulation.router,    prefix="/api", tags=["Simulation"])
app.include_router(visualization.router, prefix="/api", tags=["Visualization"])

@app.get("/")
def root():
    return {"status": "ACM online", "version": "1.0.0"}
