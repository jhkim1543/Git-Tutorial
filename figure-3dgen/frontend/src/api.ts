// Same-origin API under the app base path (/3dgen/api). Never an absolute host, never a key.
export const API = `${import.meta.env.BASE_URL}api`;

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API}${path}`, init);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch { /* keep status text */ }
    throw new Error(`${res.status} ${detail}`);
  }
  return res.json() as Promise<T>;
}

export const post = <T>(path: string, body?: unknown) =>
  api<T>(path, body instanceof FormData ? { method: "POST", body } :
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) });

export const fileUrl = (jobId: string, rel: string, bust?: number) =>
  `${API}/jobs/${jobId}/files/${rel}${bust ? `?v=${bust}` : ""}`;

export const downloadUrl = (jobId: string, stage: "editable" | "moldable") => `${API}/jobs/${jobId}/download/${stage}.zip`;
