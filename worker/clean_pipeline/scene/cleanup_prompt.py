"""Prompts used for OpenAI image-edit API calls in the scene pipeline."""

CLEANUP_PROMPT = (
    "This image contains advertising elements in the marked transparent area "
    "(products, logos, promotional text, CTA buttons, badges). "
    "Remove all advertising elements from the marked area and fill with background content "
    "that exactly matches the immediately adjacent background — same color, tone, and texture. "
    "CRITICAL: If the surrounding background is dark or black, fill with that same dark/black tone. "
    "Do NOT introduce new photographic subjects (people, bodies, objects) into the removed area. "
    "Only replicate the existing background. The result must look like those objects were never present."
)

OUTPAINT_PROMPT = (
    "The transparent/black border regions in this image need to be filled. "
    "Extend the central scene into these border regions with natural, realistic content "
    "that seamlessly continues the existing background. "
    "Match the lighting direction, color temperature, perspective, and texture of the central content precisely. "
    "The result must look like a single cohesive photograph with no visible seam between the original and extended regions."
)
