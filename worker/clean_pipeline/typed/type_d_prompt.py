"""TYPE D outpaint 프롬프트.

프롬프트만 바꿀 때는 이 파일만 수정.
"""

# v6 2026-07-31: 좌측 흰색 새틴 패브릭 연장 명시
OUTPAINT_PROMPT = (
    "This is a constrained side-extension task.\n\n"
    "Extend the existing visual content that reaches the left image boundary.\n"
    "Do not treat this as background-only generation.\n\n"
    "On the left side, the existing white satin fabric must continue naturally beyond the original canvas.\n"
    "The fabric is part of the existing scene and is explicitly allowed to be extended.\n\n"
    "Continue the fabric's:\n"
    "- outer contour\n"
    "- fold direction\n"
    "- curvature\n"
    "- pearly highlight\n"
    "- shadow softness\n"
    "- satin texture\n\n"
    "At the upper-left area, continue the pale blue-white photographic background.\n"
    "At the lower-left and middle-left area, continue the existing white satin fabric according to the shape visible at the source boundary.\n\n"
    "Do not replace the fabric with plain pale-blue background.\n"
    "Do not stop the fabric at the original vertical boundary.\n"
    "Do not create a new disconnected fabric shape.\n"
    "Do not mirror, stretch, smear, or duplicate edge pixels.\n\n"
    "The extension must look like the same fabric existed outside the original frame when the photograph was taken."
)
