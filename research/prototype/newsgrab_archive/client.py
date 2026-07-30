"""Bounded client for NewsGrab's asynchronous internal HTTP API."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any, Mapping, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .models import ArchiveRequest, JobResult


class NewsGrabClient:
    """Submit one Google News collection job and wait for its terminal state."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 15.0,
        poll_interval_seconds: float = 1.0,
        max_polls: int = 30,
    ) -> None:
        if not base_url.strip():
            raise ValueError("base_url must not be blank")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if poll_interval_seconds < 0:
            raise ValueError("poll_interval_seconds must not be negative")
        if max_polls < 1:
            raise ValueError("max_polls must be positive")
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.max_polls = max_polls

    def collect(self, request: ArchiveRequest) -> JobResult:
        submitted_at = _utc_now()
        post_payload, post_error = self._request_json("POST", "/jobs", request.to_newsgrab_payload())
        if post_error is not None:
            return _failed_result(None, submitted_at, post_error)
        job_id = post_payload.get("job_id") if post_payload is not None else None
        if not isinstance(job_id, str) or not job_id.strip():
            return _failed_result(None, submitted_at, "protocol_error: missing_job_id")

        for _ in range(self.max_polls):
            poll_payload, poll_error = self._request_json("GET", f"/jobs/{job_id}")
            if poll_error is not None:
                return _failed_result(job_id, submitted_at, poll_error)
            assert poll_payload is not None
            status = poll_payload.get("status")
            if status == "done":
                result = poll_payload.get("result")
                if not isinstance(result, list):
                    return _failed_result(job_id, submitted_at, "protocol_error: done_result_not_list")
                return JobResult(
                    job_id=job_id,
                    submitted_at=submitted_at,
                    completed_at=_utc_now(),
                    articles=tuple(result),
                    error=None,
                )
            if status == "failed":
                upstream_error = poll_payload.get("error")
                return _failed_result(
                    job_id,
                    submitted_at,
                    f"newsgrab_failed: {upstream_error or 'unknown'}",
                )
            if status not in {"queued", "running"}:
                return _failed_result(job_id, submitted_at, f"protocol_error: unknown_status:{status!r}")
            if self.poll_interval_seconds:
                time.sleep(self.poll_interval_seconds)
        return _failed_result(job_id, submitted_at, "poll_limit_exceeded")

    def _request_json(
        self,
        method: str,
        path: str,
        payload: Optional[Mapping[str, Any]] = None,
    ) -> tuple[Optional[Mapping[str, Any]], Optional[str]]:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = Request(
            f"{self.base_url}{path}",
            data=body,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310 - user explicitly configures trusted internal service
                decoded = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            return None, f"http_error: {exc.code}"
        except URLError as exc:
            return None, f"network_error: {exc.reason}"
        except (OSError, TimeoutError) as exc:
            return None, f"network_error: {exc}"
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None, "protocol_error: invalid_json"
        if not isinstance(decoded, Mapping):
            return None, "protocol_error: response_not_object"
        return decoded, None


def _failed_result(job_id: Optional[str], submitted_at: datetime, error: str) -> JobResult:
    return JobResult(
        job_id=job_id,
        submitted_at=submitted_at,
        completed_at=_utc_now(),
        articles=(),
        error=error,
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
