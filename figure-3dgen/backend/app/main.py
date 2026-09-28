"""Figure 3DGen API. Everything is served under FIG3D_BASE_PATH (default /3dgen):
  /3dgen/api/...   JSON API          /3dgen/          built frontend (frontend/dist)
"""
from __future__ import annotations

import io
import json
import mimetypes
import zipfile
from pathlib import Path

from fastapi import APIRouter, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import astra, jobs
from .config import ROOT, criteria, joint_library, settings
from .pipeline import editable, intake, joints_stage, moldable, split, views

VERSION = "0.1.0"
app = FastAPI(title="Figure 3DGen", version=VERSION, docs_url=f"{settings.base_path}/api/docs",
              openapi_url=f"{settings.base_path}/api/openapi.json")
api = APIRouter(prefix=f"{settings.base_path}/api")


def _job(job_id: str) -> dict:
    try:
        return jobs.load(job_id)
    except KeyError:
        raise HTTPException(404, "job not found") from None


def _guard(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except PermissionError as e:
        raise HTTPException(409, str(e)) from None
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    except KeyError:
        raise HTTPException(404, "job not found") from None


def _start(job_id: str, stage: str, fn, *, needs: tuple[str, tuple[str, ...]] | None = None):
    job = _job(job_id)
    if needs:
        _guard(jobs.require, job, needs[0], *needs[1])
    if jobs.is_running(job_id):
        raise HTTPException(409, "another stage of this job is running")
    jobs.reset_downstream(job_id, stage)
    _guard(jobs.submit, job_id, stage, lambda: fn(job_id))
    return _job(job_id)


@api.get("/health")
def health():
    return dict(ok=True, version=VERSION, blender=settings.blender_command() is not None, astra=astra.available(),
                astra_model=settings.astra_model, base_path=settings.base_path)


@api.get("/reference/criteria")
def get_criteria():
    return criteria()


@api.get("/reference/s3")
def get_s3():
    import csv
    manifest = list(csv.DictReader((ROOT / "reference" / "s3_50_manifest.csv").open(encoding="utf-8-sig")))
    analysis = list(csv.DictReader((ROOT / "reference" / "s3_50_analysis.csv").open(encoding="utf-8-sig")))
    return dict(samples=len(manifest), analysed=sum(r.get("AnalysisStatus") not in ("", "NOT_ANALYZED") for r in analysis),
                manifest=manifest, analysis=analysis, joint_library=joint_library())


@api.get("/jobs")
def list_jobs():
    return jobs.list_jobs()


@api.post("/jobs")
async def create_job(photo: UploadFile = File(...), glb: UploadFile = File(...), title: str = Form(""),
                     height_mm: float = Form(100.0), up_axis: str = Form("Y"), material: str = Form("PVC_ROTO")):
    if not (20 <= height_mm <= 600) or up_axis not in ("Y", "Z") or material not in criteria()["moldable"]["materials"]:
        raise HTTPException(400, "invalid options")
    photo_bytes, glb_bytes = await photo.read(), await glb.read()
    limit = settings.max_upload_mb << 20
    if len(glb_bytes) > limit or len(photo_bytes) > 40 << 20:
        raise HTTPException(413, "file too large")
    job = jobs.create(title or (glb.filename or "figure"), dict(height_mm=height_mm, up_axis=up_axis, material=material,
                                                               photo_name=photo.filename, glb_name=glb.filename))
    jobs.submit(job["id"], "intake", lambda: intake.run(job["id"], photo_bytes, glb_bytes))
    return _job(job["id"])


@api.get("/jobs/{job_id}")
def get_job(job_id: str):
    job = _job(job_id)
    root = jobs.job_dir(job_id)
    files = {}
    for rel in ("views/meta.json", "split/review.json", "split/applied.json", "editable/report.json", "joints/plan.json",
                "moldable/report.json", "intake/report.json"):
        p = root / rel
        if p.is_file():
            files[rel] = True
    job["available"] = sorted(files)
    return job


class ViewApproval(BaseModel):
    views: list[str]


@api.post("/jobs/{job_id}/views/generate")
def views_generate(job_id: str):
    if not astra.available():
        raise HTTPException(409, "Astra (OPENAI_API_KEY) is not configured: upload views or use the photo as the front view")
    return _start(job_id, "views", views.generate, needs=("intake", ("done",)))


@api.post("/jobs/{job_id}/views/approve")
def views_approve(job_id: str, body: ViewApproval):
    job = _job(job_id)
    _guard(jobs.require, job, "views", "review", "approved")
    fields = _guard(views.approve, job_id, body.views)
    jobs.reset_downstream(job_id, "views")
    jobs.set_stage(job_id, "views", **fields)
    return _job(job_id)


@api.post("/jobs/{job_id}/views/{view}")
async def views_upload(job_id: str, view: str, file: UploadFile | None = File(None), use_photo: bool = Form(False)):
    job = _job(job_id)
    _guard(jobs.require, job, "intake", "done")
    data = await file.read() if file else None
    meta = _guard(views.upload, job_id, view, data, use_photo=use_photo)
    jobs.reset_downstream(job_id, "views")
    jobs.set_stage(job_id, "views", status="review", views=meta)
    return _job(job_id)


@api.post("/jobs/{job_id}/split/analyze")
def split_analyze(job_id: str):
    return _start(job_id, "split", split.analyze, needs=("views", ("approved",)))


class SplitApply(BaseModel):
    decisions: dict[str, str] = {}
    labels: dict[str, str] = {}


@api.post("/jobs/{job_id}/split/apply")
def split_apply(job_id: str, body: SplitApply):
    job = _job(job_id)
    _guard(jobs.require, job, "split", "review", "approved")
    if jobs.is_running(job_id):
        raise HTTPException(409, "running")
    jobs.reset_downstream(job_id, "split")
    _guard(jobs.submit, job_id, "split", lambda: split.apply(job_id, body.decisions, body.labels))
    return _job(job_id)


@api.post("/jobs/{job_id}/editable/build")
def editable_build(job_id: str):
    return _start(job_id, "editable", editable.build, needs=("split", ("approved",)))


@api.post("/jobs/{job_id}/editable/approve")
def editable_approve(job_id: str):
    job = _job(job_id)
    _guard(jobs.require, job, "editable", "review")
    fields = _guard(editable.approve, job_id)
    jobs.reset_downstream(job_id, "editable")
    jobs.set_stage(job_id, "editable", **fields)
    return _job(job_id)


@api.post("/jobs/{job_id}/joints/plan")
def joints_plan(job_id: str):
    return _start(job_id, "joints", joints_stage.plan, needs=("editable", ("approved",)))


class JointApproval(BaseModel):
    plans: list[dict] = []


@api.post("/jobs/{job_id}/joints/approve")
def joints_approve(job_id: str, body: JointApproval):
    job = _job(job_id)
    _guard(jobs.require, job, "joints", "review", "approved")
    fields = _guard(joints_stage.approve, job_id, body.model_dump())
    jobs.reset_downstream(job_id, "joints")
    jobs.set_stage(job_id, "joints", **fields)
    return _job(job_id)


@api.post("/jobs/{job_id}/moldable/build")
def moldable_build(job_id: str):
    return _start(job_id, "moldable", moldable.build, needs=("joints", ("approved",)))


ALLOWED = {".png", ".json", ".glb", ".obj", ".blend", ".stl"}


@api.get("/jobs/{job_id}/files/{path:path}")
def get_file(job_id: str, path: str):
    root = _guard(jobs.job_dir, job_id).resolve()
    target = (root / path).resolve()
    if root not in target.parents or target.suffix not in ALLOWED or not target.is_file() or "cache" in target.parts:
        raise HTTPException(404, "not found")
    return FileResponse(target, media_type=mimetypes.guess_type(target.name)[0] or "application/octet-stream")


BUNDLES = {
    "editable": ["editable/editable.glb", "editable/report.json", "editable/native/editable.blend", "editable/native/editable.obj"],
    "moldable": ["moldable/moldable.glb", "moldable/report.json", "moldable/native/moldable.blend", "moldable/native/moldable.obj",
                 "moldable/stl/*.stl", "joints/plan.json", "joints/sheets/*.png"],
}


@api.get("/jobs/{job_id}/download/{stage}.zip")
def download(job_id: str, stage: str):
    job = _job(job_id)
    root = jobs.job_dir(job_id)
    if stage not in BUNDLES or job["stages"][stage]["status"] not in ("review", "approved", "done", "blocked"):
        raise HTTPException(404, "not available")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for pattern in BUNDLES[stage]:
            for p in sorted(root.glob(pattern)):
                z.write(p, p.relative_to(root))
        z.writestr("STATUS.json", json.dumps(dict(stage=stage, status=job["stages"][stage]["status"],
                                                  release_approved=False,
                                                  note="blocked = diagnostic only; human approvals still required"), indent=1))
    suffix = "" if job["stages"][stage]["status"] in ("approved", "done", "review") else "-diagnostic"
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{job_id}-{stage}{suffix}.zip"'})


app.include_router(api)

DIST = ROOT / "frontend" / "dist"
if DIST.is_dir():
    app.mount(settings.base_path, StaticFiles(directory=DIST, html=True), name="frontend")


@app.get("/")
def root_redirect():
    return JSONResponse(dict(app="figure-3dgen", ui=f"{settings.base_path}/", api=f"{settings.base_path}/api/health"))
