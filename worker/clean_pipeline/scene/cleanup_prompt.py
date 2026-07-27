"""Prompt used for the OpenAI image-edit cleanup call."""

CLEANUP_PROMPT = (
    "This image contains advertising elements in the marked transparent area "
    "(products, logos, promotional text, CTA buttons, badges). "
    "Remove all advertising elements from the marked area and replace them with "
    "natural, realistic background content that seamlessly continues the surrounding scene. "
    "Match the lighting, perspective, color, and texture of the existing background precisely. "
    "The result must look like those advertising objects were never present."
)
