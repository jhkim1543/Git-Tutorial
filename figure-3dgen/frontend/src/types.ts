export type StageName = "intake" | "views" | "split" | "editable" | "joints" | "moldable";
export type StageStatus = "pending" | "running" | "review" | "approved" | "done" | "blocked" | "failed";
export type GateStatus = "PASS" | "FAIL" | "NOT_VERIFIED" | "BLOCKED";

export interface Stage {
  status: StageStatus;
  summary?: Record<string, unknown>;
  error?: string | null;
  updated?: number;
  [k: string]: unknown;
}

export interface Job {
  id: string;
  title: string;
  created: number;
  options: { height_mm: number; up_axis: string; material: string; photo_name?: string; glb_name?: string };
  stages: Record<StageName, Stage>;
  progress: { stage: StageName; pct: number; message: string } | null;
  events: { t: number; level: string; message: string }[];
  available?: string[];
}

export interface JobRow { id: string; title: string; created: number; stages: Record<StageName, StageStatus> }

export interface Health { ok: boolean; version: string; blender: boolean; astra: boolean; astra_model: string }

export interface Gate { id: string; status: GateStatus; blocking: boolean; pages: number[]; rule: string }

export interface Seam {
  id: string; a: string; b: string; seam_mm: number; share_a: number; share_b: number;
  dihedral_median_deg: number; jaggedness: number; smaller_area_share: number;
}
export interface Decision {
  seam_id: string; a: string; b: string; action: string; needs_human: boolean;
  judge: { action: string; p_merge: number; features: Record<string, number> };
  astra?: { action: string; confidence: number; reason_ko: string; semantic_label: string } | null;
}
export interface SplitReview {
  evidence: { seams: Seam[]; parts: { part: string; triangles: number; area_share: number; shells: number }[] };
  decisions: Decision[];
  part_labels: Record<string, string>;
  astra: { status: string; result?: { summary_ko: string } };
  judge: { source: string; trained_on: number };
}

export interface JointPlan {
  interface_id: string; male: string; female: string; archetype: string; size_class: string;
  radius_mm: number; length_mm: number; clearance_mm: number; wall_mm: number; parameter_source: string;
  axis: number[]; needs_human?: boolean;
}
export interface JointDoc {
  interfaces: { id: string; a: string; b: string; area_mm2: number; diameter_mm: number }[];
  merges: { parts: string[]; into: string; reason: string; source?: string }[];
  plans: JointPlan[];
  sheets: Record<string, { status: string; image?: string; reason?: string; spec?: Record<string, unknown> }>;
  library_status: string;
}
