"""Adapts a /generate request JSON payload → CleanPipelineRequest.

Only psdPath and specs are consumed.
resizeMode, smartFitStrength, focalPosition, psdMode are deliberately ignored.

Safe zone 우선순위 (높음 → 낮음):
  1. BannerSpec DB (slug 조회, parsed_text 지면만 수치 존재)
  2. 요청 payload의 safeZone / safeTop 등 명시 값
  3. 기본값 0
"""
from __future__ import annotations

import logging

from clean_pipeline.contracts import CleanPipelineRequest, TargetSpec

_log = logging.getLogger(__name__)


def adapt_request(data: dict, job_id: str, output_directory: str) -> CleanPipelineRequest:
    source_path = data.get("psdPath", "") or data.get("sourceFilePath", "")
    specs_raw = data.get("specs", [])

    target_specs: list[TargetSpec] = []
    for s in specs_raw:
        slug = s.get("slug", "")

        # 1순위: BannerSpec DB 조회
        db_safe = _lookup_db_safe(slug)

        # 2순위: 요청 payload 명시 값
        sz = s.get("safeZone") or {}
        req_top    = int(sz.get("top",    s.get("safeTop",    s.get("safe_top",    0))))
        req_right  = int(sz.get("right",  s.get("safeRight",  s.get("safe_right",  0))))
        req_bottom = int(sz.get("bottom", s.get("safeBottom", s.get("safe_bottom", 0))))
        req_left   = int(sz.get("left",   s.get("safeLeft",   s.get("safe_left",   0))))

        if db_safe:
            safe_top    = db_safe["safe_top"]
            safe_right  = db_safe["safe_right"]
            safe_bottom = db_safe["safe_bottom"]
            safe_left   = db_safe["safe_left"]
            _log.info(
                f"[ADAPT] jobId={job_id} slug={slug!r} safe zone from DB: "
                f"top={safe_top} right={safe_right} bottom={safe_bottom} left={safe_left}"
            )
        else:
            safe_top, safe_right, safe_bottom, safe_left = req_top, req_right, req_bottom, req_left
            if slug:
                _log.info(
                    f"[ADAPT] jobId={job_id} slug={slug!r} DB safe zone unavailable "
                    f"→ using request values: top={safe_top} right={safe_right} "
                    f"bottom={safe_bottom} left={safe_left}"
                )

        target_specs.append(TargetSpec(
            width=int(s.get("width", 0)),
            height=int(s.get("height", 0)),
            safe_top=safe_top,
            safe_right=safe_right,
            safe_bottom=safe_bottom,
            safe_left=safe_left,
            slug=slug,
        ))

    if not target_specs:
        target_specs = [TargetSpec(width=0, height=0)]

    pipeline_type = str(data.get("pipelineType", "")).upper()

    return CleanPipelineRequest(
        job_id=job_id,
        source_path=source_path,
        target_specs=target_specs,
        output_directory=output_directory,
        pipeline_type=pipeline_type,
    )


def _lookup_db_safe(slug: str) -> dict | None:
    """BannerSpec DB에서 slug로 safe zone을 조회. 실패 시 None."""
    if not slug:
        return None
    try:
        from clean_pipeline.banner_spec_lookup import lookup
        return lookup(slug)
    except Exception as exc:
        _log.warning(f"[ADAPT] banner_spec_lookup import/call failed: {exc}")
        return None
