"""Stage-1 tooling for the 50 ground-truth CAD samples (run on a machine with S3 + FreeForm access).

  download  --dest DIR            fetch the 50 manifest objects from s3://q10park-archive (your AWS profile)
  verify    --dir DIR             size + SHA-256 of every manifest row (50/50 required)
  scan      --dir DIR             .cly metadata strings only (no geometry, no joint inference)
  analyze   --exports DIR         DIR/<SampleId>/*.stl|ply|obj (FreeForm export, one file per part)
                                  -> reference/analysis/<SampleId>.json + reference/s3_50_analysis.csv
  library                         aggregate analysed samples -> contracts/joint_library.json

Credentials are read by boto3 from your normal AWS configuration; nothing is printed or stored.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
ROOT = HERE.parent

from app.reference import analyze as ra  # noqa: E402
from app.reference import cly  # noqa: E402

MANIFEST = ROOT / "reference" / "s3_50_manifest.csv"
ANALYSIS_CSV = ROOT / "reference" / "s3_50_analysis.csv"
RESULTS = ROOT / "reference" / "analysis"
BUCKET = "q10park-archive"          # backup; primary s3://dataset-dreamplastic needs IAM read access


def rows():
    return list(csv.DictReader(MANIFEST.open(encoding="utf-8-sig")))


def cmd_download(args):
    import boto3
    s3 = boto3.client("s3", region_name="us-east-1")
    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)
    for r in rows():
        target = dest / r["Filename"]
        if target.is_file() and target.stat().st_size == int(r["Bytes"]):
            continue
        print("get", r["S3Key"])
        s3.download_file(args.bucket, r["S3Key"], str(target))
    cmd_verify(argparse.Namespace(dir=args.dest))


def cmd_verify(args):
    res = cly.verify_manifest(MANIFEST, Path(args.dir))
    print(json.dumps({k: v for k, v in res.items() if k != "rows"}))
    for r in res["rows"]:
        if r["status"] != "PASS":
            print(" ", r)
    return 0 if res["passed"] == res["total"] else 1


def cmd_scan(args):
    out = [cly.scan_header(Path(args.dir) / r["Filename"]) for r in rows() if (Path(args.dir) / r["Filename"]).is_file()]
    (ROOT / "reference" / "cly_headers.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"scanned {len(out)} files -> reference/cly_headers.json (metadata only)")


def cmd_analyze(args):
    RESULTS.mkdir(parents=True, exist_ok=True)
    exports = Path(args.exports)
    table = list(csv.DictReader(ANALYSIS_CSV.open(encoding="utf-8-sig")))
    fields = list(table[0].keys())
    for row in table:
        folder = exports / row["SampleId"]
        if not folder.is_dir():
            continue
        parts = ra.load_assembly(folder, unit_scale=args.unit_scale)
        res = ra.analyze_assembly(parts)
        res["sample_id"] = row["SampleId"]
        res["source"] = dict(export_dir=str(folder.name), unit_scale=args.unit_scale, cly_sha256=row["SHA256"])
        (RESULTS / f"{row['SampleId']}.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
        pins = [j for j in res["joints"] if j["kind"] == "pin_socket"]
        row.update(PartCount=str(res["part_count"]), JointCount=str(len(pins)),
                   JointArchetypes=";".join(sorted({j["archetype"] for j in pins})),
                   MaleComponents=";".join(sorted({j["male"] for j in pins})),
                   FemaleComponents=";".join(sorted({j["female"] for j in pins})),
                   AssemblyDirection=";".join(str(j["withdraw_axis"]) for j in pins),
                   ClearanceEvidence=";".join(str(j["clearance_median_mm"]) for j in pins),
                   WallThicknessEvidence=";".join(str(j["socket_wall_p05_mm"]) for j in pins),
                   EvidenceImageOrExport=f"reference/analysis/{row['SampleId']}.json",
                   AnalysisStatus="OBSERVED_AUTO" if pins else "OBSERVED_NO_PIN_FOUND")
        print(row["SampleId"], row["PartCount"], "parts,", row["JointCount"], "pin/socket", row["JointArchetypes"])
    with ANALYSIS_CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, quoting=csv.QUOTE_ALL)
        w.writeheader()
        w.writerows(table)


def cmd_library(args):
    results = {p.stem: json.loads(p.read_text()) for p in sorted(RESULTS.glob("*.json"))}
    lib = ra.build_library(results, min_samples=args.min_samples)
    (ROOT / "contracts" / "joint_library.json").write_text(json.dumps(lib, indent=1), encoding="utf-8")
    print(json.dumps(dict(status=lib["status"], samples=lib["samples"], summary=lib["summary"]), indent=1))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("download"); d.add_argument("--dest", required=True); d.add_argument("--bucket", default=BUCKET)
    v = sub.add_parser("verify"); v.add_argument("--dir", required=True)
    s = sub.add_parser("scan"); s.add_argument("--dir", required=True)
    a = sub.add_parser("analyze"); a.add_argument("--exports", required=True)
    a.add_argument("--unit-scale", type=float, default=1.0, help="25.4 when the export is in inches")
    lb = sub.add_parser("library"); lb.add_argument("--min-samples", type=int, default=10)
    args = ap.parse_args()
    return {"download": cmd_download, "verify": cmd_verify, "scan": cmd_scan, "analyze": cmd_analyze,
            "library": cmd_library}[args.cmd](args) or 0


if __name__ == "__main__":
    sys.exit(main())
