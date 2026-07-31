"""TYPE D outpaint 프롬프트.

프롬프트만 바꿀 때는 이 파일만 수정.
"""

# v2 2026-07-31: 경계 자연스러움 강화
OUTPAINT_PROMPT = (
    "Seamlessly extend the background into the empty border areas.\n\n"
    "Preserve all existing people, products, text, logos, and advertisement elements exactly.\n\n"
    "Use the pixels and visual structure near each original image boundary as the source for continuation. "
    "Continue the existing color gradients, lighting direction, blur, fabric flow, folds, highlights, shadows, "
    "and background texture smoothly across the boundary.\n\n"
    "Blend the generated area gradually into the original image. "
    "Do not create a visible seam, straight vertical band, triangular patch, hard edge, pasted-looking region, "
    "mirrored texture, stretched pixels, or repeated pattern.\n\n"
    "The transition between the original image and the generated background must be soft and continuous, "
    "with no detectable border.\n\n"
    "Do not add any new text, logos, products, people, objects, or advertisement elements. "
    "Only reconstruct and extend the natural background."
)
