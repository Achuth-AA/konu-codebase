"""JSONL / CSV writers."""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import IO, Iterable

from .models import FIELDS, Record


class Writer:
    def __init__(self, path: str | None, include_raw: bool = True) -> None:
        self.path = path
        self.include_raw = include_raw
        self.fmt = "csv" if path and path.endswith(".csv") else "jsonl"
        self._fh: IO[str] = open(path, "w", newline="", encoding="utf-8") if path else sys.stdout
        self._csv = None
        if self.fmt == "csv":
            cols = FIELDS if include_raw else [f for f in FIELDS if f != "raw"]
            self._csv = csv.DictWriter(self._fh, fieldnames=cols, extrasaction="ignore")
            self._csv.writeheader()
        self.count = 0

    def write(self, rec: Record) -> None:
        d = rec.to_dict(self.include_raw)
        if self._csv is not None:
            if "raw" in d:
                d["raw"] = json.dumps(d["raw"], ensure_ascii=False, default=str)
            self._csv.writerow(d)
        else:
            self._fh.write(json.dumps(d, ensure_ascii=False, default=str) + "\n")
        self.count += 1

    def write_all(self, recs: Iterable[Record]) -> int:
        for r in recs:
            self.write(r)
        return self.count

    def close(self) -> None:
        if self.path:
            self._fh.close()
        else:
            self._fh.flush()


def out_path(template: str | None, source: str) -> str | None:
    """``data/{source}.jsonl`` → ``data/nobroker.jsonl``; creates the folder."""
    if not template:
        return None
    p = Path(template.format(source=source))
    p.parent.mkdir(parents=True, exist_ok=True)
    return str(p)
