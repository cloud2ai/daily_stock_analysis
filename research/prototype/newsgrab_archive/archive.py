"""DSA-owned immutable archive files for NewsGrab collection runs."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional
from uuid import uuid4

from .availability import classify_strict_pit
from .models import ArchiveRequest, JobResult


ARCHIVE_SCHEMA_VERSION = "dsa-newsgrab-archive-v1"


class ArchiveWriter:
    """Persist a completed or failed NewsGrab run without overwriting evidence."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def write_run(
        self,
        request: ArchiveRequest,
        result: JobResult,
        *,
        archived_at: datetime,
        run_id: Optional[str] = None,
    ) -> Path:
        _require_aware("archived_at", archived_at)
        normalized_archived_at = archived_at.astimezone(timezone.utc)
        safe_run_id = run_id or uuid4().hex
        if not safe_run_id or any(character in safe_run_id for character in "/\\"):
            raise ValueError("run_id must be a non-empty path component")
        output = self.root / normalized_archived_at.strftime("%Y-%m-%d") / safe_run_id
        output.mkdir(parents=True, exist_ok=False)

        article_rows, error_rows = self._normalize_articles(
            result.articles, request=request, result=result, archived_at=normalized_archived_at
        )
        if result.error is not None:
            error_rows.append(
                {
                    "error_code": "newsgrab_job_failed",
                    "reason": result.error,
                    "job_id": result.job_id,
                }
            )

        articles_path = output / "articles.jsonl"
        errors_path = output / "errors.jsonl"
        self._write_jsonl(articles_path, article_rows)
        self._write_jsonl(errors_path, error_rows)
        manifest = {
            "schema_version": ARCHIVE_SCHEMA_VERSION,
            "run_status": "completed" if result.succeeded else "failed",
            "request": {
                "query": request.query,
                "scope": request.scope,
                "language": request.language,
                "region": request.region,
                "max_results": request.max_results,
                "days": request.days,
            },
            "job_id": result.job_id,
            "submitted_at": _isoformat(result.submitted_at),
            "completed_at": _isoformat(result.completed_at),
            "archived_at": _isoformat(normalized_archived_at),
            "article_count": len(article_rows),
            "error_count": len(error_rows),
            "files": {
                "articles.jsonl": {"sha256": _sha256_file(articles_path)},
                "errors.jsonl": {"sha256": _sha256_file(errors_path)},
            },
        }
        (output / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        manifest_path = output / "manifest.json"
        (output / "manifest.sha256").write_text(
            f"{_sha256_file(manifest_path)}  manifest.json\n", encoding="utf-8"
        )
        _seal_archive(output)
        return output

    @staticmethod
    def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
        with path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    @staticmethod
    def _normalize_articles(
        articles: Iterable[Any],
        *,
        request: ArchiveRequest,
        result: JobResult,
        archived_at: datetime,
    ) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
        accepted: list[Mapping[str, Any]] = []
        errors: list[Mapping[str, Any]] = []
        seen_fingerprints: set[str] = set()
        for raw_article in articles:
            if not isinstance(raw_article, Mapping):
                errors.append(
                    {
                        "error_code": "article_not_object",
                        "reason": "newsgrab_result_item_must_be_object",
                        "job_id": result.job_id,
                        "raw_article": raw_article,
                    }
                )
                continue
            invalid_field = _missing_required_field(raw_article)
            if invalid_field is not None:
                errors.append(
                    {
                        "error_code": f"article_missing_{invalid_field}",
                        "reason": f"article_{invalid_field}_required_for_archive",
                        "job_id": result.job_id,
                        "raw_article": raw_article,
                    }
                )
                continue
            fingerprint = _canonical_sha256(raw_article)
            if fingerprint in seen_fingerprints:
                errors.append(
                    {
                        "error_code": "duplicate_article_in_run",
                        "reason": "duplicate_raw_article_fingerprint",
                        "job_id": result.job_id,
                        "raw_article": raw_article,
                    }
                )
                continue
            seen_fingerprints.add(fingerprint)
            availability = classify_strict_pit(
                raw_article.get("published_date"),
                archived_at=archived_at,
                decision_cutoff=archived_at,
            )
            content = str(raw_article["content"])
            accepted.append(
                {
                    "query": request.query,
                    "scope": request.scope,
                    "language": request.language,
                    "region": request.region,
                    "job_id": result.job_id,
                    "archived_at": _isoformat(archived_at),
                    "title": raw_article["title"],
                    "content": content,
                    "source": raw_article["source"],
                    "url": raw_article["url"],
                    "resolved_url": raw_article.get("resolved_url") or raw_article.get("final_url"),
                    "published_at": _isoformat(availability.published_at),
                    "published_at_precision": availability.published_at_precision,
                    "strict_pit_status": availability.status,
                    "strict_pit_reason": availability.reason,
                    "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    "raw_article": raw_article,
                }
            )
        return accepted, errors


def _missing_required_field(article: Mapping[str, Any]) -> Optional[str]:
    for field in ("title", "content", "url", "source"):
        value = article.get(field)
        if not isinstance(value, str) or not value.strip():
            return field
    return None


def _canonical_sha256(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _seal_archive(output: Path) -> None:
    """Make a completed local run read-only after all audit files are written."""

    for path in output.iterdir():
        path.chmod(0o444)
    output.chmod(0o555)


def _isoformat(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    _require_aware("timestamp", value)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _require_aware(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
