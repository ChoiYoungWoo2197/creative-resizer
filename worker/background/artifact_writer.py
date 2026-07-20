"""Stage 19 Artifact Writer.

Saves debug artifacts to the specified output directory.
Sensitive data (API keys, auth headers) is never saved.

artifact_level:
  "none"     — no artifacts
  "standard" — key masks + selected result + report
  "full"     — all candidates + all masks + seam debug
"""
from __future__ import annotations

import json
import os
import time
from PIL import Image


def _save_image(img: Image.Image | None, path: str) -> bool:
    if img is None:
        return False
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        img.convert("RGB").save(path)
        return True
    except Exception:
        return False


def _save_json(data: dict, path: str) -> bool:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
        return True
    except Exception:
        return False


def write_artifacts(
    output_dir: str,
    artifact_level: str,
    source_image: Image.Image | None = None,
    masks: dict | None = None,
    candidates: list | None = None,
    selected_candidate=None,
    metrics: dict | None = None,
    warnings: list[str] | None = None,
    request_id: str = "",
    elapsed_ms: int = 0,
) -> list[str]:
    """Write Stage 19 debug artifacts.

    Returns list of saved file paths.
    API keys, full HTTP headers, and sensitive config are never saved.
    """
    if artifact_level == "none":
        return []

    os.makedirs(output_dir, exist_ok=True)
    saved: list[str] = []
    ts = int(time.time())

    def _path(name: str) -> str:
        return os.path.join(output_dir, name)

    # ── source ────────────────────────────────────────────────────────────────
    if source_image is not None:
        if _save_image(source_image, _path("source.png")):
            saved.append("source.png")

    # ── masks ─────────────────────────────────────────────────────────────────
    if masks:
        mask_map = {
            "protected-mask.png":          masks.get("protected_mask"),
            "removal-mask.png":            masks.get("removal_mask"),
            "outpaint-mask.png":           masks.get("outpaint_mask"),
            "generation-allowed-mask.png": masks.get("generation_allowed_mask"),
            "generation-blocked-mask.png": masks.get("generation_blocked_mask"),
            "product-mask.png":            masks.get("product_mask"),
        }
        for fname, mimg in mask_map.items():
            if mimg is not None and _save_image(mimg, _path(fname)):
                saved.append(fname)

    # ── selected result ───────────────────────────────────────────────────────
    if selected_candidate is not None and selected_candidate.image is not None:
        if _save_image(selected_candidate.image, _path("background.selected.png")):
            saved.append("background.selected.png")

    # ── candidates (full level) ───────────────────────────────────────────────
    if artifact_level == "full" and candidates:
        for c in candidates:
            if c.image is not None:
                fname = f"candidate-{c.candidate_id}.png"
                if _save_image(c.image, _path(fname)):
                    saved.append(fname)

    # ── JSON reports ─────────────────────────────────────────────────────────
    candidates_data = []
    if candidates:
        for c in candidates:
            candidates_data.append({
                "candidateId":      c.candidate_id,
                "provider":         c.provider,
                "method":           c.method,
                "score":            c.score,
                "accepted":         c.accepted,
                "rejectionReasons": c.rejection_reasons,
                "naturalness":      c.naturalness_score,
                "seamScore":        c.seam_score,
                "seamRisk":         c.seam_risk,
                "blurBandRisk":     c.blur_band_risk,
                "repetitionRisk":   c.repetition_risk,
                "ghostingRisk":     c.ghosting_risk,
                "shadowApplied":    c.shadow_applied,
                "shadowOpacity":    c.shadow_opacity,
                "maskAreaRatio":    c.mask_area_ratio,
                "elapsedMs":        c.elapsed_ms,
                "extras":           {
                    k: v for k, v in c.extras.items()
                    if not any(s in k.lower() for s in ("key", "token", "secret", "auth"))
                },
            })

    if _save_json(candidates_data, _path("background-candidates.json")):
        saved.append("background-candidates.json")

    report = {
        "requestId":        request_id,
        "timestamp":        ts,
        "elapsedMs":        elapsed_ms,
        "selectedMethod":   selected_candidate.method   if selected_candidate else "native",
        "selectedProvider": selected_candidate.provider if selected_candidate else "native",
        "selectedScore":    selected_candidate.score    if selected_candidate else 0.0,
        "metrics":          metrics or {},
        "warnings":         warnings or [],
        "candidateCount":   len(candidates) if candidates else 0,
    }
    if _save_json(report, _path("stage19-report.json")):
        saved.append("stage19-report.json")

    # ── markdown report ───────────────────────────────────────────────────────
    md_lines = [
        "# Stage 19 Background Pipeline Report\n",
        f"- **requestId**: {request_id}",
        f"- **elapsed**: {elapsed_ms}ms",
        f"- **selectedMethod**: {report['selectedMethod']}",
        f"- **selectedScore**: {report['selectedScore']}",
        "",
        "## Candidates\n",
        "| id | provider | method | score | accepted | rejection |",
        "|---|---|---|---|---|---|",
    ]
    for c in candidates_data:
        reasons = ", ".join(c["rejectionReasons"][:2]) or "—"
        md_lines.append(
            f"| {c['candidateId']} | {c['provider']} | {c['method']} "
            f"| {c['score']} | {c['accepted']} | {reasons} |"
        )
    md_lines += ["", "## Warnings", ""]
    for w in (warnings or []):
        md_lines.append(f"- {w}")

    md_path = _path("stage19-report.md")
    try:
        os.makedirs(os.path.dirname(md_path), exist_ok=True)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write("\n".join(md_lines) + "\n")
        saved.append("stage19-report.md")
    except Exception:
        pass

    return saved
