from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Response

from .estimate_routes import router as estimate_router
from .metrics import CONTENT_TYPE_LATEST, generate_latest

APP_VERSION = datetime.utcnow().strftime("%Y-%m-%d")
BASE_DIR = Path(__file__).resolve().parent.parent
CATALOG_PATH = BASE_DIR / "data" / "estimation_weights_volumes_categories.json"
RULES_PATH = BASE_DIR / "data" / "moving_rules.json"

app = FastAPI(title="Estimate Moving Price", version=APP_VERSION)
app.include_router(estimate_router)


def _hash_file(path: Path) -> str:
    data = path.read_bytes()
    return str(abs(hash(data)))


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {
        "status": "ok",
        "catalog_hash": _hash_file(CATALOG_PATH),
        "rules_hash": _hash_file(RULES_PATH),
        "version": APP_VERSION,
    }


@app.get("/metrics", include_in_schema=False)
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
