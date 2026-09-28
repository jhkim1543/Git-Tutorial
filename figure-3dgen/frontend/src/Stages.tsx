import { useEffect, useMemo, useState } from "react";
import { api, downloadUrl, fileUrl, post } from "./api";
import type { Decision, Gate, Job, JointDoc, SplitReview } from "./types";
import Viewer from "./Viewer";

interface P { job: Job; refresh: () => void; onError: (m: string) => void }

const VIEWS = ["front", "right", "back", "left"] as const;
const VIEW_KO: Record<string, string> = { front: "정면", right: "우측", back: "후면", left: "좌측" };

function useJson<T>(job: Job, rel: string, dep: unknown): T | null {
  const [data, setData] = useState<T | null>(null);
  useEffect(() => {
    if (!job.available?.includes(rel)) { setData(null); return; }
    api<T>(`/jobs/${job.id}/files/${rel}`).then(setData).catch(() => setData(null));
  }, [job.id, rel, dep, job.available?.includes(rel)]);
  return data;
}

async function act(fn: () => Promise<unknown>, refresh: () => void, onError: (m: string) => void) {
  try { await fn(); refresh(); } catch (e) { onError((e as Error).message); }
}

export function Badge({ s }: { s: string }) {
  return <span className={`badge b-${s.toLowerCase()}`}>{s}</span>;
}

export function Gates({ gates }: { gates: Gate[] }) {
  return (
    <table className="grid">
      <thead><tr><th>게이트 (마우스를 올리면 기준)</th><th>상태</th><th>구분</th><th>PDF</th></tr></thead>
      <tbody>
        {gates.map((g) => (
          <tr key={g.id} title={g.rule}>
            <td><code>{g.id}</code></td><td><Badge s={g.status} /></td><td className="small">{g.blocking ? "차단" : "보고"}</td>
            <td className="small">{g.pages.length ? `p.${g.pages.join(",")}` : "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/* ------------------------------------------------------------------ 1. intake */
export function IntakePanel({ job }: P) {
  const rep = useJson<{ parts: { part: string; triangles: number; open_edges: number; nonmanifold_edges: number; closed: boolean }[]; role: Record<string, string> }>(job, "intake/report.json", job.stages.intake.updated);
  if (job.stages.intake.status !== "done") return <p className="muted">입력 분석 중…</p>;
  return (
    <div className="two">
      <div>
        <h3>원본 사진 <small className="muted">외형의 정답 기준</small></h3>
        <img className="photo" src={fileUrl(job.id, "intake/photo.png")} alt="원본 사진" />
        <p className="note">AI GLB는 <b>파트 분할 참고용</b>입니다. 외형은 사진에서 만든 승인 다시점을 따릅니다. 열린 면 GLB도 정상 입력입니다.</p>
      </div>
      <div>
        <h3>AI 레퍼런스 GLB <small className="muted">참고용 · 정답 아님</small></h3>
        <Viewer url={fileUrl(job.id, "intake/reference-view.glb")} />
        {rep && (
          <table className="grid"><thead><tr><th>파트</th><th>삼각면</th><th>열린 에지</th><th>비다양체</th><th>폐합</th></tr></thead>
            <tbody>{rep.parts.map((p) => (
              <tr key={p.part}><td>{p.part}</td><td>{p.triangles.toLocaleString()}</td><td>{p.open_edges}</td><td>{p.nonmanifold_edges}</td><td><Badge s={p.closed ? "PASS" : "OPEN"} /></td></tr>
            ))}</tbody></table>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ 2. views */
export function ViewsPanel({ job, refresh, onError }: P & { astra: boolean }) {
  const meta = (job.stages.views.views ?? {}) as Record<string, { source: string; approved: boolean }>;
  const [pick, setPick] = useState<string[]>(VIEWS.filter((v) => meta[v]));
  const bust = job.stages.views.updated;
  useEffect(() => setPick(VIEWS.filter((v) => meta[v])), [bust]);
  const upload = (v: string, f: File | null, usePhoto = false) => {
    const fd = new FormData();
    if (f) fd.append("file", f);
    fd.append("use_photo", usePhoto ? "true" : "false");
    act(() => post(`/jobs/${job.id}/views/${v}`, fd), refresh, onError);
  };
  return (
    <div>
      <p className="note">다시점 이미지는 <b>원본 사진</b>에서 만듭니다(Astra 이미지 생성 또는 직접 업로드). 승인한 실루엣이 Editable 재모델링의 외형 기준이 됩니다. GLB 렌더는 기준으로 쓰지 않습니다.</p>
      <div className="row">
        <button className="primary" onClick={() => act(() => post(`/jobs/${job.id}/views/generate`), refresh, onError)}>Astra로 다시점 생성</button>
        <button onClick={() => upload("front", null, true)}>원본 사진을 정면으로 사용</button>
      </div>
      <div className="views">
        {VIEWS.map((v) => (
          <div key={v} className="view-card">
            <div className="view-head"><b>{VIEW_KO[v]}</b>{meta[v] && <small className="muted">{meta[v].source}</small>}</div>
            {meta[v] ? <img src={fileUrl(job.id, `views/${v}.png`, bust)} alt={v} /> : <div className="empty">없음</div>}
            {meta[v]?.approved && <img className="mask" src={fileUrl(job.id, `views/${v}-mask.png`, bust)} alt="silhouette" />}
            <label className="file">업로드<input type="file" accept="image/*" onChange={(e) => upload(v, e.target.files?.[0] ?? null)} /></label>
            {meta[v] && <label><input type="checkbox" checked={pick.includes(v)} onChange={(e) => setPick(e.target.checked ? [...pick, v] : pick.filter((x) => x !== v))} /> 승인</label>}
          </div>
        ))}
      </div>
      <button className="primary" disabled={!pick.includes("front")} onClick={() => act(() => post(`/jobs/${job.id}/views/approve`, { views: pick }), refresh, onError)}>
        선택한 시점 승인 (정면 필수)
      </button>
    </div>
  );
}

/* ------------------------------------------------------------------ 3. split review */
export function SplitPanel({ job, refresh, onError }: P) {
  const st = job.stages.split;
  const review = useJson<SplitReview>(job, "split/review.json", st.updated);
  const [choice, setChoice] = useState<Record<string, string>>({});
  const [labels, setLabels] = useState<Record<string, string>>({});
  useEffect(() => { if (review) { setChoice({}); setLabels(review.part_labels ?? {}); } }, [review]);
  const seams = useMemo(() => Object.fromEntries((review?.evidence.seams ?? []).map((s) => [s.id, s])), [review]);
  return (
    <div>
      <p className="note">GLB의 분할은 가설입니다. 판정 모델(경계 연속성·포위도·파편도·경계 지그재그)과 Astra 시각 판단(사진 기준)을 비교해, 같은 망토·같은 눈이 여러 조각이면 <b>병합</b>, 다른 부위인데 경계가 지저분하면 <b>깨끗이 재분할</b>을 Blender에 명령합니다.</p>
      <div className="row">
        <button className="primary" onClick={() => act(() => post(`/jobs/${job.id}/split/analyze`), refresh, onError)}>분할 분석 실행</button>
        {review && <span className="muted">판정 모델: {review.judge.source} (학습 {review.judge.trained_on}건) · Astra: {review.astra.status}</span>}
      </div>
      {review && (
        <>
          {review.astra.result?.summary_ko && <p className="note">Astra: {review.astra.result.summary_ko}</p>}
          <div className="views">
            {VIEWS.map((v) => <img key={v} src={fileUrl(job.id, `split/renders/parts-${v}.png`, st.updated)} alt={v} />)}
          </div>
          <table className="grid">
            <thead><tr><th>경계</th><th>파트 A / B</th><th>길이 mm</th><th>꺾임°</th><th>지그재그</th><th>P(병합)</th><th>Astra</th><th>결정</th></tr></thead>
            <tbody>
              {review.decisions.map((d: Decision) => (
                <tr key={d.seam_id} className={d.needs_human ? "attn" : ""}>
                  <td><a href={fileUrl(job.id, `split/renders/${d.seam_id}.png`)} target="_blank" rel="noreferrer">{d.seam_id}</a></td>
                  <td>{d.a}<br />{d.b}</td>
                  <td>{seams[d.seam_id]?.seam_mm.toFixed(1)}</td>
                  <td>{seams[d.seam_id]?.dihedral_median_deg.toFixed(1)}</td>
                  <td>{seams[d.seam_id]?.jaggedness.toFixed(2)}</td>
                  <td>{d.judge.p_merge.toFixed(2)} <small>{d.judge.action}</small></td>
                  <td className="small">{d.astra ? `${d.astra.action} (${d.astra.confidence.toFixed(2)}) ${d.astra.reason_ko}` : "—"}</td>
                  <td>
                    <select value={choice[d.seam_id] ?? d.action} onChange={(e) => setChoice({ ...choice, [d.seam_id]: e.target.value })}>
                      <option value="merge">병합</option><option value="resplit_clean">깨끗이 재분할</option><option value="keep">유지</option>
                    </select>
                    {d.needs_human && <Badge s="REVIEW" />}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <h4>파트 의미 라벨</h4>
          <div className="labels">
            {review.evidence.parts.map((p) => (
              <label key={p.part}>{p.part}<input value={labels[p.part] ?? ""} placeholder="head, cape, eye…" onChange={(e) => setLabels({ ...labels, [p.part]: e.target.value })} /></label>
            ))}
          </div>
          <button className="primary" onClick={() => act(() => post(`/jobs/${job.id}/split/apply`, {
            decisions: Object.fromEntries(review.decisions.map((d) => [d.seam_id, choice[d.seam_id] ?? d.action])), labels,
          }), refresh, onError)}>승인 → Blender에 분할 명령 실행</button>
        </>
      )}
      {st.status === "approved" && (
        <>
          <h3>의미 분할 결과 <small className="muted">{String(st.summary?.parts_before)} → {String(st.summary?.parts_after)} 파트 · 병합 {String(st.summary?.merges)} · 재분할 {String(st.summary?.resplits)}</small></h3>
          <Viewer url={fileUrl(job.id, "split/split-view.glb", st.updated)} />
        </>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ 4. editable */
interface EditableReport {
  gates: Gate[]; blocking_pass: boolean; min_iou: number | null; closed_parts: number;
  parts: { part: string; closure_mode: string; retopology: string; self_intersection: string; blender: { quads: number; polygons: number; closed_manifold: boolean; shells: number; quad_ratio: number } }[];
  history: { pass_: number; silhouette: Record<string, { iou: number; excess_ratio: number; deficit_ratio: number }>; rejected?: { part: string }[] }[];
}
export function EditablePanel({ job, refresh, onError }: P) {
  const st = job.stages.editable;
  const rep = useJson<EditableReport>(job, "editable/report.json", st.updated);
  return (
    <div>
      <p className="note">각 의미 파트를 Blender에서 <b>새 닫힌 솔리드</b>로 재구성합니다(열린 판은 설계 두께의 쉘). 승인 다시점 실루엣과 비교해 초과 부분을 비주얼 헐로 보정하고, 보정 후에도 무결성이 깨지면 직전 통과본을 유지합니다. 모든 파트가 닫혀야 합니다.</p>
      <div className="row">
        <button className="primary" onClick={() => act(() => post(`/jobs/${job.id}/editable/build`), refresh, onError)}>Editable 재모델링</button>
        <button disabled={!rep?.blocking_pass || st.status !== "review"} onClick={() => act(() => post(`/jobs/${job.id}/editable/approve`), refresh, onError)}>Editable 승인 (몰더블 진행)</button>
        {rep && <a className="button" href={downloadUrl(job.id, "editable")}>BLEND·OBJ·GLB 받기</a>}
      </div>
      {rep && (
        <>
          <div className="two">
            <Viewer url={fileUrl(job.id, "editable/editable.glb", st.updated)} caption={`폐합 ${rep.closed_parts}/${rep.parts.length} · 최소 실루엣 IoU ${rep.min_iou ?? "—"}`} />
            <div>
              <Gates gates={rep.gates} />
            </div>
          </div>
          <table className="grid">
            <thead><tr><th>파트</th><th>폐합 방식</th><th>리토폴로지</th><th>쿼드</th><th>쿼드 비율</th><th>셸</th><th>닫힘</th><th>자기교차</th></tr></thead>
            <tbody>{rep.parts.map((p) => (
              <tr key={p.part}><td>{p.part}</td><td>{p.closure_mode}</td><td className="small">{p.retopology}</td><td>{p.blender.quads?.toLocaleString()}</td>
                <td>{p.blender.quad_ratio}</td><td>{p.blender.shells}</td><td><Badge s={p.blender.closed_manifold ? "PASS" : "FAIL"} /></td><td><Badge s={p.self_intersection} /></td></tr>
            ))}</tbody>
          </table>
          <h4>사진 실루엣 검증 루프 <small className="muted">회색 일치 · 빨강 모델 초과 · 파랑 사진 대비 부족</small></h4>
          {rep.history.map((h) => (
            <div key={h.pass_} className="pass">
              <b>{h.pass_}회차</b>
              <div className="views">
                {Object.entries(h.silhouette).map(([v, s]) => (
                  <figure key={v}><img src={fileUrl(job.id, `editable/silhouette/pass${h.pass_}-${v}.png`, st.updated)} alt={v} />
                    <figcaption>{VIEW_KO[v]} IoU {s.iou} · 초과 {s.excess_ratio} · 부족 {s.deficit_ratio}</figcaption></figure>
                ))}
              </div>
              {h.rejected?.length ? <p className="warn">보정 거부(무결성 실패, 직전본 유지): {h.rejected.map((r) => r.part).join(", ")}</p> : null}
            </div>
          ))}
        </>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ 5. joints */
export function JointsPanel({ job, refresh, onError }: P) {
  const st = job.stages.joints;
  const doc = useJson<JointDoc>(job, "joints/plan.json", st.updated);
  const [edits, setEdits] = useState<Record<string, { archetype?: string; swap?: boolean; merge?: boolean }>>({});
  useEffect(() => setEdits({}), [doc]);
  return (
    <div>
      <p className="note">승인된 Editable에서 접점 그래프를 만들고 모든 접점마다 <b>병합</b> 또는 <b>암수 1쌍</b>을 정합니다. Astra가 접점별 암수 개념도(이미지)와 구조화 사양을 만들고, Moldable은 승인된 사양을 따릅니다. 치수는 S3 정답 라이브러리({doc?.library_status ?? "—"}) 또는 표시된 가정값입니다.</p>
      <button className="primary" onClick={() => act(() => post(`/jobs/${job.id}/joints/plan`), refresh, onError)}>접점 분석 · 암수 계획 · 개념도 생성</button>
      {doc && (
        <>
          {doc.merges.length > 0 && <p className="note">몰더블 병합: {doc.merges.map((m) => `${m.parts.join(" + ")} → ${m.into}`).join(" · ")}</p>}
          <div className="joints">
            {doc.plans.map((p) => {
              const e = edits[p.interface_id] ?? {};
              const sheet = doc.sheets[p.interface_id];
              return (
                <div key={p.interface_id} className={`joint ${p.needs_human ? "attn" : ""}`}>
                  <div className="joint-imgs">
                    <img src={fileUrl(job.id, `joints/sheets/${p.interface_id}-context.png`, st.updated)} alt="context" />
                    {sheet?.status === "DONE" ? <img src={fileUrl(job.id, `joints/sheets/${sheet.image}`, st.updated)} alt="Astra concept" />
                      : <div className="empty">개념도 <Badge s={sheet?.status ?? "NOT_VERIFIED"} /><br /><small>{sheet?.reason}</small></div>}
                  </div>
                  <b>{p.interface_id}</b> 수 <code>{e.swap ? p.female : p.male}</code> → 암 <code>{e.swap ? p.male : p.female}</code>
                  <div className="small muted">{p.size_class} · 핀 r {p.radius_mm} · 길이 {p.length_mm} · 간극 {p.clearance_mm} · 벽 {p.wall_mm} · {p.parameter_source}</div>
                  <div className="row">
                    <select value={e.archetype ?? p.archetype} onChange={(ev) => setEdits({ ...edits, [p.interface_id]: { ...e, archetype: ev.target.value } })}>
                      <option value="round_pin">원형 핀</option><option value="d_key_pin">D-키 핀</option><option value="neck_plug">넥 플러그</option>
                    </select>
                    <label><input type="checkbox" checked={!!e.swap} onChange={(ev) => setEdits({ ...edits, [p.interface_id]: { ...e, swap: ev.target.checked } })} /> 암수 뒤집기</label>
                    <label><input type="checkbox" checked={!!e.merge} onChange={(ev) => setEdits({ ...edits, [p.interface_id]: { ...e, merge: ev.target.checked } })} /> 병합</label>
                  </div>
                </div>
              );
            })}
          </div>
          <button className="primary" onClick={() => act(() => post(`/jobs/${job.id}/joints/approve`, {
            plans: Object.entries(edits).map(([interface_id, e]) => ({ interface_id, ...e })),
          }), refresh, onError)}>암수 계획 승인</button>
        </>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ 6. moldable */
interface MoldReport {
  gates?: Gate[]; status: string; reason?: string; unplanned?: string[];
  joints?: { joints: { interface_id: string; status: string; plan: { male: string; female: string; archetype: string; radius_mm: number }; reasons?: string[]; evidence?: Record<string, unknown> }[] };
  insertion?: { interface_id: string; status: string; moving_group?: string[] }[];
  checks?: { parts: { part: string; thickness: { p05_mm?: number; min_mm?: number }; undercut: { undercut_ratio: number }; min_wall: string; sharp_points: string; undercut_gate: string }[]; stability: { status: string }; legal_line: { status: string; longest_flat_bottom_mm: number } };
  merges?: { into: string; members: string[] }[];
}
export function MoldablePanel({ job, refresh, onError }: P) {
  const st = job.stages.moldable;
  const rep = useJson<MoldReport>(job, "moldable/report.json", st.updated);
  const glb = job.available?.includes("moldable/report.json") && rep?.gates;
  return (
    <div>
      <p className="note">Moldable은 승인된 Editable 스냅샷(해시 일치)에서만 파생됩니다: 병합 → 겹침 해소(접촉 간극) → 본체 일체형 수 핀 + 간극 적용 암 소켓 → 간섭·서브어셈블리 삽입 경로 → PDF 조건(두께·언더컷·샤프포인트·자립·표기면) 검사.</p>
      <div className="row">
        <button className="primary" onClick={() => act(() => post(`/jobs/${job.id}/moldable/build`), refresh, onError)}>Moldable 생성 · 검증</button>
        {rep && <a className="button" href={downloadUrl(job.id, "moldable")}>BLEND·OBJ·STL·GLB 받기</a>}
      </div>
      {rep && !rep.gates && <p className="warn">차단: {rep.reason ?? rep.status} {rep.unplanned?.join(", ")}</p>}
      {glb && rep?.gates && (
        <div className="two">
          <Viewer url={fileUrl(job.id, "moldable/moldable.glb", st.updated)} caption="파랑 = 수 파트, 주황 = 암 파트 (양쪽인 파트는 기본색)" />
          <Gates gates={rep.gates} />
        </div>
      )}
      {rep?.joints && (
        <table className="grid"><thead><tr><th>접점</th><th>수 → 암</th><th>형식</th><th>상태</th><th>삽입</th><th>근거/사유</th></tr></thead>
          <tbody>{rep.joints.joints.map((j) => {
            const ins = rep.insertion?.find((s) => s.interface_id === j.interface_id);
            return (<tr key={j.interface_id}><td>{j.interface_id}</td><td>{j.plan.male} → {j.plan.female}</td><td>{j.plan.archetype} r{j.plan.radius_mm}</td>
              <td><Badge s={j.status === "built" ? "PASS" : "FAIL"} /></td><td>{ins ? <Badge s={ins.status} /> : "—"} <small>{ins?.moving_group?.join("+")}</small></td>
              <td className="small">{j.reasons?.join(" / ") ?? Object.entries(j.evidence ?? {}).map(([k, v]) => `${k}: ${v}`).join(" · ")}</td></tr>);
          })}</tbody></table>
      )}
      {rep?.checks && (
        <table className="grid"><thead><tr><th>파트</th><th>두께 p05</th><th>최소</th><th>언더컷 비율</th><th>최소벽</th><th>샤프포인트</th><th>언더컷</th></tr></thead>
          <tbody>{rep.checks.parts.map((p) => (
            <tr key={p.part}><td>{p.part}</td><td>{p.thickness.p05_mm}</td><td>{p.thickness.min_mm}</td><td>{p.undercut.undercut_ratio}</td>
              <td><Badge s={p.min_wall} /></td><td><Badge s={p.sharp_points} /></td><td><Badge s={p.undercut_gate} /></td></tr>
          ))}</tbody></table>
      )}
    </div>
  );
}
