import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";

interface Props { url: string; caption?: string; highlight?: string[] }

interface Part { name: string; mesh: THREE.Mesh; home: THREE.Vector3; dir: THREE.Vector3; tris: number }

/** Part viewer: orbit, explode (display only — not an insertion proof), wireframe, click/isolate. */
export default function Viewer({ url, caption, highlight }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const state = useRef<{ parts: Part[]; render: () => void; size: number } | null>(null);
  const [parts, setParts] = useState<{ name: string; tris: number }[]>([]);
  const [explode, setExplode] = useState(0);
  const [wire, setWire] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [isolate, setIsolate] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const el = host.current!;
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    el.appendChild(renderer.domElement);
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(35, 1, 0.001, 100);
    scene.add(new THREE.HemisphereLight(0xffffff, 0x303040, 1.6));
    const key = new THREE.DirectionalLight(0xffffff, 1.6);
    key.position.set(1, 2, 2);
    scene.add(key);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    const render = () => renderer.render(scene, camera);
    const resize = () => {
      const w = el.clientWidth, h = el.clientHeight;
      renderer.setSize(w, h, false);
      camera.aspect = w / Math.max(h, 1);
      camera.updateProjectionMatrix();
    };
    const ro = new ResizeObserver(resize);
    ro.observe(el);
    resize();
    let raf = 0;
    const loop = () => { controls.update(); render(); raf = requestAnimationFrame(loop); };
    loop();
    setError(null);
    new GLTFLoader().load(url, (gltf) => {
      const root = gltf.scene;
      scene.add(root);
      const box = new THREE.Box3().setFromObject(root);
      const center = box.getCenter(new THREE.Vector3());
      const size = box.getSize(new THREE.Vector3()).length();
      const list: Part[] = [];
      root.traverse((o) => {
        const m = o as THREE.Mesh;
        if (!m.isMesh) return;
        m.material = new THREE.MeshStandardMaterial({
          color: (m.material as THREE.MeshStandardMaterial).color ?? new THREE.Color(0xcccccc),
          vertexColors: !!m.geometry.getAttribute("color"), roughness: 0.55, metalness: 0.05, flatShading: false,
        });
        m.geometry.computeVertexNormals();
        const c = new THREE.Box3().setFromObject(m).getCenter(new THREE.Vector3());
        const dir = c.clone().sub(center);
        if (dir.length() < size * 1e-3) dir.set(0, 1, 0);
        list.push({ name: m.name || o.parent?.name || "part", mesh: m, home: m.position.clone(), dir: dir.normalize(),
          tris: (m.geometry.index?.count ?? m.geometry.getAttribute("position").count) / 3 });
      });
      controls.target.copy(center);
      camera.position.copy(center.clone().add(new THREE.Vector3(0.35, 0.25, 1).normalize().multiplyScalar(size * 1.6)));
      camera.near = size / 200; camera.far = size * 20; camera.updateProjectionMatrix();
      state.current = { parts: list, render, size };
      setParts(list.map((p) => ({ name: p.name, tris: p.tris })));
    }, undefined, () => setError("모델을 불러오지 못했습니다"));
    const ray = new THREE.Raycaster();
    const click = (ev: MouseEvent) => {
      const r = renderer.domElement.getBoundingClientRect();
      const p = new THREE.Vector2(((ev.clientX - r.left) / r.width) * 2 - 1, -((ev.clientY - r.top) / r.height) * 2 + 1);
      ray.setFromCamera(p, camera);
      const hit = ray.intersectObjects(state.current?.parts.filter((x) => x.mesh.visible).map((x) => x.mesh) ?? [])[0];
      setSelected(hit ? (hit.object.name || hit.object.parent?.name || null) : null);
    };
    renderer.domElement.addEventListener("dblclick", click);
    return () => {
      cancelAnimationFrame(raf); ro.disconnect(); controls.dispose(); renderer.dispose();
      renderer.domElement.remove(); state.current = null;
    };
  }, [url]);

  useEffect(() => {
    const s = state.current;
    if (!s) return;
    for (const p of s.parts) {
      p.mesh.position.copy(p.home).addScaledVector(p.dir, explode * s.size * 0.25);
      const mat = p.mesh.material as THREE.MeshStandardMaterial;
      mat.wireframe = wire;
      const hot = p.name === selected || (highlight ?? []).includes(p.name);
      mat.emissive = new THREE.Color(hot ? 0x3346ff : 0x000000);
      mat.emissiveIntensity = hot ? 0.45 : 0;
      p.mesh.visible = !isolate || !selected || p.name === selected;
    }
  }, [explode, wire, selected, isolate, highlight, parts]);

  return (
    <div className="viewer">
      <div className="viewer-canvas" ref={host}>{error && <div className="viewer-error">{error}</div>}</div>
      <div className="viewer-bar">
        <label>분해 <input type="range" min={0} max={1} step={0.01} value={explode} onChange={(e) => setExplode(+e.target.value)} /></label>
        <label><input type="checkbox" checked={wire} onChange={(e) => setWire(e.target.checked)} /> 폴리곤</label>
        <label><input type="checkbox" checked={isolate} onChange={(e) => setIsolate(e.target.checked)} /> 선택만</label>
        <span className="muted">더블클릭으로 파트 선택 · 분해 표시는 삽입 검증이 아닙니다</span>
      </div>
      <div className="chips">
        {parts.map((p) => (
          <button key={p.name} className={`chip ${p.name === selected ? "on" : ""}`} onClick={() => setSelected(p.name === selected ? null : p.name)}>
            {p.name} <small>{p.tris.toLocaleString()}△</small>
          </button>
        ))}
      </div>
      {caption && <p className="muted small">{caption}</p>}
    </div>
  );
}
