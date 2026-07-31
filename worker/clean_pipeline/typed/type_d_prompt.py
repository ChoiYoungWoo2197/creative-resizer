"""TYPE D outpaint 프롬프트.

프롬프트만 바꿀 때는 이 파일만 수정.

OUTPAINT_PROMPT        : 통합 단일 호출용 (SEQUENTIAL_LR_CALLS=false 시 사용)
OUTPAINT_PROMPT_LEFT   : 순차 처리 1단계 — 좌측 전용
OUTPAINT_PROMPT_RIGHT  : 순차 처리 2단계 — 우측 전용 (좌측 결과를 입력으로 받음)
"""

# v9 2026-07-31: 원본 보존 최우선 + 배경 확장 허용 방식
OUTPAINT_PROMPT = (
    "Edit the provided advertisement image directly.\n\n"
    "Target output size:\n"
    "- 1200 × 560 pixels\n\n"
    "Main goal:\n"
    "Adapt the original advertisement to 1200 × 560 while preserving the original ad content as much as possible.\n"
    "Reconstruct or extend only the necessary background and surrounding non-critical visual areas so the final result looks natural, seamless, and professionally designed at this wider size.\n\n"
    "Important editing principle:\n"
    "Preserve the original advertisement's key subject matter, layout, and design identity.\n"
    "Do not redesign or reinterpret the ad.\n"
    "Do not create a new advertisement.\n"
    "Do not replace the original model, product, or text.\n\n"
    "Preserve as much as possible:\n"
    "- the woman's face, hair, eyes, expression, skin, neck, shoulders, and pose\n"
    "- the blue cream on the cheek\n"
    "- the white satin fabric that the woman is leaning on\n"
    "- the cosmetic product jar\n"
    "- the product label and brand mark\n"
    "- all Korean text\n"
    "- the overall composition and visual balance\n"
    "- the premium skincare advertisement look and tone\n\n"
    "Allowed changes:\n"
    "- regenerate or extend only the background and surrounding support areas needed to fit the 1200 × 560 canvas\n"
    "- create natural continuation of the pale blue-white photographic background on the left\n"
    "- naturally continue the existing white satin fabric where needed\n"
    "- continue the black advertising panel on the right where needed\n"
    "- allow minimal transition repair near the boundary so the result looks seamless\n\n"
    "Left-side guidance:\n"
    "The left side is a bright skincare photo area.\n"
    "If the existing white satin fabric reaches the boundary, continue it naturally as part of the existing scene.\n"
    "The fabric should keep a soft, elegant, satin-like appearance with smooth folds, pearly highlights, and soft shadows.\n"
    "The upper-left area may continue the pale blue-white blurred background.\n"
    "The middle and lower left areas may continue the existing white satin fabric where visually appropriate.\n\n"
    "Right-side guidance:\n"
    "The right side is a black advertising panel.\n"
    "Extend or regenerate only the black panel background as needed.\n"
    "Do not add extra text, icons, logos, or decorative elements.\n\n"
    "Strict prohibitions:\n"
    "- do not generate a new person\n"
    "- do not alter the woman's identity or expression\n"
    "- do not replace the product\n"
    "- do not rewrite or restyle the Korean text\n"
    "- do not move the main subject, product, or text into a new layout\n"
    "- do not create obvious seams, vertical split lines, repeated edge patterns, stretched textures, or mirrored artifacts\n"
    "- do not turn the satin fabric area into plain empty background if the original image suggests the fabric continues\n\n"
    "Quality target:\n"
    "The final image should look like the same original advertisement, naturally completed at 1200 × 560 from the beginning.\n"
    "It should preserve the original ad as much as possible, while making the added or repaired background feel seamless and visually coherent."
)

# ── 순차 처리 좌측 전용 프롬프트 ─────────────────────────────────────────────
# v10 2026-07-31: re-imagine 자유도 + tangent 흐름 중심
OUTPAINT_PROMPT_LEFT = (
    "Naturally re-imagine and extend the bright satin fabric backdrop "
    "from the center towards the far-left edge. "
    "Smoothly connect the flow of the glossy cloth wrinkles, "
    "maintaining the natural arc and continuous tangent lines of the folds. "
    "Do not create any awkward cuts or pixel artifacts."
)

# ── 순차 처리 우측 전용 프롬프트 ─────────────────────────────────────────────
OUTPAINT_PROMPT_RIGHT = (
    "Edit only the right masked extension area of this advertisement image.\n\n"
    "The right side contains a clean solid black advertising panel.\n"
    "Continue only the existing black panel background into the new right area.\n\n"
    "Match the original black tone exactly.\n"
    "Match any subtle tonal depth or slight variation visible near the boundary.\n\n"
    "Strict prohibitions:\n"
    "- do not add text, Korean characters, products, logos, icons, or highlights\n"
    "- do not add decorative graphics or gradients\n"
    "- do not mirror, stretch, or duplicate edge pixels\n"
    "- do not modify anything outside the masked area"
)
