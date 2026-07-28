"""Prompts used for OpenAI image-edit API calls in the scene pipeline."""

CLEANUP_PROMPT = (
    "This image contains advertising elements in the marked transparent area "
    "(products, logos, promotional text, CTA buttons, badges). "
    "Remove all advertising elements from the marked area and fill with BACKGROUND ONLY — "
    "walls, fabric, soft light, neutral scenery — matching the color, tone, and texture of the "
    "immediately adjacent background. "
    "CRITICAL RULES: "
    "1. Do NOT extend, continue, or duplicate any person, face, body, arm, shoulder, or skin "
    "visible elsewhere in the image into the removed area. "
    "People belong only where they already appear — never generate new body parts in the fill zone. "
    "2. Do NOT introduce any new photographic subjects or objects. "
    "3. Fill only with background environment (light, texture, fabric, wall, scenery). "
    "4. The result must look as if the removed objects were peeled away revealing the plain background behind them."
)

OUTPAINT_PROMPT = (
    "The transparent/black border regions in this image need to be filled. "
    "Extend the central scene into these border regions with natural, realistic content "
    "that seamlessly continues the existing background. "
    "Match the lighting direction, color temperature, perspective, and texture of the central content precisely. "
    "The result must look like a single cohesive photograph with no visible seam between the original and extended regions."
)
