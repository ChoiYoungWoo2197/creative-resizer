"""TYPE D outpaint 프롬프트.

프롬프트만 바꿀 때는 이 파일만 수정.
"""

# v3 2026-07-31: L/R 각각 구체적 지시 + 자연스러운 배너 합성 강조
OUTPAINT_PROMPT = (
    "Seamlessly reconstruct and extend the left and right outer background areas so the final image looks like one originally composed banner.\n\n"
    "Preserve all existing people, products, text, logos, and advertisement elements exactly.\n\n"
    "Do not treat the side areas as narrow pasted extensions. "
    "Instead, rebuild each side as a natural continuation of the surrounding original scene.\n\n"
    "For the left side: "
    "continue the soft bright photographic background and the flowing white fabric structure with smooth large-scale folds, highlights, shadows, and gentle gradients. "
    "Avoid triangular patches, hard seams, or obvious extension strips.\n\n"
    "For the right side: "
    "create a soft dark atmospheric continuation that blends naturally with the black ad panel. "
    "Use a clean, wide, subtle dark gradient or vignette-like continuation. "
    "Do not create a narrow gray vertical band, hard strip, repeated texture, or pasted-looking patch.\n\n"
    "Use the nearby boundary pixels as guidance and blend the generated result gradually into the original image. "
    "The transition must be wide, soft, and visually continuous, with no detectable border.\n\n"
    "Do not add any new text, logos, products, people, objects, or decorative elements. "
    "Only reconstruct and extend the background."
)
