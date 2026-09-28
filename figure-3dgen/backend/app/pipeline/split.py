"""Stage 3 — split review of the AI GLB partition (judge model + Astra) and Blender re-split.

1. analyze(): seam evidence for every adjacent part pair -> SplitJudge proposal -> Astra vision check
   (original photo views vs. review renders). Disagreement or low confidence -> needs human decision.
2. apply(): human-approved decisions become Blender commands (join / separate). Approved decisions are
   logged as training labels and the judge is re-fitted (feedback loop).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import trimesh

from .. import astra, jobs
from ..blender.runner import run_plan
from ..config import settings
from ..geometry import meshio, segmentation
from ..geometry.render import VIEWS, png_bytes, render
from .intake import load_reference
from .views import load_masks

MODEL_DIR = lambda: settings.data_dir / "models"   # noqa: E731


def judge() -> segmentation.SplitJudge:
    p = MODEL_DIR() / "split_judge.json"
    return segmentation.SplitJudge(json.loads(p.read_text()) if p.is_file() else None)


def analyze(job_id: str) -> dict:
    root = jobs.job_dir(job_id)
    job = jobs.load(job_id)
    out = root / "split"
    out.mkdir(exist_ok=True)
    parts = load_reference(root)
    jobs.progress(job_id, "split", 10, "경계 근거 분석")
    evidence = segmentation.analyze(parts, job["options"]["height_mm"])
    model = judge()
    for seam in evidence["seams"]:
        seam["judge"] = model.decide(seam)
    # review renders: one per seam (pair highlighted), front + right
    jobs.progress(job_id, "split", 35, "검토용 렌더 생성")
    renders = out / "renders"
    renders.mkdir(exist_ok=True)
    bounds = np.array([np.min([m.bounds[0] for m in parts.values()], 0), np.max([m.bounds[1] for m in parts.values()], 0)])
    for view in ("front", "right", "back", "left"):
        (renders / f"parts-{view}.png").write_bytes(png_bytes(render(parts, view, size=512, bounds=bounds,
                                                                     colors={n: meshio.part_color(i) for i, n in enumerate(parts)})))
    flagged = [s for s in evidence["seams"] if s["judge"]["action"] != "keep"]
    center = bounds.mean(0)
    for s in flagged[:24]:
        best = max(VIEWS, key=lambda v: float(np.dot(np.asarray(s["origin"]) - center, VIEWS[v][0])))
        img = render(parts, best, size=384, bounds=bounds, highlight={s["a"], s["b"]})
        (renders / f"{s['id']}.png").write_bytes(png_bytes(img))
    astra_result, astra_status = None, "NOT_REQUESTED"
    if astra.available() and flagged:
        jobs.progress(job_id, "split", 55, "Astra 분할 검토")
        views = [(f"approved-{v}.png", (root / "views" / f"{v}.png").read_bytes()) for v in load_masks(root)]
        images = views + [(f"model-parts-{v}.png", (renders / f"parts-{v}.png").read_bytes()) for v in ("front", "right")]
        images += [(f"seam-{s['id']}.png", (renders / f"{s['id']}.png").read_bytes()) for s in flagged[:10]]
        prompt = ("The approved photo views are ground truth. The coloured renders show how an AI model divided the figure "
                  "into parts; that division may be wrong. For every seam below decide: 'merge' when both parts are the same "
                  "region split into messy patches (same cape, same eye, same hair mass), 'resplit_clean' when the two regions "
                  "are genuinely different design parts (skin vs cloth, eye vs face) but the cut is ragged, 'keep' when the "
                  "boundary is a clean real design boundary, 'uncertain' otherwise. Also give each part a short semantic label "
                  "(head, hair, face, eye_left, cape, torso, arm_right, ...). Seam evidence (geometry, not ground truth): "
                  + json.dumps([{k: s[k] for k in ("id", "a", "b", "seam_mm", "share_a", "share_b", "dihedral_median_deg",
                                                    "jaggedness", "smaller_area_share")} | {"judge": s["judge"]["action"]}
                                for s in flagged], ensure_ascii=False))
        try:
            astra_result = astra.review_json(prompt, images, astra.SPLIT_SCHEMA, name="split_review", cache=root / "cache" / "astra")
            astra_status = "DONE"
        except astra.AstraError as exc:
            astra_status = f"ERROR: {exc}"
    elif not astra.available():
        astra_status = "BLOCKED: OPENAI_API_KEY not configured"
    decisions = combine(evidence["seams"], astra_result)
    labels = {p["part"]: p["label"] for p in (astra_result or {}).get("result", {}).get("part_labels", [])}
    review = dict(evidence=evidence, judge=model.params(), astra=dict(status=astra_status, **(astra_result or {})),
                  decisions=decisions, part_labels=labels)
    (out / "review.json").write_text(json.dumps(review, indent=1, ensure_ascii=False), encoding="utf-8")
    needs = sum(d["needs_human"] for d in decisions)
    return dict(status="review", summary=dict(seams=len(evidence["seams"]), proposals=sum(d["action"] != "keep" for d in decisions),
                                               needs_human=needs, astra=astra_status))


def combine(seams: list[dict], astra_result: dict | None) -> list[dict]:
    verdict = {d["seam_id"]: d for d in (astra_result or {}).get("result", {}).get("decisions", [])}
    out = []
    for s in seams:
        j = s["judge"]
        a = verdict.get(s["id"])
        action = j["action"]
        needs = False
        if a:
            if a["action"] == "uncertain":
                needs = True
            elif a["action"] != j["action"]:
                # Astra sees the photo; geometry cannot tell design intent. Prefer Astra, but ask a human.
                action, needs = a["action"], True
        else:
            needs = j["action"] != "keep" and 0.35 < j["p_merge"] < 0.8
        out.append(dict(seam_id=s["id"], a=s["a"], b=s["b"], action=action, judge=j, astra=a, needs_human=needs,
                        approved=False))
    return out


def apply(job_id: str, decisions: dict[str, str], labels: dict[str, str]) -> dict:
    """decisions: seam_id -> merge | resplit_clean | keep (human-approved)."""
    root = jobs.job_dir(job_id)
    review = json.loads((root / "split" / "review.json").read_text(encoding="utf-8"))
    seams = {s["id"]: s for s in review["evidence"]["seams"]}
    unknown = set(decisions) - set(seams)
    if unknown:
        raise ValueError(f"unknown seams: {sorted(unknown)}")
    final = {sid: decisions.get(sid, next(d["action"] for d in review["decisions"] if d["seam_id"] == sid)) for sid in seams}
    if any(v not in ("merge", "resplit_clean", "keep") for v in final.values()):
        raise ValueError("every seam needs merge / resplit_clean / keep")
    parts = load_reference(root)
    work = root / "split" / "blender"
    work.mkdir(parents=True, exist_ok=True)
    # merge groups (union-find over merge seams)
    parent = {n: n for n in parts}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for sid, act in final.items():
        if act == "merge":
            parent[find(seams[sid]["a"])] = find(seams[sid]["b"])
    groups: dict[str, list[str]] = {}
    for n in parts:
        groups.setdefault(find(n), []).append(n)
    ops, names = [], {}
    for n, m in parts.items():
        np.savez_compressed(work / f"ref-{n}.npz", vertices=np.asarray(m.vertices), faces=np.asarray(m.faces))
        ops.append(dict(op="load", name=n, file=f"ref-{n}.npz"))
    for i, (rootname, members) in enumerate(sorted(groups.items())):
        label = next((labels.get(m) for m in members if labels.get(m)), None) or review["part_labels"].get(members[0]) or "part"
        sem = meshio.safe_name(f"P{i + 1:02d}_{label}", set(names.values()))
        names[rootname] = sem
        if len(members) > 1:
            ops.append(dict(op="join", parts=sorted(members), into=sem))
        else:
            ops.append(dict(op="join", parts=members, into=sem))
    # clean re-splits between two (possibly merged) semantic parts: computed labels, executed by Blender
    merged_mesh = {}
    for sid, act in final.items():
        if act != "resplit_clean":
            continue
        a, b = names[find(seams[sid]["a"])], names[find(seams[sid]["b"])]
        if a == b:
            continue
        ma = merged_mesh[a] if a in merged_mesh else _group_mesh(parts, groups, names, a)
        mb = merged_mesh[b] if b in merged_mesh else _group_mesh(parts, groups, names, b)
        combined, lab, info = segmentation.clean_resplit(ma, mb)
        key = f"{a}__{b}"
        np.savez_compressed(work / f"{key}.npz", vertices=np.asarray(combined.vertices), faces=np.asarray(combined.faces))
        np.savez_compressed(work / f"{key}-labels.npz", labels=lab)
        ops += [dict(op="load", name=key, file=f"{key}.npz"),
                dict(op="separate", source=key, labels=f"{key}-labels.npz", names=[a + "__new", b + "__new"])]
        ops.append(dict(op="replace", old=[a, b], new=[a + "__new", b + "__new"]))
        merged_mesh[a], merged_mesh[b] = (combined.submesh([np.flatnonzero(lab == 0)], append=True),
                                          combined.submesh([np.flatnonzero(lab == 1)], append=True))
    final_names = sorted(set(names.values()))
    ops += [dict(op="save_npz", part=n, file=f"out-{n}.npz") for n in final_names]
    ops.append(dict(op="export", blend="split.blend", obj="split.obj"))
    jobs.progress(job_id, "split", 60, "Blender 분할 명령 실행")
    result = run_plan(work, ops, budget_s=settings.part_seconds * 2)
    seg = {}
    for n in final_names:
        with np.load(work / f"out-{n}.npz") as raw:
            seg[n] = trimesh.Trimesh(raw["vertices"], raw["faces"], process=False)
    manifest = meshio.save_parts(seg, root / "split" / "parts", dict(source_decisions=final))
    (root / "split" / "applied.json").write_text(json.dumps(dict(decisions=final, names=names, blender=result), indent=1,
                                                            ensure_ascii=False), encoding="utf-8")
    (root / "split" / "split-view.glb").write_bytes(meshio.export_glb(seg))
    record_labels(review, final)
    return dict(status="approved", summary=dict(parts_before=len(parts), parts_after=len(seg),
                                                merges=sum(v == "merge" for v in final.values()),
                                                resplits=sum(v == "resplit_clean" for v in final.values()),
                                                geometry_sha256=manifest["geometry_sha256"]))


def _group_mesh(parts, groups, names, sem):
    members = next(m for r, m in groups.items() if names[r] == sem)
    mesh = trimesh.util.concatenate([parts[m] for m in members])
    return meshio.weld(mesh)


def record_labels(review: dict, final: dict[str, str]) -> None:
    """Feedback loop: approved decisions become training labels; the judge is re-fitted (MAP on prior)."""
    d = MODEL_DIR()
    d.mkdir(parents=True, exist_ok=True)
    seams = {s["id"]: s for s in review["evidence"]["seams"]}
    with (d / "split_labels.jsonl").open("a", encoding="utf-8") as f:
        for sid, act in final.items():
            s = {k: v for k, v in seams[sid].items() if k != "judge"}
            f.write(json.dumps(dict(seam=s, label=int(act == "merge")), ensure_ascii=False) + "\n")
    rows = [json.loads(line) for line in (d / "split_labels.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(rows) >= 4 and len({r["label"] for r in rows}) == 2:
        model = segmentation.SplitJudge.fit([r["seam"] for r in rows], [r["label"] for r in rows])
        (d / "split_judge.json").write_text(json.dumps(model.params(), indent=1), encoding="utf-8")


def load_split(root: Path):
    return meshio.load_saved(root / "split" / "parts")
