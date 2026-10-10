from __future__ import annotations

import random
import time
from typing import Any

import requests


class ExternalError(RuntimeError):
    def __init__(self, service: str, message: str, *, transient: bool = False):
        super().__init__(f"{service}: {message}")
        self.transient = transient


def request_json(method: str, url: str, *, service: str, attempts: int = 3,
                 timeout: int = 30, session: requests.Session | None = None,
                 **kwargs: Any) -> dict[str, Any]:
    sender = session or requests
    for attempt in range(max(1, attempts)):
        try:
            response = sender.request(method, url, timeout=timeout, **kwargs)
        except (requests.Timeout, requests.ConnectionError) as exc:
            if attempt + 1 == attempts:
                raise ExternalError(service, "network timeout or connection failure", transient=True) from exc
            time.sleep(min(20, 2 ** attempt) + random.random())
            continue
        if response.status_code in {429, 500, 502, 503, 504}:
            if attempt + 1 < attempts:
                try:
                    delay = min(30, float(response.headers.get("Retry-After", 0)))
                except ValueError:
                    delay = 0
                time.sleep(max(delay, min(20, 2 ** attempt) + random.random()))
                continue
            raise ExternalError(service, f"HTTP {response.status_code}; try again later", transient=True)
        if not response.ok:
            # API error bodies may contain credentials. Only expose a bounded error code.
            code = ""
            try:
                detail = response.json().get("error", {})
                if isinstance(detail, dict):
                    code = str(detail.get("code") or detail.get("type") or "")[:40]
            except (ValueError, AttributeError):
                pass
            suffix = f" ({code})" if code else ""
            hint = {"190": ": access token expired or invalid", "200": ": check Page or Instagram publishing permissions",
                    "10": ": check app access and permissions", "4": ": rate limit reached",
                    "17": ": rate limit reached", "32": ": rate limit reached", "613": ": rate limit reached"}.get(code, "")
            if service == "LLM" and response.status_code == 400:
                hint = ": model/provider rejected the request; check configured models and JSON output support"
            raise ExternalError(service, f"HTTP {response.status_code}{suffix}{hint}")
        try:
            data = response.json()
        except ValueError as exc:
            raise ExternalError(service, "invalid JSON response") from exc
        if not isinstance(data, dict):
            raise ExternalError(service, "expected a JSON object")
        return data
    raise AssertionError("unreachable")

