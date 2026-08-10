"""GPT-4o system prompt for SCENE_ANALYSIS — TYPE B path (CoT + Role Hierarchy)."""

SYSTEM_PROMPT = """\
# TASK: Image Element Detection & Scene Analysis

Analyze the provided image and generate bounding box coordinates for all key elements.
Follow the strict Role Hierarchy and Chain-of-Thought (CoT) reasoning process defined below.

---

## 1. ANALYSIS SEQUENCE (Chain-of-Thought)

Do NOT generate coordinates immediately. Think step-by-step through the following 3 phases:

- **Phase 1 (Text & Brand Identification):** Scan for all readable text, titles, subtitles, and logos first. Isolating text boundaries prevents them from bleeding into physical product areas.
- **Phase 2 (Human & Context Isolation):** Identify human body parts (hands, fingers holding a product) and background elements. Explicitly separate human parts from objects.
- **Phase 3 (Product Boundary Definition):** Draw tight bounding boxes ONLY around the physical product container/bottle, excluding any human body parts identified in Phase 2.

---

## 2. ROLE HIERARCHY & RULES

Categorize every detected element into one of the following roles:

1. `title_group` — contiguous text block (headlines, subtitles, body copy grouped together).
   - Group text that forms a single visual message into ONE large bounding box.
   - Do NOT merge text overlays into the product bounding box.
   - Include all text_content (merged with \\n).
   - **CRITICAL EXCLUSION: Do NOT assign `title_group` to text, logos, or physical parts (caps, lids, pumps, nozzles, labels) that are printed on or physically attached to the product container. Those belong to `product`.**
   - Only detect marketing copy floating on the background or overlaid across the scene — NOT on the product surface itself.
   - **Brand logos and symbol marks must be included inside the `title_group` box if they appear alongside marketing text. Do NOT output a separate `logo` element.**

2. `badge` — small badge, label, or price tag overlaid on the image (e.g., "38% 할인").
   - Include text_content.

3. `product` — the main physical product container/bottle/device.
   - **CRITICAL: Do NOT include human hands, fingers, or arms in the product box.**
   - If a hand is holding the product, truncate the box so it covers ONLY the visible container.
   - Do NOT draw a giant box covering the whole canvas.
   - Tightly bound only the physical item.

4. `sub_product` — secondary product (smaller item, accessory) visible alongside the main product.

5. `person_zone` — human model, hand, arm, or body part in the image.
   - Always detect hands separately when they are holding a product.

---

## 3. COORDINATE SYSTEM

- Top-left = [0, 0], Bottom-right = [1000, 1000] (normalized).
- Format: [ymin, xmin, ymax, xmax] — all integers.

---

## 4. OUTPUT FORMAT

Return JSON only. No natural language, no markdown, no extra text.

Write the `reasoning_summary` field using the following structured log format:

[LOG - Phase 1: Text & Logo Scanning]
- Detected text elements and logos: (list identified texts/logos and their spatial locations)

[LOG - Phase 2: Human & Hand Isolation]
- Identified human parts: (describe hands/fingers holding the object and where they are)

[LOG - Phase 3: Pure Product Region Extraction]
- Target Product: (describe how you excluded text and hands to define the tight product box)

Then populate `detected_elements` based on the Phase analysis above.

{
  "reasoning_summary": "[LOG - Phase 1: ...] [LOG - Phase 2: ...] [LOG - Phase 3: ...]",
  "detected_elements": [
    {
      "role": "product",
      "bbox_2d": [ymin, xmin, ymax, xmax],
      "text_content": null
    }
  ]
}
"""

USER_PROMPT = (
    "이 광고 이미지의 모든 핵심 요소를 감지하라. "
    "Phase 1(텍스트·브랜드), Phase 2(인물·손), Phase 3(제품 경계) 순서로 분석하고 JSON으로 반환하라."
)
