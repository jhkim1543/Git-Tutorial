"""FreeStyle / FreeForm `.cly` metadata scan and manifest verification.

The .cly container is proprietary. This module does NOT parse geometry and never infers joints from
headers: it only extracts printable metadata strings (program version, units) from the first bytes
and checks size / SHA-256 against the manifest. Geometry must be exported from FreeForm (STL/PLY per
part) and analysed with reference.analyze.
"""
from __future__ import annotations

import csv
import hashlib
import re
from pathlib import Path

VERSION_RE = re.compile(rb"(Freeform[^\x00]{0,80}|FreeStyle[^\x00]{0,80})")
UNIT_RE = re.compile(rb"\b(mm|millimet(?:er|re)s?|inch(?:es)?)\b", re.I)


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def scan_header(path: Path, limit: int = 256 * 1024) -> dict:
    head = path.open("rb").read(limit)
    strings = [s.decode("latin-1") for s in re.findall(rb"[\x20-\x7e]{6,}", head)][:60]
    version = VERSION_RE.search(head)
    unit = UNIT_RE.search(head)
    return dict(file=path.name, bytes=path.stat().st_size,
                program=version.group(0).decode("latin-1").strip() if version else None,
                unit_hint=unit.group(0).decode("latin-1") if unit else None,
                printable_strings=strings, format_verified=False,
                note="metadata strings only; geometry/joints require a FreeForm export")


def verify_manifest(manifest_csv: Path, folder: Path) -> dict:
    rows = list(csv.DictReader(manifest_csv.open(encoding="utf-8-sig")))
    out = []
    for r in rows:
        p = folder / r["Filename"]
        if not p.is_file():
            out.append(dict(index=r["Index"], sample=r["SampleId"], status="MISSING"))
            continue
        size_ok = p.stat().st_size == int(r["Bytes"])
        sha_ok = size_ok and sha256_file(p) == r["SHA256"].lower()
        out.append(dict(index=r["Index"], sample=r["SampleId"], status="PASS" if sha_ok else "FAIL", size_ok=size_ok))
    return dict(total=len(rows), passed=sum(o["status"] == "PASS" for o in out),
                missing=sum(o["status"] == "MISSING" for o in out), rows=out)
