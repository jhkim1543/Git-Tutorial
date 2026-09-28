"""Supervise disposable Blender processes running worker.py on a JSON plan."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

from ..config import settings

WORKER = Path(__file__).with_name("worker.py")


class BlenderUnavailable(RuntimeError):
    pass


class BlenderFailed(RuntimeError):
    def __init__(self, message: str, diagnostic: dict):
        super().__init__(message)
        self.diagnostic = diagnostic


def run_plan(job_dir: Path, ops: list[dict], *, budget_s: int, on_progress: Callable[[dict], None] | None = None) -> dict:
    command = settings.blender_command()
    if command is None:
        raise BlenderUnavailable("Blender not found: set FIG3D_BLENDER_PATH or install the bpy module")
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "plan.json").write_text(json.dumps({"ops": ops}, indent=1), encoding="utf-8")
    for stale in ("result.json", "progress.json"):
        (job_dir / stale).unlink(missing_ok=True)
    args = command + [str(WORKER), "--", str(job_dir)] if command[0] != sys.executable else command + [str(WORKER), str(job_dir)]
    started = time.monotonic()
    timed_out = False
    with (job_dir / "blender.log").open("w", encoding="utf-8") as log:
        proc = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, cwd=job_dir)
        try:
            while proc.poll() is None:
                if on_progress:
                    try:
                        on_progress(json.loads((job_dir / "progress.json").read_text(encoding="utf-8")))
                    except (OSError, ValueError):
                        pass
                if time.monotonic() - started > budget_s:
                    timed_out = True
                    break
                time.sleep(0.5)
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
    diagnostic = dict(seconds=round(time.monotonic() - started, 2), budget_s=budget_s, timed_out=timed_out,
                      returncode=proc.returncode,
                      log_tail=(job_dir / "blender.log").read_text(encoding="utf-8", errors="replace")[-4000:])
    result_path = job_dir / "result.json"
    if timed_out or not result_path.is_file():
        raise BlenderFailed("Blender timed out" if timed_out else "Blender produced no result", diagnostic)
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["process"] = {k: v for k, v in diagnostic.items() if k != "log_tail"}
    if result.get("status") != "done":
        errors = [o for o in result["ops"] if o.get("status") == "error"]
        diagnostic["errors"] = errors
        raise BlenderFailed(errors[0]["error"] if errors else "Blender plan failed", diagnostic)
    return result
