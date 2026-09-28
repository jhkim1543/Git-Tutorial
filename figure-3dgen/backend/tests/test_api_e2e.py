"""HTTP end-to-end on the synthetic figure: upload photo + GLB -> views -> split -> editable -> joints -> moldable."""
import time

import pytest
from fastapi.testclient import TestClient

import synthetic
from app.config import settings

B = settings.base_path + "/api"


@pytest.fixture(scope="module")
def client(tmp_path_factory, monkeypatch_module):
    monkeypatch_module.setattr(settings, "data_dir", tmp_path_factory.mktemp("data"))
    monkeypatch_module.delenv("OPENAI_API_KEY", raising=False)
    from app.main import app
    return TestClient(app)


@pytest.fixture(scope="module")
def monkeypatch_module():
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


def wait(client, jid, stage, timeout=600):
    end = time.time() + timeout
    while time.time() < end:
        job = client.get(f"{B}/jobs/{jid}").json()
        st = job["stages"][stage]["status"]
        if st not in ("running", "pending"):
            return job
        time.sleep(0.5)
    raise TimeoutError(stage)


def test_health(client):
    h = client.get(f"{B}/health").json()
    assert h["ok"] and h["astra"] is False


def test_open_glb_is_accepted_and_astra_absence_is_blocked_not_faked(client):
    views = synthetic.view_pngs(384)
    r = client.post(f"{B}/jobs", files=dict(photo=("photo.png", views["front"], "image/png"),
                                            glb=("ai.glb", synthetic.ai_glb(), "model/gltf-binary")),
                    data=dict(height_mm="100", up_axis="Y", material="PVC_ROTO"))
    assert r.status_code == 200
    jid = r.json()["id"]
    job = wait(client, jid, "intake")
    assert job["stages"]["intake"]["status"] == "done"
    assert job["stages"]["intake"]["summary"]["closed"] == 0          # open input accepted
    assert client.post(f"{B}/jobs/{jid}/views/generate").status_code == 409
    # split before approved views is refused: the photo is the ground truth, not the GLB
    assert client.post(f"{B}/jobs/{jid}/split/analyze").status_code == 409
    for v, data in views.items():
        assert client.post(f"{B}/jobs/{jid}/views/{v}", files=dict(file=(f"{v}.png", data, "image/png"))).status_code == 200
    assert client.post(f"{B}/jobs/{jid}/views/approve", json=dict(views=list(views))).json()["stages"]["views"]["status"] == "approved"
    client.post(f"{B}/jobs/{jid}/split/analyze")
    job = wait(client, jid, "split")
    assert job["stages"]["split"]["status"] == "review"
    assert job["stages"]["split"]["summary"]["astra"].startswith("BLOCKED")
    review = client.get(f"{B}/jobs/{jid}/files/split/review.json").json()
    assert {d["action"] for d in review["decisions"]} == {"merge"}
    client.post(f"{B}/jobs/{jid}/split/apply", json=dict(decisions={}, labels={"mesh_head_0": "head", "mesh_body": "body"}))
    job = wait(client, jid, "split")
    assert job["stages"]["split"]["status"] == "approved" and job["stages"]["split"]["summary"]["parts_after"] == 4
    pytest.jid = jid


@pytest.mark.skipif(settings.blender_command() is None, reason="Blender / bpy not installed")
def test_editable_then_moldable(client):
    jid = pytest.jid
    assert client.post(f"{B}/jobs/{jid}/joints/plan").status_code == 409        # moldable path needs approved editable
    client.post(f"{B}/jobs/{jid}/editable/build")
    job = wait(client, jid, "editable", 900)
    rep = client.get(f"{B}/jobs/{jid}/files/editable/report.json").json()
    gates = {g["id"]: g["status"] for g in rep["gates"]}
    assert job["stages"]["editable"]["status"] == "review", gates
    assert gates["all_parts_closed"] == "PASS" and gates["photo_silhouette"] == "PASS"
    assert gates["zbrush_sculpt_roundtrip"] == "NOT_VERIFIED"
    assert client.post(f"{B}/jobs/{jid}/editable/approve").json()["stages"]["editable"]["status"] == "approved"
    client.post(f"{B}/jobs/{jid}/joints/plan")
    job = wait(client, jid, "joints")
    plan = client.get(f"{B}/jobs/{jid}/files/joints/plan.json").json()
    assert plan["plans"] and all(s["status"] == "BLOCKED" for s in plan["sheets"].values())
    client.post(f"{B}/jobs/{jid}/joints/approve", json=dict(plans=[]))
    client.post(f"{B}/jobs/{jid}/moldable/build")
    job = wait(client, jid, "moldable", 900)
    rep = client.get(f"{B}/jobs/{jid}/files/moldable/report.json").json()
    gates = {g["id"]: g["status"] for g in rep["gates"]}
    assert gates["derived_from_editable"] == "PASS" and gates["male_female_pairs"] == "PASS"
    assert gates["all_parts_closed"] == "PASS" and gates["insertion_path"] == "PASS"
    assert gates["process_and_physical_approval"] == "NOT_VERIFIED" and rep["release_approved"] is False
    z = client.get(f"{B}/jobs/{jid}/download/moldable.zip")
    assert z.status_code == 200 and len(z.content) > 1000
    assert client.get(f"{B}/jobs/{jid}/files/../../etc/passwd").status_code == 404
