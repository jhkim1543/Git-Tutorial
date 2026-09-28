"""Disk-backed job store with an explicit stage state machine.

Stages run in order. Re-running or re-approving a stage resets every downstream stage, because
their evidence was bound to geometry that no longer exists.
"""
from __future__ import annotations

import json
import secrets
import shutil
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from .config import settings

STAGES = ("intake", "views", "split", "editable", "joints", "moldable")
# pending -> running -> review (needs a human decision) -> approved | done | blocked | failed
_lock = threading.RLock()
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="fig3d")   # one heavy job at a time
_running: set[str] = set()


def jobs_root() -> Path:
    root = settings.data_dir / "jobs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def job_dir(job_id: str) -> Path:
    if not job_id or not all(c.isalnum() or c in "-_" for c in job_id):
        raise KeyError(job_id)
    path = jobs_root() / job_id
    if not (path / "job.json").is_file():
        raise KeyError(job_id)
    return path


def load(job_id: str) -> dict:
    with _lock:
        return json.loads((job_dir(job_id) / "job.json").read_text(encoding="utf-8"))


def save(job: dict) -> None:
    with _lock:
        path = jobs_root() / job["id"] / "job.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(job, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)


def create(title: str, options: dict) -> dict:
    job_id = time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)
    (jobs_root() / job_id).mkdir()
    job = dict(id=job_id, title=title, created=time.time(), options=options,
               stages={s: dict(status="pending") for s in STAGES}, progress=None, events=[])
    save(job)
    return job


def list_jobs() -> list[dict]:
    out = []
    for p in sorted(jobs_root().iterdir(), reverse=True):
        try:
            j = json.loads((p / "job.json").read_text(encoding="utf-8"))
            out.append(dict(id=j["id"], title=j.get("title"), created=j["created"],
                            stages={k: v["status"] for k, v in j["stages"].items()}))
        except (OSError, ValueError, KeyError):
            continue
    return out[:100]


def update(job_id: str, fn: Callable[[dict], None]) -> dict:
    with _lock:
        job = load(job_id)
        fn(job)
        save(job)
        return job


def event(job_id: str, message: str, level: str = "info") -> None:
    def add(j):
        j["events"] = (j.get("events", []) + [dict(t=time.time(), level=level, message=message)])[-80:]
    update(job_id, add)


def set_stage(job_id: str, stage: str, **fields) -> dict:
    def apply(j):
        j["stages"][stage].update(fields)
        j["stages"][stage]["updated"] = time.time()
    return update(job_id, apply)


def reset_downstream(job_id: str, stage: str) -> None:
    after = STAGES[STAGES.index(stage) + 1:]
    root = job_dir(job_id)

    def apply(j):
        for s in after:
            j["stages"][s] = dict(status="pending", reset_by=stage, updated=time.time())
    update(job_id, apply)
    for s in after:
        shutil.rmtree(root / s, ignore_errors=True)


def require(job: dict, stage: str, *allowed: str) -> None:
    status = job["stages"][stage]["status"]
    if status not in allowed:
        raise PermissionError(f"{stage} must be {' / '.join(allowed)} (now {status})")


def progress(job_id: str, stage: str, pct: float, message: str) -> None:
    update(job_id, lambda j: j.__setitem__("progress", dict(stage=stage, pct=round(pct, 1), message=message, t=time.time())))


def submit(job_id: str, stage: str, fn: Callable[[], dict | None]) -> None:
    """Run a stage in the background. Exceptions become `failed` with the message, never success."""
    with _lock:
        if job_id in _running:
            raise PermissionError("another stage of this job is running")
        _running.add(job_id)
    set_stage(job_id, stage, status="running", error=None)

    def work():
        try:
            fields = fn() or {}
            fields.setdefault("status", "done")
            set_stage(job_id, stage, **fields)
            event(job_id, f"{stage}: {fields['status']}")
        except Exception as exc:  # recorded verbatim
            set_stage(job_id, stage, status="failed", error=f"{type(exc).__name__}: {exc}",
                      trace=traceback.format_exc()[-3000:])
            event(job_id, f"{stage} failed: {exc}", "error")
        finally:
            with _lock:
                _running.discard(job_id)
            update(job_id, lambda j: j.__setitem__("progress", None))

    _executor.submit(work)


def is_running(job_id: str) -> bool:
    with _lock:
        return job_id in _running
