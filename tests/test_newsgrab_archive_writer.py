import json
import hashlib
import stat
from datetime import datetime

import pytest

from research.prototype.newsgrab_archive.archive import ArchiveWriter
from research.prototype.newsgrab_archive.models import ArchiveRequest, JobResult


def utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def request() -> ArchiveRequest:
    return ArchiveRequest(query="600519", scope="company")


def done_result(*articles) -> JobResult:
    now = utc("2026-07-28T07:55:00Z")
    return JobResult(job_id="job-123", submitted_at=now, completed_at=now, articles=articles)


def valid_article(**overrides):
    article = {
        "title": "Verified article",
        "content": "Full text",
        "url": "https://example.com/story",
        "source": "example.com",
        "published_date": "2026-07-28T07:30:00Z",
    }
    article.update(overrides)
    return article


def test_writer_records_checksums_and_rejects_a_reused_run_directory(tmp_path):
    output = ArchiveWriter(tmp_path).write_run(
        request(),
        done_result(valid_article()),
        archived_at=utc("2026-07-28T08:00:00Z"),
        run_id="fixed",
    )

    manifest = json.loads((output / "manifest.json").read_text())
    article = json.loads((output / "articles.jsonl").read_text())
    assert manifest["run_status"] == "completed"
    assert manifest["files"]["articles.jsonl"]["sha256"]
    assert article["strict_pit_status"] == "eligible"
    assert article["content_sha256"]
    manifest_digest = (output / "manifest.sha256").read_text().strip().split()[0]
    assert manifest_digest == hashlib.sha256((output / "manifest.json").read_bytes()).hexdigest()
    assert not (output.stat().st_mode & stat.S_IWUSR)
    assert not ((output / "manifest.json").stat().st_mode & stat.S_IWUSR)
    with pytest.raises(FileExistsError):
        ArchiveWriter(tmp_path).write_run(
            request(),
            done_result(valid_article()),
            archived_at=utc("2026-07-28T08:00:00Z"),
            run_id="fixed",
        )


def test_writer_records_invalid_article_and_failed_job_as_audit_errors(tmp_path):
    result = done_result(valid_article(content=""), valid_article(title="second"))
    output = ArchiveWriter(tmp_path).write_run(
        request(), result, archived_at=utc("2026-07-28T08:00:00Z"), run_id="invalid"
    )

    errors = [json.loads(line) for line in (output / "errors.jsonl").read_text().splitlines()]
    articles = [json.loads(line) for line in (output / "articles.jsonl").read_text().splitlines()]
    assert errors[0]["error_code"] == "article_missing_content"
    assert len(articles) == 1

    failed_output = ArchiveWriter(tmp_path).write_run(
        request(),
        JobResult(
            job_id="job-124",
            submitted_at=utc("2026-07-28T08:01:00Z"),
            completed_at=utc("2026-07-28T08:02:00Z"),
            articles=(),
            error="newsgrab_failed: upstream timeout",
        ),
        archived_at=utc("2026-07-28T08:02:00Z"),
        run_id="failed",
    )
    failed_manifest = json.loads((failed_output / "manifest.json").read_text())
    failed_errors = [
        json.loads(line) for line in (failed_output / "errors.jsonl").read_text().splitlines()
    ]
    assert failed_manifest["run_status"] == "failed"
    assert failed_errors[0]["error_code"] == "newsgrab_job_failed"


def test_writer_keeps_non_object_newsgrab_result_as_an_explicit_protocol_error(tmp_path):
    output = ArchiveWriter(tmp_path).write_run(
        request(),
        done_result("not an article object"),
        archived_at=utc("2026-07-28T08:00:00Z"),
        run_id="malformed",
    )

    errors = [json.loads(line) for line in (output / "errors.jsonl").read_text().splitlines()]
    assert errors == [
        {
            "error_code": "article_not_object",
            "job_id": "job-123",
            "raw_article": "not an article object",
            "reason": "newsgrab_result_item_must_be_object",
        }
    ]
