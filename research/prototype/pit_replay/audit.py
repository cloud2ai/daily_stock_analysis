"""Immutable PIT replay audit artifacts."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime, time
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Iterable, Mapping
from uuid import uuid4


def _json_default(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"cannot serialize {type(value).__name__}")


class AuditWriter:
    """Write one non-overwriting run directory and content hashes for its JSONL."""

    def __init__(self, output_root: Path) -> None:
        self.output_dir = output_root / f"pit-replay-{uuid4().hex}"
        self.output_dir.mkdir(parents=True, exist_ok=False)
        self._written: list[Path] = []

    def write_jsonl(self, name: str, records: Iterable[Mapping[str, Any]]) -> Path:
        path = self.output_dir / f"{name}.jsonl"
        with path.open("x", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True, default=_json_default))
                handle.write("\n")
        self._written.append(path)
        return path

    def write_json(self, name: str, payload: Mapping[str, Any]) -> Path:
        path = self.output_dir / name
        with path.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True, default=_json_default)
            handle.write("\n")
        return path

    def write_checksums(self) -> Path:
        payload = {
            path.name: sha256(path.read_bytes()).hexdigest()
            for path in sorted(self._written)
        }
        return self.write_json("checksums.json", payload)
