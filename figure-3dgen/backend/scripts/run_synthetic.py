"""Offline end-to-end run on the synthetic figure (no Astra key needed).

The approved views are renders of the synthetic *truth* (standing in for photo-derived views);
the GLB is a deliberately broken AI-style partition. Usage:
    FIG3D_DATA_DIR=/tmp/fig3d python scripts/run_synthetic.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HERE), str(HERE / "tests")]

import synthetic  # noqa: E402
from app import jobs  # noqa: E402
from app.pipeline import editable, intake, joints_stage, moldable, split, views  # noqa: E402


def step(jid, stage, fn, *a):
    t = time.time()
    fields = fn(jid, *a)
    jobs.set_stage(jid, stage, **fields)
    print(f"{stage:9s} {fields['status']:9s} {time.time() - t:6.1f}s  {json.dumps(fields.get('summary', {}), ensure_ascii=False)[:300]}")
    return fields


def main() -> int:
    job = jobs.create("synthetic figure (test only)", dict(height_mm=100.0, up_axis="Y", material="PVC_ROTO"))
    jid = job["id"]
    pngs = synthetic.view_pngs()
    step(jid, "intake", intake.run, pngs["front"], synthetic.ai_glb())
    for view, data in pngs.items():
        views.upload(jid, view, data)
    step(jid, "views", views.approve, list(pngs))
    step(jid, "split", split.analyze)
    step(jid, "split", split.apply, {}, {"mesh_head_0": "head", "mesh_eye_0": "eye", "mesh_cape_0": "cape", "mesh_body": "body"})
    ed = step(jid, "editable", editable.build)
    if ed["status"] != "review":
        print("editable blocked — see", jobs.job_dir(jid) / "editable" / "report.json")
        return 1
    step(jid, "editable", editable.approve)
    step(jid, "joints", joints_stage.plan)
    step(jid, "joints", joints_stage.approve, {})
    mo = step(jid, "moldable", moldable.build)
    print("job:", jid, "->", jobs.job_dir(jid))
    return 0 if mo["status"] == "done" else 2


if __name__ == "__main__":
    sys.exit(main())
