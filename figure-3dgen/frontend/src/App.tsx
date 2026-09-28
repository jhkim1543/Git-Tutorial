import { useCallback, useEffect, useState } from "react";
import { api, post } from "./api";
import type { Health, Job, JobRow, StageName } from "./types";
import { Badge, EditablePanel, IntakePanel, JointsPanel, MoldablePanel, SplitPanel, ViewsPanel } from "./Stages";

const STAGES: { key: StageName; ko: string }[] = [
  { key: "intake", ko: "1 입력" }, { key: "views", ko: "2 사진 다시점" }, { key: "split", ko: "3 분할 검토" },
  { key: "editable", ko: "4 Editable" }, { key: "joints", ko: "5 암수 계획" }, { key: "moldable", ko: "6 Moldable" },
];

function NewJob({ onCreated, onError }: { onCreated: (id: string) => void; onError: (m: string) => void }) {
  const [photo, setPhoto] = useState<File | null>(null);
  const [glb, setGlb] = useState<File | null>(null);
  const [height, setHeight] = useState(100);
  const [up, setUp] = useState("Y");
  const [material, setMaterial] = useState("PVC_ROTO");
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    if (!photo || !glb) return;
    const fd = new FormData();
    fd.append("photo", photo); fd.append("glb", glb); fd.append("title", glb.name);
    fd.append("height_mm", String(height)); fd.append("up_axis", up); fd.append("material", material);
    setBusy(true);
    try { onCreated((await post<Job>("/jobs", fd)).id); } catch (e) { onError((e as Error).message); } finally { setBusy(false); }
  };
  return (
    <div className="card">
      <h3>새 작업</h3>
      <label>원본 사진 (필수 · 외형 기준)<input type="file" accept="image/*" onChange={(e) => setPhoto(e.target.files?.[0] ?? null)} /></label>
      <label>AI 생성 GLB (참고 · 파트 분할 가설)<input type="file" accept=".glb" onChange={(e) => setGlb(e.target.files?.[0] ?? null)} /></label>
      <div className="row">
        <label>전체 높이 mm<input type="number" min={20} max={600} value={height} onChange={(e) => setHeight(+e.target.value)} /></label>
        <label>위 축<select value={up} onChange={(e) => setUp(e.target.value)}><option>Y</option><option>Z</option></select></label>
      </div>
      <label>소재·공정<select value={material} onChange={(e) => setMaterial(e.target.value)}>
        <option value="PVC_ROTO">PVC ROTO</option><option value="PVC_INJECTION">PVC 사출</option><option value="ABS_INJECTION">ABS 사출</option>
      </select></label>
      <button className="primary" disabled={!photo || !glb || busy} onClick={submit}>{busy ? "업로드 중…" : "시작"}</button>
    </div>
  );
}

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [jobs, setJobs] = useState<JobRow[]>([]);
  const [jobId, setJobId] = useState<string | null>(new URLSearchParams(location.search).get("job"));
  const [job, setJob] = useState<Job | null>(null);
  const [tab, setTab] = useState<StageName>("intake");
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setJobs(await api<JobRow[]>("/jobs"));
      if (jobId) setJob(await api<Job>(`/jobs/${jobId}`));
    } catch (e) { setError((e as Error).message); }
  }, [jobId]);

  useEffect(() => { api<Health>("/health").then(setHealth).catch(() => setHealth(null)); }, []);
  useEffect(() => {
    refresh();
    const url = new URL(location.href);
    if (jobId) url.searchParams.set("job", jobId); else url.searchParams.delete("job");
    history.replaceState(null, "", url);
  }, [jobId, refresh]);
  const running = job && Object.values(job.stages).some((s) => s.status === "running" || s.status === "pending" && job.stages.intake.status !== "done");
  useEffect(() => {
    if (!running) return;
    const t = setInterval(refresh, 1500);
    return () => clearInterval(t);
  }, [running, refresh]);
  useEffect(() => {
    if (!job) return;
    const first = STAGES.find((s) => !["done", "approved"].includes(job.stages[s.key].status));
    setTab(first?.key ?? "moldable");
  }, [job?.id]);

  const panelProps = job ? { job, refresh, onError: setError } : null;
  return (
    <div className="app">
      <header>
        <strong>Figure 3DGen</strong><span className="muted">사진 기준 Editable → Moldable</span>
        <span className="spacer" />
        {health ? (<>
          <Badge s={health.blender ? "BLENDER" : "NO_BLENDER"} />
          <Badge s={health.astra ? "ASTRA" : "ASTRA_OFF"} />
          <small className="muted">v{health.version}</small>
        </>) : <Badge s="API_OFFLINE" />}
      </header>
      {error && <div className="error" onClick={() => setError(null)}>{error} <small>(닫기)</small></div>}
      <main>
        <aside>
          <NewJob onCreated={(id) => setJobId(id)} onError={setError} />
          <div className="card">
            <h3>작업 목록</h3>
            {jobs.map((j) => (
              <button key={j.id} className={`jobrow ${j.id === jobId ? "on" : ""}`} onClick={() => setJobId(j.id)}>
                <span>{j.title}</span>
                <span className="dots">{STAGES.map((s) => <i key={s.key} className={`dot d-${j.stages[s.key]}`} title={`${s.ko}: ${j.stages[s.key]}`} />)}</span>
              </button>
            ))}
            {!jobs.length && <p className="muted">아직 작업이 없습니다.</p>}
          </div>
        </aside>
        <section>
          {!job && (
            <div className="card intro">
              <h2>작업 흐름</h2>
              <ol>
                <li><b>입력</b> — 원본 사진(정답 기준)과 AI GLB(파트 분할 참고)를 함께 올립니다. 열린 면 GLB도 받습니다.</li>
                <li><b>사진 다시점</b> — 사진으로 정면·측면·후면 뷰를 만들고 승인합니다.</li>
                <li><b>분할 검토</b> — 판정 모델 + Astra가 잘못 나뉜 조각(같은 망토·같은 눈)과 지저분한 경계를 찾아 Blender에 병합/재분할을 명령합니다.</li>
                <li><b>Editable</b> — Blender가 모든 파트를 닫힌 쿼드 솔리드로 새로 만들고 사진 실루엣으로 검증·보정합니다.</li>
                <li><b>암수 계획</b> — 접점마다 병합 또는 암수 1쌍, Astra 개념도.</li>
                <li><b>Moldable</b> — Editable에 암수를 넣고 병합·몰더블 조건(PDF)을 검사합니다.</li>
              </ol>
              <p className="muted">모든 판정은 PASS / FAIL / NOT_VERIFIED / BLOCKED 로 표시되며, 사람 승인이 필요한 항목은 자동으로 통과되지 않습니다.</p>
            </div>
          )}
          {job && panelProps && (
            <>
              <div className="card">
                <div className="title-row"><h2>{job.title}</h2><small className="muted">{job.id} · {job.options.height_mm} mm · {job.options.material}</small></div>
                <nav className="stepper">
                  {STAGES.map((s) => (
                    <button key={s.key} className={`step s-${job.stages[s.key].status} ${tab === s.key ? "on" : ""}`} onClick={() => setTab(s.key)}>
                      {s.ko}<small>{job.stages[s.key].status}</small>
                    </button>
                  ))}
                </nav>
                {job.progress && Object.values(job.stages).some((s) => s.status === "running") && (
                  <div className="progress"><div style={{ width: `${job.progress.pct}%` }} /><span>{job.progress.message}</span></div>
                )}
                {job.stages[tab].status === "failed" && <p className="warn">실패: {String(job.stages[tab].error)}</p>}
              </div>
              <div className="card">
                {tab === "intake" && <IntakePanel {...panelProps} />}
                {tab === "views" && <ViewsPanel {...panelProps} astra={!!health?.astra} />}
                {tab === "split" && <SplitPanel {...panelProps} />}
                {tab === "editable" && <EditablePanel {...panelProps} />}
                {tab === "joints" && <JointsPanel {...panelProps} />}
                {tab === "moldable" && <MoldablePanel {...panelProps} />}
              </div>
              <details className="card"><summary>작업 기록</summary>
                <ul className="events">{[...job.events].reverse().map((e, i) => <li key={i} className={e.level}>{new Date(e.t * 1000).toLocaleTimeString()} {e.message}</li>)}</ul>
              </details>
            </>
          )}
        </section>
      </main>
    </div>
  );
}
