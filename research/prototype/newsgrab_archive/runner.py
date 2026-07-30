"""Explicit CLI for one forward-only DSA NewsGrab archive run."""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone
from typing import Optional, Sequence

from .archive import ArchiveWriter
from .client import NewsGrabClient
from .models import ArchiveRequest, VALID_SCOPES


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Archive one NewsGrab collection run for future DSA PIT research."
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("GOOGLE_NEWS_COLLECTOR_URL"),
        required=os.getenv("GOOGLE_NEWS_COLLECTOR_URL") is None,
        help="Trusted NewsGrab collector URL; defaults to GOOGLE_NEWS_COLLECTOR_URL when configured",
    )
    parser.add_argument(
        "--archive-root",
        default=os.getenv("NEWSGRAB_ARCHIVE_ROOT", "data/newsgrab_archive"),
        help="DSA-owned, write-once archive root",
    )
    parser.add_argument("--query", required=True, help="Company, industry, or market query text")
    parser.add_argument("--scope", required=True, choices=sorted(VALID_SCOPES))
    parser.add_argument("--language", default="zh-CN")
    parser.add_argument("--region", default="CN")
    parser.add_argument("--max-results", type=int, default=10)
    parser.add_argument("--days", type=int, choices=(1,), default=1)
    parser.add_argument("--timeout-seconds", type=float, default=15.0)
    parser.add_argument("--poll-interval-seconds", type=float, default=1.0)
    parser.add_argument("--max-polls", type=int, default=30)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    request = ArchiveRequest(
        query=args.query,
        scope=args.scope,
        language=args.language,
        region=args.region,
        max_results=args.max_results,
        days=args.days,
    )
    client = NewsGrabClient(
        args.base_url,
        timeout_seconds=args.timeout_seconds,
        poll_interval_seconds=args.poll_interval_seconds,
        max_polls=args.max_polls,
    )
    result = client.collect(request)
    output = ArchiveWriter(args.archive_root).write_run(
        request,
        result,
        archived_at=datetime.now(timezone.utc),
    )
    print(f"NewsGrab archive written: {output}")
    if result.error is not None:
        print(f"NewsGrab archive failed: {result.error}")
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
