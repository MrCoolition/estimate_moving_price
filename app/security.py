from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from fastapi import HTTPException

try:  # pragma: no cover - optional dependency
    import redis  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    redis = None


class HMACVerifier:
    def __init__(self, secret: str):
        self._secret = secret.encode("utf-8")

    def verify(self, header: Optional[str], payload: bytes) -> None:
        if not self._secret:
            return
        if not header or not header.startswith("sha256="):
            raise HTTPException(status_code=401, detail="Missing or invalid signature header")
        provided = header.split("=", 1)[1]
        digest = hmac.new(self._secret, payload, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(provided, digest):
            raise HTTPException(status_code=401, detail="Signature mismatch")


@dataclass
class IdempotencyRecord:
    body_hash: str
    response_json: str
    created_at: float


class IdempotencyStore:
    def __init__(self, redis_url: Optional[str], ttl_seconds: int = 86400):
        self._ttl = ttl_seconds
        self._redis = None
        if redis_url and redis is not None:
            self._redis = redis.Redis.from_url(redis_url)
        self._local: dict[str, IdempotencyRecord] = {}
        self._lock = threading.Lock()

    def _make_key(self, key: str) -> str:
        return f"quote:idemp:{key}"

    def _now(self) -> float:
        return time.time()

    def get_or_set(self, key: Optional[str], body: bytes, compute: Callable[[], Any]) -> Any:
        if not key:
            return compute()
        body_hash = hashlib.sha256(body).hexdigest()
        if self._redis:
            namespaced = self._make_key(key)
            existing = self._redis.get(namespaced)
            if existing:
                record = json.loads(existing.decode("utf-8"))
                if record["body_hash"] != body_hash:
                    raise HTTPException(status_code=409, detail="Idempotency conflict")
                return json.loads(record["response_json"])
            response = compute()
            payload = json.dumps({"body_hash": body_hash, "response_json": json.dumps(response)})
            self._redis.setex(namespaced, self._ttl, payload)
            return response
        with self._lock:
            record = self._local.get(key)
            now = self._now()
            if record and now - record.created_at < self._ttl:
                if record.body_hash != body_hash:
                    raise HTTPException(status_code=409, detail="Idempotency conflict")
                return json.loads(record.response_json)
            response = compute()
            self._local[key] = IdempotencyRecord(body_hash=body_hash, response_json=json.dumps(response), created_at=now)
            return response
