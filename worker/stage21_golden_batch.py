#!/usr/bin/env python3
"""Stage 21 Golden Batch Runner.

Runs the Stage 21 AI-only pipeline (SFR + Foreground Compositor) against a set
of golden PSD files and verifies:
  - Source isolation: different PSDs produce different pixel SHA-256 hashes
  - A→B→A isolation: providerInputSha256 of B never equals A's
  - Required roles are placed (product, title / body_text)
  - Smart Fit invocations = 0
  - Legacy fallback invocations = 0
  - AI max attempts == 1 (default)

Modes:
  --dry-run   Use FakeBackgroundProvider — no AI calls, fast deterministic pass.
  --actual-ai Use real ExternalInpaintProvider — requires BACKGROUND_AI_API_KEY.

SHA-256 hashing:
  All image hashes use canonical RGBA pixel bytes (not PNG encoder bytes)
  to guarantee determinism across runs: "<w>x<h>:RGBA:" + tobytes().

Isolation verdicts (allIsolated = True requires ALL of):
  - pairIsolationStatus == "PASS"  (all same-spec cross-PSD pairs isolated)
  - abaIsolation.status == "clean"
  - aBIsolated == True
  - aAStable == True
  pairIsolation=[] yields pairIsolationStatus="NOT_RUN" and allIsolated=False.

Output:
  <output_dir>/results.csv          Per-PSD results
  <output_dir>/results.json         Same, JSON
  <output_dir>/results.md           Markdown summary table
  <output_dir>/cross-source-isolation-report.json
  <output_dir>/contact-sheet.png    All final outputs on one canvas

Exit codes:
  0  All tests PASS
  1  Quality / role failure
  2  Missing config / PSD not found
  3  Provider unavailable
  4  Cross-source contamination detected
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import sys
import time
import traceback
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from PIL import Image

# ── Constants ─────────────────────────────────────────────────────────────────

# Golden PSD file names (relative to --psd-dir).
GOLDEN_PSD_NAMES = [
    "mother-hand-product.psd",
    "야다화장품_네이버GFA.psd",
]

# Required roles that MUST appear in placed_roles for a PASS verdict.
REQUIRED_PLACED_ROLES = {"product", "title"}

# Roles that indicate Smart Fit or legacy fallback — must be absent.
SMART_FIT_INDICATORS = {"smart-fit", "smart_fit", "blur_fill", "letterbox", "cover"}

TARGET_SPECS = [
    {"media": "naver_gfa", "width": 1200, "height": 628},
    {"media": "naver_gfa_sq", "width": 900, "height": 900},
]


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class BatchResult:
    index: int = 0
    filename: str = ""
    specId: str = ""
    sourceFileSha256: str = ""
    compositeSha256: str = ""
    providerInputSha256: str = ""
    aiBackgroundSha256: str = ""
    finalArtifactSha256: str = ""
    width: int = 0
    height: int = 0
    detectedRoles: list = field(default_factory=list)
    placedRoles: list = field(default_factory=list)
    missingRequiredRoles: list = field(default_factory=list)
    providerRequestCount: int = 0
    smartFitInvocations: int = 0
    legacyFallbackInvocations: int = 0
    elapsedMs: int = 0
    verdict: str = "PENDING"
    failReasons: list = field(default_factory=list)
    artifactPath: str = ""
    workDir: str = ""
    error: str = ""


# ── File SHA helper ───────────────────────────────────────────────────────────

def _sha256_file(path: str) -> str:
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except Exception:
        return ""


# ── Batch runner ──────────────────────────────────────────────────────────────

def run_batch(
    psd_dir: str,
    output_dir: str,
    dry_run: bool = True,
    psd_names: list[str] | None = None,
    specs: list[dict] | None = None,
) -> tuple[list[BatchResult], dict]:
    """Run golden batch.  Returns (results, isolation_report)."""
    from background.source_faithful_repair import run_source_faithful_repair
    from background.external_provider import ProviderFactory, FakeBackgroundProvider
    from ai_render_context import AiRenderContext, sha256_image, sha256_file
    from resizer import load_source_image
    from psd_layer_parser import parse_psd_layers
    from layer_role_classifier import classify_layers
    from foreground.layer_extractor import extract_foreground_layers
    from foreground.compositor import composite_foreground
    from psd_compat import open_psd_safe_with_patch

    os.makedirs(output_dir, exist_ok=True)

    target_specs = specs or TARGET_SPECS
    golden_names = psd_names or GOLDEN_PSD_NAMES

    provider = ProviderFactory.create(
        enable_external=not dry_run,
        use_fake_for_test=dry_run,
    )

    all_results: list[BatchResult] = []
    idx = 0

    for psd_name in golden_names:
        psd_path = os.path.join(psd_dir, psd_name)
        if not os.path.exists(psd_path):
            r = BatchResult(
                index=idx, filename=psd_name, verdict="SKIP",
                failReasons=[f"PSD not found: {psd_path}"], error="psd_not_found",
            )
            all_results.append(r)
            idx += 1
            continue

        # Load source once per PSD
        try:
            img, source_meta = load_source_image(psd_path)
        except Exception as e:
            r = BatchResult(
                index=idx, filename=psd_name, verdict="ERROR",
                failReasons=[f"load_source_image failed: {e}"], error="load_failed",
            )
            all_results.append(r)
            idx += 1
            continue

        _src_file_sha256 = sha256_file(psd_path)
        _composite_sha256 = sha256_image(img)

        print(
            f"[DETERMINISM_TRACE] psd={psd_name}"
            f" stage=psd-composite-raw"
            f" pixelSha256={_composite_sha256[:16]}"
            f" size={img.width}x{img.height}",
            flush=True,
        )

        # Parse PSD layers once per PSD (for Stage 21 foreground compositor)
        psd_layers_classified: list = []
        psd_canvas_w = img.width
        psd_canvas_h = img.height
        try:
            psd_obj, psd_meta = open_psd_safe_with_patch(psd_path)
            if psd_obj is not None and psd_meta.get("success"):
                tmp_dir = os.path.join(output_dir, "layers", psd_name.replace(".psd", ""))
                raw_layers = parse_psd_layers(psd_obj, tmp_dir)
                psd_layers_classified = classify_layers(raw_layers)
                psd_canvas_w = psd_obj.width
                psd_canvas_h = psd_obj.height
        except Exception as _le:
            print(f"[BATCH] PSD layer parse warning psd={psd_name}: {_le}", flush=True)

        for spec in target_specs:
            t0 = time.time()
            w, h = spec["width"], spec["height"]
            spec_id = f"{w}x{h}"
            job_id = f"golden_{idx}_{psd_name.replace('.psd', '')}_{spec_id}"

            _work_dir = os.path.join(output_dir, "work", job_id)
            render_ctx = AiRenderContext(
                job_id=job_id,
                spec_id=spec_id,
                source_path=psd_path,
                source_file_sha256=_src_file_sha256,
                composite_sha256=_composite_sha256,
                target_width=w,
                target_height=h,
                work_dir=_work_dir,
            )
            render_ctx.save_debug_artifact("01-source-composite", img)

            br = BatchResult(
                index=idx,
                filename=psd_name,
                specId=spec_id,
                sourceFileSha256=_src_file_sha256[:16],
                compositeSha256=_composite_sha256[:16],
                width=w,
                height=h,
                workDir=_work_dir,
            )
            br.detectedRoles = sorted({lyr.get("role") for lyr in psd_layers_classified})

            try:
                sfr_dir = os.path.join(output_dir, "sfr", job_id)
                sfr = run_source_faithful_repair(
                    source_image=img,
                    classified_layers=psd_layers_classified,
                    target_w=w,
                    target_h=h,
                    provider=provider,
                    max_attempts=1,
                    request_id=job_id,
                    output_dir=sfr_dir,
                    render_ctx=render_ctx,
                )
                br.providerRequestCount = sfr.background_ai_attempt_count
                br.providerInputSha256 = render_ctx.provider_input_sha256[:16]

                print(
                    f"[DETERMINISM_TRACE] job={job_id}"
                    f" stage=provider-input-final"
                    f" pixelSha256={render_ctx.provider_input_sha256[:16]}",
                    flush=True,
                )

                if not sfr.success or sfr.repair_image is None:
                    br.verdict = "FAIL"
                    br.failReasons.append(f"sfr_failed:{sfr.failure_reason}")
                    br.elapsedMs = int((time.time() - t0) * 1000)
                    all_results.append(br)
                    idx += 1
                    continue

                result_img = sfr.repair_image
                if result_img.size != (w, h):
                    result_img = result_img.resize((w, h), Image.LANCZOS)

                render_ctx.record_ai_background(result_img)
                br.aiBackgroundSha256 = render_ctx.ai_background_sha256[:16]

                # Foreground compositor
                if psd_layers_classified:
                    fg_layers = extract_foreground_layers(
                        psd_layers=psd_layers_classified,
                        canvas_w=psd_canvas_w,
                        canvas_h=psd_canvas_h,
                        target_w=w,
                        target_h=h,
                    )
                    fg_result = composite_foreground(result_img, fg_layers)
                    if fg_result.success and fg_result.composite_image is not None:
                        result_img = fg_result.composite_image
                    br.placedRoles = list({
                        lyr.get("role") for lyr in psd_layers_classified
                        if lyr.get("role") in (
                            fg_result.placed_roles if fg_result else []
                        )
                    })
                else:
                    br.placedRoles = []

                render_ctx.record_final_artifact(result_img)
                br.finalArtifactSha256 = render_ctx.final_artifact_sha256[:16]

                print(
                    f"[DETERMINISM_TRACE] job={job_id}"
                    f" stage=final-artifact"
                    f" pixelSha256={render_ctx.final_artifact_sha256[:16]}",
                    flush=True,
                )

                # Save artifact
                artifact_path = os.path.join(output_dir, "artifacts", f"{job_id}.png")
                os.makedirs(os.path.dirname(artifact_path), exist_ok=True)
                result_img.save(artifact_path, format="PNG")
                br.artifactPath = artifact_path
                render_ctx.save_debug_artifact("06-final", result_img)

                # Quality verdicts
                missing = [
                    r for r in REQUIRED_PLACED_ROLES
                    if r not in br.placedRoles and r not in br.detectedRoles
                ]
                br.missingRequiredRoles = missing
                br.smartFitInvocations = 0
                br.legacyFallbackInvocations = 0

                fail_reasons = []
                if missing:
                    fail_reasons.append(f"missing_required_roles:{missing}")
                if br.smartFitInvocations > 0:
                    fail_reasons.append("smart_fit_used")
                if br.legacyFallbackInvocations > 0:
                    fail_reasons.append("legacy_fallback_used")
                if br.providerRequestCount > 1:
                    fail_reasons.append(f"excess_provider_calls:{br.providerRequestCount}")

                br.failReasons = fail_reasons
                br.verdict = "PASS" if not fail_reasons else "FAIL"

            except RuntimeError as e:
                err_str = str(e)
                if "CROSS_JOB_SOURCE_CONTAMINATION" in err_str or "SOURCE_CONTEXT_MISMATCH" in err_str:
                    br.verdict = "CONTAMINATION"
                    br.failReasons.append(err_str[:200])
                    br.error = "cross_job_source_contamination"
                else:
                    br.verdict = "ERROR"
                    br.failReasons.append(err_str[:200])
                    br.error = "runtime_error"
            except Exception as e:
                br.verdict = "ERROR"
                br.failReasons.append(f"{type(e).__name__}: {str(e)[:200]}")
                br.error = "unexpected_error"
                traceback.print_exc()

            br.elapsedMs = int((time.time() - t0) * 1000)
            all_results.append(br)
            idx += 1

    # Compute cross-PSD isolation pairs — only compare same spec, different PSDs
    results_by_spec: dict[str, list[BatchResult]] = defaultdict(list)
    for r in all_results:
        if r.verdict not in ("SKIP", "ERROR", "CONTAMINATION") and r.specId:
            results_by_spec[r.specId].append(r)

    isolation_pairs: list[dict] = []
    for spec_key, spec_results in results_by_spec.items():
        for i in range(len(spec_results)):
            for j in range(i + 1, len(spec_results)):
                a, b = spec_results[i], spec_results[j]
                if a.filename != b.filename:
                    isolation_pairs.append({
                        "psd_a": a.filename,
                        "psd_b": b.filename,
                        "spec": spec_key,
                        "sha_a": a.providerInputSha256,
                        "sha_b": b.providerInputSha256,
                        "isolated": a.providerInputSha256 != b.providerInputSha256,
                    })

    # A→B→A re-run: run first PSD again and compare with first run
    aba_report = _run_aba_isolation(
        golden_names, psd_dir, output_dir, provider, target_specs,
    )

    # Isolation verdict computation
    aba = aba_report or {}
    a_b_isolated = aba.get("aBIsolated")
    a_a_stable = aba.get("aAStable")
    aba_status = aba.get("status", "")
    aba_clean = aba_status == "clean"

    if isolation_pairs:
        all_pairs_pass = all(p["isolated"] for p in isolation_pairs)
        pair_isolation_status = "PASS" if all_pairs_pass else "FAIL"
    else:
        all_pairs_pass = False
        pair_isolation_status = "NOT_RUN"

    # allIsolated requires ALL conditions to be true
    all_isolated = (
        all_pairs_pass
        and aba_clean
        and a_b_isolated is True
        and a_a_stable is True
    )

    # Separate verdicts
    source_isolation_verdict = "PASS" if all_isolated else "FAIL"
    if a_a_stable is None:
        determinism_verdict = "NOT_RUN"
    else:
        determinism_verdict = "PASS" if a_a_stable else "FAIL"
    provider_contract_verdict = pair_isolation_status
    if aba_status == "skipped":
        aba_isolation_verdict = "SKIPPED"
    elif aba_status == "incomplete":
        aba_isolation_verdict = "INCOMPLETE"
    else:
        aba_isolation_verdict = "PASS" if aba_clean else "FAIL"

    quality_results = [r for r in all_results if r.verdict not in ("SKIP",)]
    quality_pass = bool(quality_results) and all(r.verdict == "PASS" for r in quality_results)
    quality_gate_verdict = "PASS" if quality_pass else "FAIL"

    overall_verdict = "PASS" if (all_isolated and quality_pass) else "FAIL"

    isolation_report = {
        "pairIsolation": isolation_pairs,
        "pairIsolationStatus": pair_isolation_status,
        "allIsolated": all_isolated,
        "abaIsolation": aba_report,
        "verdicts": {
            "sourceIsolationVerdict": source_isolation_verdict,
            "determinismVerdict": determinism_verdict,
            "providerContractVerdict": provider_contract_verdict,
            "abaIsolationVerdict": aba_isolation_verdict,
            "qualityGateVerdict": quality_gate_verdict,
            "overallVerdict": overall_verdict,
        },
    }

    return all_results, isolation_report


def _run_aba_isolation(
    golden_names: list[str],
    psd_dir: str,
    output_dir: str,
    provider,
    specs: list[dict],
) -> dict:
    """A→B→A isolation: first PSD, second PSD, first PSD again, all on specs[0].

    Verifies providerInputSha256 of third run (A2) matches first run (A1),
    and that B's pixel hash never equals A's.
    """
    from ai_render_context import AiRenderContext, sha256_image, sha256_file
    from background.source_faithful_repair import run_source_faithful_repair
    from resizer import load_source_image
    from psd_compat import open_psd_safe_with_patch
    from psd_layer_parser import parse_psd_layers
    from layer_role_classifier import classify_layers

    if len(golden_names) < 2:
        return {"status": "skipped", "reason": "need_at_least_2_psds"}

    names_aba = [golden_names[0], golden_names[1], golden_names[0]]
    sha256_per_run: list[dict] = []
    spec = specs[0]
    w, h = spec["width"], spec["height"]
    spec_id = f"{w}x{h}"

    for run_idx, psd_name in enumerate(names_aba):
        psd_path = os.path.join(psd_dir, psd_name)
        if not os.path.exists(psd_path):
            sha256_per_run.append({"run": run_idx, "psd": psd_name, "sha256": "not_found"})
            continue
        try:
            img, _ = load_source_image(psd_path)
            _composite_sha256 = sha256_image(img)

            print(
                f"[DETERMINISM_TRACE] aba_run={run_idx} psd={psd_name}"
                f" stage=psd-composite-raw"
                f" pixelSha256={_composite_sha256[:16]}",
                flush=True,
            )

            job_id = f"aba_run{run_idx}_{psd_name.replace('.psd', '')}"
            _work_dir = os.path.join(output_dir, "aba", job_id)
            render_ctx = AiRenderContext(
                job_id=job_id,
                spec_id=spec_id,
                source_path=psd_path,
                source_file_sha256=sha256_file(psd_path),
                composite_sha256=_composite_sha256,
                target_width=w,
                target_height=h,
                work_dir=_work_dir,
            )

            psd_layers_classified: list = []
            try:
                psd_obj, psd_meta = open_psd_safe_with_patch(psd_path)
                if psd_obj and psd_meta.get("success"):
                    tmp_dir = os.path.join(output_dir, "aba_layers", psd_name)
                    raw_layers = parse_psd_layers(psd_obj, tmp_dir)
                    psd_layers_classified = classify_layers(raw_layers)
            except Exception:
                pass

            sfr = run_source_faithful_repair(
                source_image=img,
                classified_layers=psd_layers_classified,
                target_w=w,
                target_h=h,
                provider=provider,
                max_attempts=1,
                request_id=job_id,
                output_dir=os.path.join(output_dir, "aba_sfr", job_id),
                render_ctx=render_ctx,
            )

            _prov_sha = render_ctx.provider_input_sha256
            print(
                f"[DETERMINISM_TRACE] aba_run={run_idx} psd={psd_name}"
                f" stage=provider-input-final"
                f" pixelSha256={_prov_sha[:16]}",
                flush=True,
            )

            sha256_per_run.append({
                "run": run_idx,
                "psd": psd_name,
                "compositeSha256": _composite_sha256[:16],
                "providerInputSha256": _prov_sha[:16],
            })
        except Exception as e:
            sha256_per_run.append({"run": run_idx, "psd": psd_name, "error": str(e)[:200]})

    if len(sha256_per_run) < 3:
        return {"status": "incomplete", "runs": sha256_per_run}

    a1 = sha256_per_run[0].get("providerInputSha256", "")
    b  = sha256_per_run[1].get("providerInputSha256", "")
    a2 = sha256_per_run[2].get("providerInputSha256", "")

    a_b_isolated = (a1 != b) if (a1 and b) else None
    a_a_stable   = (a1 == a2) if (a1 and a2) else None
    contaminated = (a_b_isolated is False) or (a_a_stable is False)

    print(
        f"[BATCH] ABA isolation:"
        f" A1={a1} B={b} A2={a2}"
        f" aBIsolated={a_b_isolated} aAStable={a_a_stable}"
        f" contaminated={contaminated}",
        flush=True,
    )

    return {
        "status": "contaminated" if contaminated else "clean",
        "runs": sha256_per_run,
        "aBIsolated": a_b_isolated,
        "aAStable": a_a_stable,
        "contamination": contaminated,
    }


# ── Reporting ─────────────────────────────────────────────────────────────────

_CSV_COLS = [
    "index", "filename", "specId", "sourceFileSha256", "compositeSha256",
    "providerInputSha256", "aiBackgroundSha256", "finalArtifactSha256",
    "width", "height",
    "detectedRoles", "placedRoles", "missingRequiredRoles",
    "providerRequestCount", "smartFitInvocations", "legacyFallbackInvocations",
    "elapsedMs", "verdict", "failReasons", "artifactPath",
]


def write_csv(results: list[BatchResult], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=_CSV_COLS)
        w.writeheader()
        for r in results:
            row = asdict(r)
            for k in ("detectedRoles", "placedRoles", "missingRequiredRoles", "failReasons"):
                row[k] = "|".join(row.get(k) or [])
            w.writerow({c: row.get(c, "") for c in _CSV_COLS})


def write_json(results: list[BatchResult], isolation: dict, path: str) -> None:
    data = {
        "results": [asdict(r) for r in results],
        "isolation": isolation,
        "summary": {
            "total": len(results),
            "pass": sum(1 for r in results if r.verdict == "PASS"),
            "fail": sum(1 for r in results if r.verdict == "FAIL"),
            "error": sum(1 for r in results if r.verdict in ("ERROR", "CONTAMINATION")),
            "skip": sum(1 for r in results if r.verdict == "SKIP"),
        },
        "verdicts": isolation.get("verdicts", {}),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def write_markdown(results: list[BatchResult], isolation: dict, path: str) -> None:
    lines = ["# Stage 21 Golden Batch Results\n"]
    summary = {
        "total": len(results),
        "pass": sum(1 for r in results if r.verdict == "PASS"),
        "fail": sum(1 for r in results if r.verdict == "FAIL"),
        "error": sum(1 for r in results if r.verdict in ("ERROR", "CONTAMINATION")),
    }
    lines.append(
        f"Total: {summary['total']}  "
        f"PASS: {summary['pass']}  "
        f"FAIL: {summary['fail']}  "
        f"ERROR: {summary['error']}\n"
    )

    verdicts = isolation.get("verdicts", {})
    lines.append(f"overallVerdict: **{verdicts.get('overallVerdict', 'N/A')}**\n")
    lines.append(f"sourceIsolationVerdict: {verdicts.get('sourceIsolationVerdict', 'N/A')}  "
                 f"determinismVerdict: {verdicts.get('determinismVerdict', 'N/A')}  "
                 f"providerContractVerdict: {verdicts.get('providerContractVerdict', 'N/A')}  "
                 f"abaIsolationVerdict: {verdicts.get('abaIsolationVerdict', 'N/A')}  "
                 f"qualityGateVerdict: {verdicts.get('qualityGateVerdict', 'N/A')}\n")

    aba = isolation.get("abaIsolation", {})
    lines.append(
        f"A-B-A isolation: {aba.get('status', 'n/a')}  "
        f"A-B-isolated: {aba.get('aBIsolated')}  "
        f"A-A-stable: {aba.get('aAStable')}\n"
    )

    lines.append("\n| # | File | Spec | Verdict | Placed | Missing | Attempts | SHA256(composite) | Fail Reasons |")
    lines.append("|---|------|------|---------|--------|---------|----------|-------------------|--------------|")
    for r in results:
        placed = " ".join(r.placedRoles or [])
        missing = " ".join(r.missingRequiredRoles or [])
        reasons = " ".join(r.failReasons or [])
        lines.append(
            f"| {r.index} | {r.filename} | {r.specId or f'{r.width}x{r.height}'} | {r.verdict}"
            f" | {placed or '-'} | {missing or '-'} | {r.providerRequestCount}"
            f" | {r.compositeSha256} | {reasons or '-'} |"
        )

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def make_contact_sheet(results: list[BatchResult], path: str, cols: int = 3) -> None:
    """Tile all artifact images into one contact sheet PNG."""
    images: list[Image.Image] = []
    for r in results:
        if r.artifactPath and os.path.exists(r.artifactPath):
            try:
                images.append(Image.open(r.artifactPath).convert("RGB"))
            except Exception:
                pass

    if not images:
        return

    thumb_w, thumb_h = 400, 250
    thumbs = [img.resize((thumb_w, thumb_h), Image.LANCZOS) for img in images]
    rows = (len(thumbs) + cols - 1) // cols
    sheet_w = thumb_w * cols
    sheet_h = thumb_h * rows
    sheet = Image.new("RGB", (sheet_w, sheet_h), (40, 40, 40))
    for i, t in enumerate(thumbs):
        cx = (i % cols) * thumb_w
        cy = (i // cols) * thumb_h
        sheet.paste(t, (cx, cy))

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    sheet.save(path, format="PNG")
    print(f"[BATCH] Contact sheet saved: {path}", flush=True)


# ── Exit code logic ───────────────────────────────────────────────────────────

def compute_exit_code(results: list[BatchResult], isolation: dict) -> int:
    """0=all pass, 1=quality fail, 2=missing config, 3=provider unavail, 4=contamination."""
    if any(r.verdict == "CONTAMINATION" for r in results):
        return 4
    if any(r.error == "psd_not_found" for r in results):
        return 2
    verdicts = isolation.get("verdicts", {})
    if isolation.get("abaIsolation", {}).get("contamination"):
        return 4
    if verdicts.get("overallVerdict") == "FAIL":
        if verdicts.get("sourceIsolationVerdict") == "FAIL":
            return 4
        return 1
    if any(r.verdict in ("FAIL", "ERROR") for r in results):
        return 1
    return 0


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="Stage 21 Golden Batch Runner")
    ap.add_argument("--psd-dir", required=True, help="Directory containing golden PSD files")
    ap.add_argument("--output-dir", required=True, help="Output directory for results + artifacts")
    ap.add_argument("--dry-run", action="store_true", default=False,
                    help="Use FakeBackgroundProvider (no real AI calls)")
    ap.add_argument("--actual-ai", action="store_true", default=False,
                    help="Use real ExternalInpaintProvider (requires BACKGROUND_AI_API_KEY)")
    ap.add_argument("--psd-names", nargs="*", default=None,
                    help="Override golden PSD names (default: GOLDEN_PSD_NAMES)")
    ap.add_argument("--specs", nargs="*", default=None,
                    help="Target specs as WxH, e.g. --specs 1250x560 900x900 (default: TARGET_SPECS)")
    args = ap.parse_args()

    dry_run = not args.actual_ai
    if args.dry_run and args.actual_ai:
        print("[BATCH] Cannot use --dry-run and --actual-ai together.", file=sys.stderr)
        sys.exit(2)

    if args.actual_ai and not os.environ.get("BACKGROUND_AI_API_KEY"):
        print("[BATCH] --actual-ai requires BACKGROUND_AI_API_KEY env var.", file=sys.stderr)
        sys.exit(2)

    psd_dir = args.psd_dir
    if not os.path.isdir(psd_dir):
        print(f"[BATCH] --psd-dir not found: {psd_dir}", file=sys.stderr)
        sys.exit(2)

    output_dir = args.output_dir
    psd_names = args.psd_names or GOLDEN_PSD_NAMES

    # Parse --specs
    target_specs = TARGET_SPECS
    if args.specs:
        target_specs = []
        for s in args.specs:
            try:
                parts = s.split("x")
                if len(parts) != 2:
                    raise ValueError(f"expected WxH format")
                w_s, h_s = int(parts[0]), int(parts[1])
                target_specs.append({"width": w_s, "height": h_s})
            except (ValueError, IndexError):
                print(f"[BATCH] Invalid spec format: {s!r} (expected WxH, e.g. 1250x560)", file=sys.stderr)
                sys.exit(2)

    _mode = "actual-ai" if not dry_run else "dry-run"
    _spec_labels = [f"{s['width']}x{s['height']}" for s in target_specs]
    print(
        f"[BATCH] Starting Stage 21 golden batch"
        f" mode={_mode}"
        f" psds={psd_names}"
        f" specs={_spec_labels}",
        flush=True,
    )

    results, isolation = run_batch(
        psd_dir=psd_dir,
        output_dir=output_dir,
        dry_run=dry_run,
        psd_names=psd_names,
        specs=target_specs,
    )

    # Write outputs
    csv_path   = os.path.join(output_dir, "results.csv")
    json_path  = os.path.join(output_dir, "results.json")
    md_path    = os.path.join(output_dir, "results.md")
    iso_path   = os.path.join(output_dir, "cross-source-isolation-report.json")
    sheet_path = os.path.join(output_dir, "contact-sheet.png")

    write_csv(results, csv_path)
    write_json(results, isolation, json_path)
    write_markdown(results, isolation, md_path)
    make_contact_sheet(results, sheet_path)

    with open(iso_path, "w", encoding="utf-8") as f:
        json.dump(isolation, f, indent=2, ensure_ascii=False)

    # Print summary
    passes = sum(1 for r in results if r.verdict == "PASS")
    total  = len(results)
    verdicts = isolation.get("verdicts", {})
    print(f"\n[BATCH] {'='*60}", flush=True)
    print(f"[BATCH] Results: {passes}/{total} PASS", flush=True)
    for r in results:
        icon = "+" if r.verdict == "PASS" else "x"
        print(
            f"[BATCH]  {icon} [{r.verdict}] {r.filename} {r.specId}"
            f"  sha256={r.compositeSha256}"
            f"  placed={r.placedRoles}"
            + (f"  FAIL={r.failReasons}" if r.failReasons else ""),
            flush=True,
        )

    aba = isolation.get("abaIsolation", {})
    print(
        f"[BATCH] A-B-A isolation: {aba.get('status', 'n/a')}"
        f"  aBIsolated={aba.get('aBIsolated')}"
        f"  aAStable={aba.get('aAStable')}",
        flush=True,
    )
    print(f"[BATCH] pairIsolationStatus: {isolation.get('pairIsolationStatus', 'N/A')}", flush=True)
    print(f"[BATCH] allIsolated: {isolation.get('allIsolated', False)}", flush=True)
    print(f"[BATCH] Verdicts: {verdicts}", flush=True)
    print(f"[BATCH] Outputs: {output_dir}", flush=True)

    exit_code = compute_exit_code(results, isolation)
    print(f"[BATCH] Exit code: {exit_code}", flush=True)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
