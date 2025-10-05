import asyncio
import json
import sys
from pathlib import Path
from typing import Dict, Optional

import pytest
from starlette.requests import Request

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import main  # noqa: E402
from app.security import IdempotencyStore  # noqa: E402


def _build_scope(headers: Dict[str, str]) -> dict:
    header_items = [(key.lower().encode("utf-8"), value.encode("utf-8")) for key, value in headers.items()]
    return {
        "type": "http",
        "method": "POST",
        "path": "/estimate",
        "headers": header_items,
        "query_string": b"",
    }


def call_estimate(payload: dict, headers: Optional[Dict[str, str]] = None):
    headers = headers or {}
    body = json.dumps(payload).encode("utf-8")
    scope = _build_scope(headers)

    state = {"sent": False}

    async def receive():
        if state["sent"]:
            return {"type": "http.request", "body": b"", "more_body": False}
        state["sent"] = True
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request(scope, receive)
    return asyncio.run(main.estimate(request))


@pytest.fixture(autouse=True)
def reset_idempotency_store(monkeypatch):
    monkeypatch.setattr(main, "idempotency_store", IdempotencyStore(redis_url=None))
    yield
