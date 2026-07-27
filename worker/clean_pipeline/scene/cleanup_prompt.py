"""Prompts used for OpenAI image-edit API calls in the scene pipeline."""

CLEANUP_PROMPT = (
    "This image contains advertising elements in the marked transparent area "
    "(products, logos, promotional text, CTA buttons, badges). "
    "Remove all advertising elements from the marked area and replace them with "
    "natural, realistic background content that seamlessly continues the surrounding scene. "
    "Match the lighting, perspective, color, and texture of the existing background precisely. "
    "The result must look like those advertising objects were never present."
)

OUTPAINT_PROMPT = (
    "The transparent/black border regions in this image need to be filled. "
    "Extend the central scene into these border regions with natural, realistic content "
    "that seamlessly continues the existing background. "
    "Match the lighting direction, color temperature, perspective, and texture of the central content precisely. "
    "The result must look like a single cohesive photograph with no visible seam between the original and extended regions."
)
