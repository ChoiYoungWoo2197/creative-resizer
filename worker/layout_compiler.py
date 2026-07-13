"""5단계: Layout compiler — CreativeObjectSet + target spec → multi-candidate CandidateLayout.

흐름:
    compile_layout(creative_object_set, w, h, safe_zones, layout_profile)
        → 비율 타입 판별 (horizontal / square / vertical)
        → 5+ 후보 생성 (template zone assignment)
        → 각 후보 점수화 (7-component weighted score)
        → hard fail 검사 (8가지 조건)
        → best / topCandidates / allCandidates 반환

원칙:
    - NO_CROP_ROLES(cta/headline/body_text/price/discount/logo)는 crop 금지
    - blur background 사용 금지 (배경은 background_builder 담당)
    - 전부 hard fail → emergency_fallback (fallbackUsed=True)
"""

from safe_zone import (
    normalize_safe_zone,
    get_object_safe_zone,
    rect_inside_safe_zone,
    HARD_FAIL_ROLES,
)

# ─── 역할 집합 ────────────────────────────────────────────────────────────────

REQUIRED_ROLES = frozenset({"cta", "headline", "logo", "main_image"})
NO_CROP_ROLES  = frozenset({"cta", "headline", "body_text", "price", "discount", "logo"})
TEXT_ROLES     = frozenset({"headline", "body_text", "price", "discount"})
IMAGE_ROLES    = frozenset({"main_image", "person"})

# ─── 점수 가중치 (합 = 1.0) ───────────────────────────────────────────────────

_W_SAFE_ZONE       = 0.25
_W_NO_CROP         = 0.20
_W_READABILITY     = 0.20
_W_OVERLAP         = 0.15
_W_VISUAL_BALANCE  = 0.10
_W_BG_CLEAN        = 0.05
_W_ORIGINAL_INTENT = 0.05

# ─── 템플릿 정의 ──────────────────────────────────────────────────────────────
# zone 필드:
#   zone_id    : 식별자
#   roles      : 우선순위 순으로 배치할 역할 목록
#   x/y/w/h   : 0.0~1.0 canvas 비율
#   mode       : "center"|"stack_v"|"top_left"|"top_right"|"bottom_center"
#   allow_crop : True이면 obj.canCrop 존중
#   optional   : True이면 매칭 객체 없어도 무시
#   max_objs   : 이 zone에 배치할 최대 객체 수

_HORIZONTAL_TEMPLATES = [
    {
        "id": "horizontal_split_left",
        "zones": [
            {"zone_id": "image",  "roles": ["main_image", "person"],
             "x": 0.02, "y": 0.00, "w": 0.46, "h": 1.00,
             "mode": "center",        "allow_crop": True,  "optional": False, "max_objs": 1},
            {"zone_id": "logo",   "roles": ["logo"],
             "x": 0.52, "y": 0.03, "w": 0.20, "h": 0.18,
             "mode": "top_left",      "allow_crop": False, "optional": True,  "max_objs": 1},
            {"zone_id": "text",   "roles": ["headline", "body_text", "price", "discount"],
             "x": 0.52, "y": 0.24, "w": 0.45, "h": 0.50,
             "mode": "stack_v",       "allow_crop": False, "optional": False, "max_objs": 4},
            {"zone_id": "cta",    "roles": ["cta"],
             "x": 0.52, "y": 0.77, "w": 0.45, "h": 0.18,
             "mode": "bottom_center", "allow_crop": False, "optional": False, "max_objs": 1},
            {"zone_id": "deco",   "roles": ["decoration"],
             "x": 0.03, "y": 0.82, "w": 0.44, "h": 0.14,
             "mode": "center",        "allow_crop": True,  "optional": True,  "max_objs": 1},
        ],
    },
    {
        "id": "horizontal_split_right",
        "zones": [
            {"zone_id": "image",  "roles": ["main_image", "person"],
             "x": 0.52, "y": 0.00, "w": 0.46, "h": 1.00,
             "mode": "center",        "allow_crop": True,  "optional": False, "max_objs": 1},
            {"zone_id": "logo",   "roles": ["logo"],
             "x": 0.02, "y": 0.03, "w": 0.20, "h": 0.18,
             "mode": "top_left",      "allow_crop": False, "optional": True,  "max_objs": 1},
            {"zone_id": "text",   "roles": ["headline", "body_text", "price", "discount"],
             "x": 0.02, "y": 0.24, "w": 0.45, "h": 0.50,
             "mode": "stack_v",       "allow_crop": False, "optional": False, "max_objs": 4},
            {"zone_id": "cta",    "roles": ["cta"],
             "x": 0.02, "y": 0.77, "w": 0.45, "h": 0.18,
             "mode": "bottom_center", "allow_crop": False, "optional": False, "max_objs": 1},
        ],
    },
    {
        "id": "horizontal_center_product",
        "zones": [
            {"zone_id": "logo",   "roles": ["logo"],
             "x": 0.03, "y": 0.03, "w": 0.18, "h": 0.18,
             "mode": "top_left",      "allow_crop": False, "optional": True,  "max_objs": 1},
            {"zone_id": "text",   "roles": ["headline", "body_text", "price", "discount"],
             "x": 0.24, "y": 0.03, "w": 0.50, "h": 0.24,
             "mode": "stack_v",       "allow_crop": False, "optional": False, "max_objs": 3},
            {"zone_id": "image",  "roles": ["main_image", "person"],
             "x": 0.14, "y": 0.28, "w": 0.72, "h": 0.48,
             "mode": "center",        "allow_crop": True,  "optional": False, "max_objs": 1},
            {"zone_id": "cta",    "roles": ["cta"],
             "x": 0.30, "y": 0.79, "w": 0.40, "h": 0.17,
             "mode": "bottom_center", "allow_crop": False, "optional": False, "max_objs": 1},
        ],
    },
    {
        "id": "horizontal_text_overlay_safe",
        "zones": [
            {"zone_id": "logo",   "roles": ["logo"],
             "x": 0.03, "y": 0.04, "w": 0.18, "h": 0.16,
             "mode": "top_left",      "allow_crop": False, "optional": True,  "max_objs": 1},
            {"zone_id": "text",   "roles": ["headline", "body_text", "price", "discount"],
             "x": 0.03, "y": 0.25, "w": 0.44, "h": 0.50,
             "mode": "stack_v",       "allow_crop": False, "optional": False, "max_objs": 4},
            {"zone_id": "cta",    "roles": ["cta"],
             "x": 0.03, "y": 0.78, "w": 0.44, "h": 0.18,
             "mode": "bottom_center", "allow_crop": False, "optional": False, "max_objs": 1},
            {"zone_id": "image",  "roles": ["main_image", "person"],
             "x": 0.52, "y": 0.05, "w": 0.45, "h": 0.90,
             "mode": "center",        "allow_crop": True,  "optional": False, "max_objs": 1},
        ],
    },
    {
        "id": "horizontal_minimal",
        "zones": [
            {"zone_id": "logo",     "roles": ["logo"],
             "x": 0.03, "y": 0.04, "w": 0.15, "h": 0.16,
             "mode": "top_left",      "allow_crop": False, "optional": True,  "max_objs": 1},
            {"zone_id": "headline", "roles": ["headline"],
             "x": 0.03, "y": 0.22, "w": 0.55, "h": 0.35,
             "mode": "center",        "allow_crop": False, "optional": False, "max_objs": 1},
            {"zone_id": "cta",      "roles": ["cta"],
             "x": 0.03, "y": 0.62, "w": 0.35, "h": 0.26,
             "mode": "center",        "allow_crop": False, "optional": False, "max_objs": 1},
            {"zone_id": "image",    "roles": ["main_image", "person"],
             "x": 0.60, "y": 0.05, "w": 0.37, "h": 0.88,
             "mode": "center",        "allow_crop": True,  "optional": True,  "max_objs": 1},
        ],
    },
]

_SQUARE_TEMPLATES = [
    {
        "id": "square_product_top_text_bottom",
        "zones": [
            {"zone_id": "logo",  "roles": ["logo"],
             "x": 0.03, "y": 0.03, "w": 0.22, "h": 0.12,
             "mode": "top_left",      "allow_crop": False, "optional": True,  "max_objs": 1},
            {"zone_id": "image", "roles": ["main_image", "person"],
             "x": 0.08, "y": 0.03, "w": 0.84, "h": 0.52,
             "mode": "center",        "allow_crop": True,  "optional": False, "max_objs": 1},
            {"zone_id": "text",  "roles": ["headline", "body_text", "price", "discount"],
             "x": 0.05, "y": 0.58, "w": 0.90, "h": 0.25,
             "mode": "stack_v",       "allow_crop": False, "optional": False, "max_objs": 3},
            {"zone_id": "cta",   "roles": ["cta"],
             "x": 0.25, "y": 0.84, "w": 0.50, "h": 0.12,
             "mode": "bottom_center", "allow_crop": False, "optional": False, "max_objs": 1},
        ],
    },
    {
        "id": "square_text_top_product_center",
        "zones": [
            {"zone_id": "logo",  "roles": ["logo"],
             "x": 0.03, "y": 0.03, "w": 0.22, "h": 0.10,
             "mode": "top_left",      "allow_crop": False, "optional": True,  "max_objs": 1},
            {"zone_id": "text",  "roles": ["headline", "body_text", "price", "discount"],
             "x": 0.05, "y": 0.14, "w": 0.90, "h": 0.28,
             "mode": "stack_v",       "allow_crop": False, "optional": False, "max_objs": 3},
            {"zone_id": "image", "roles": ["main_image", "person"],
             "x": 0.10, "y": 0.44, "w": 0.80, "h": 0.38,
             "mode": "center",        "allow_crop": True,  "optional": False, "max_objs": 1},
            {"zone_id": "cta",   "roles": ["cta"],
             "x": 0.20, "y": 0.84, "w": 0.60, "h": 0.12,
             "mode": "bottom_center", "allow_crop": False, "optional": False, "max_objs": 1},
        ],
    },
    {
        "id": "square_center_focus",
        "zones": [
            {"zone_id": "logo",     "roles": ["logo"],
             "x": 0.03, "y": 0.03, "w": 0.22, "h": 0.10,
             "mode": "top_left",      "allow_crop": False, "optional": True,  "max_objs": 1},
            {"zone_id": "image",    "roles": ["main_image", "person"],
             "x": 0.05, "y": 0.05, "w": 0.90, "h": 0.70,
             "mode": "center",        "allow_crop": True,  "optional": False, "max_objs": 1},
            {"zone_id": "headline", "roles": ["headline"],
             "x": 0.05, "y": 0.72, "w": 0.90, "h": 0.14,
             "mode": "center",        "allow_crop": False, "optional": False, "max_objs": 1},
            {"zone_id": "cta",      "roles": ["cta"],
             "x": 0.25, "y": 0.86, "w": 0.50, "h": 0.10,
             "mode": "bottom_center", "allow_crop": False, "optional": False, "max_objs": 1},
        ],
    },
    {
        "id": "square_split_left_right",
        "zones": [
            {"zone_id": "logo",  "roles": ["logo"],
             "x": 0.03, "y": 0.03, "w": 0.22, "h": 0.12,
             "mode": "top_left",      "allow_crop": False, "optional": True,  "max_objs": 1},
            {"zone_id": "text",  "roles": ["headline", "body_text", "price", "discount"],
             "x": 0.03, "y": 0.18, "w": 0.44, "h": 0.55,
             "mode": "stack_v",       "allow_crop": False, "optional": False, "max_objs": 3},
            {"zone_id": "cta",   "roles": ["cta"],
             "x": 0.03, "y": 0.76, "w": 0.44, "h": 0.20,
             "mode": "center",        "allow_crop": False, "optional": False, "max_objs": 1},
            {"zone_id": "image", "roles": ["main_image", "person"],
             "x": 0.52, "y": 0.05, "w": 0.45, "h": 0.90,
             "mode": "center",        "allow_crop": True,  "optional": False, "max_objs": 1},
        ],
    },
    {
        "id": "square_minimal",
        "zones": [
            {"zone_id": "logo",     "roles": ["logo"],
             "x": 0.03, "y": 0.03, "w": 0.22, "h": 0.10,
             "mode": "top_left",      "allow_crop": False, "optional": True,  "max_objs": 1},
            {"zone_id": "image",    "roles": ["main_image", "person"],
             "x": 0.10, "y": 0.15, "w": 0.80, "h": 0.55,
             "mode": "center",        "allow_crop": True,  "optional": True,  "max_objs": 1},
            {"zone_id": "headline", "roles": ["headline"],
             "x": 0.05, "y": 0.72, "w": 0.90, "h": 0.14,
             "mode": "center",        "allow_crop": False, "optional": False, "max_objs": 1},
            {"zone_id": "cta",      "roles": ["cta"],
             "x": 0.25, "y": 0.86, "w": 0.50, "h": 0.10,
             "mode": "bottom_center", "allow_crop": False, "optional": False, "max_objs": 1},
        ],
    },
]

# ─── 9단계: 좁은 safe zone용 추가 horizontal 후보 (tight left/right margin 대응) ──
# x 범위를 0.20~0.80 내로 제한 → safeZone right/left 20% 지면에서도 hard fail 없음

_HORIZONTAL_TEMPLATES_EXTRA = [
    {
        "id": "horizontal_text_left_product_right",
        "zones": [
            {"zone_id": "logo",   "roles": ["logo"],
             "x": 0.21, "y": 0.06, "w": 0.16, "h": 0.14,
             "mode": "top_left",      "allow_crop": False, "optional": True,  "max_objs": 1},
            {"zone_id": "text",   "roles": ["headline", "body_text", "price", "discount"],
             "x": 0.21, "y": 0.24, "w": 0.27, "h": 0.44,
             "mode": "stack_v",       "allow_crop": False, "optional": False, "max_objs": 3},
            {"zone_id": "cta",    "roles": ["cta"],
             "x": 0.21, "y": 0.72, "w": 0.27, "h": 0.20,
             "mode": "bottom_center", "allow_crop": False, "optional": False, "max_objs": 1},
            {"zone_id": "image",  "roles": ["main_image", "person"],
             "x": 0.52, "y": 0.05, "w": 0.27, "h": 0.90,
             "mode": "center",        "allow_crop": True,  "optional": False, "max_objs": 1},
        ],
    },
    {
        "id": "horizontal_background_full_text_cta_center",
        "zones": [
            {"zone_id": "logo",   "roles": ["logo"],
             "x": 0.21, "y": 0.06, "w": 0.18, "h": 0.14,
             "mode": "top_left",      "allow_crop": False, "optional": True,  "max_objs": 1},
            {"zone_id": "text",   "roles": ["headline", "body_text", "price", "discount"],
             "x": 0.21, "y": 0.24, "w": 0.58, "h": 0.40,
             "mode": "stack_v",       "allow_crop": False, "optional": False, "max_objs": 4},
            {"zone_id": "cta",    "roles": ["cta"],
             "x": 0.30, "y": 0.68, "w": 0.40, "h": 0.22,
             "mode": "bottom_center", "allow_crop": False, "optional": False, "max_objs": 1},
            {"zone_id": "image",  "roles": ["main_image", "person"],
             "x": 0.21, "y": 0.05, "w": 0.58, "h": 0.17,
             "mode": "center",        "allow_crop": True,  "optional": True,  "max_objs": 1},
        ],
    },
    {
        "id": "horizontal_no_product_reposition_preserve_original",
        "zones": [
            {"zone_id": "logo",     "roles": ["logo"],
             "x": 0.21, "y": 0.05, "w": 0.18, "h": 0.14,
             "mode": "top_left",      "allow_crop": False, "optional": True,  "max_objs": 1},
            {"zone_id": "headline", "roles": ["headline"],
             "x": 0.21, "y": 0.20, "w": 0.58, "h": 0.30,
             "mode": "center",        "allow_crop": False, "optional": False, "max_objs": 1},
            {"zone_id": "cta",      "roles": ["cta"],
             "x": 0.30, "y": 0.72, "w": 0.40, "h": 0.20,
             "mode": "bottom_center", "allow_crop": False, "optional": False, "max_objs": 1},
        ],
    },
]

# ─── 9단계: 추가 square 후보 ──────────────────────────────────────────────────

_SQUARE_TEMPLATES_EXTRA = [
    {
        "id": "square_headline_top_product_right_cta_bottom",
        "zones": [
            {"zone_id": "logo",  "roles": ["logo"],
             "x": 0.04, "y": 0.04, "w": 0.22, "h": 0.10,
             "mode": "top_left",      "allow_crop": False, "optional": True,  "max_objs": 1},
            {"zone_id": "text",  "roles": ["headline", "body_text"],
             "x": 0.04, "y": 0.16, "w": 0.48, "h": 0.34,
             "mode": "stack_v",       "allow_crop": False, "optional": False, "max_objs": 2},
            {"zone_id": "image", "roles": ["main_image", "person"],
             "x": 0.55, "y": 0.10, "w": 0.41, "h": 0.56,
             "mode": "center",        "allow_crop": True,  "optional": False, "max_objs": 1},
            {"zone_id": "cta",   "roles": ["cta"],
             "x": 0.15, "y": 0.84, "w": 0.70, "h": 0.12,
             "mode": "bottom_center", "allow_crop": False, "optional": False, "max_objs": 1},
        ],
    },
    {
        "id": "square_original_center_crop_cta_bottom",
        "zones": [
            {"zone_id": "logo",     "roles": ["logo"],
             "x": 0.04, "y": 0.03, "w": 0.22, "h": 0.10,
             "mode": "top_left",      "allow_crop": False, "optional": True,  "max_objs": 1},
            {"zone_id": "image",    "roles": ["main_image", "person"],
             "x": 0.05, "y": 0.04, "w": 0.90, "h": 0.74,
             "mode": "center",        "allow_crop": True,  "optional": True,  "max_objs": 1},
            {"zone_id": "headline", "roles": ["headline"],
             "x": 0.05, "y": 0.77, "w": 0.90, "h": 0.10,
             "mode": "center",        "allow_crop": False, "optional": False, "max_objs": 1},
            {"zone_id": "cta",      "roles": ["cta"],
             "x": 0.20, "y": 0.87, "w": 0.60, "h": 0.09,
             "mode": "bottom_center", "allow_crop": False, "optional": False, "max_objs": 1},
        ],
    },
    {
        "id": "square_product_center_cta_below",
        "zones": [
            {"zone_id": "logo",     "roles": ["logo"],
             "x": 0.04, "y": 0.03, "w": 0.22, "h": 0.09,
             "mode": "top_left",      "allow_crop": False, "optional": True,  "max_objs": 1},
            {"zone_id": "image",    "roles": ["main_image", "person"],
             "x": 0.08, "y": 0.04, "w": 0.84, "h": 0.67,
             "mode": "center",        "allow_crop": True,  "optional": False, "max_objs": 1},
            {"zone_id": "headline", "roles": ["headline"],
             "x": 0.05, "y": 0.73, "w": 0.90, "h": 0.12,
             "mode": "center",        "allow_crop": False, "optional": False, "max_objs": 1},
            {"zone_id": "cta",      "roles": ["cta"],
             "x": 0.20, "y": 0.86, "w": 0.60, "h": 0.11,
             "mode": "bottom_center", "allow_crop": False, "optional": False, "max_objs": 1},
        ],
    },
]

_VERTICAL_TEMPLATES = [
    {
        "id": "vertical_logo_top_product_center_cta_bottom",
        "zones": [
            {"zone_id": "logo",  "roles": ["logo"],
             "x": 0.05, "y": 0.03, "w": 0.90, "h": 0.10,
             "mode": "top_left",      "allow_crop": False, "optional": True,  "max_objs": 1},
            {"zone_id": "image", "roles": ["main_image", "person"],
             "x": 0.05, "y": 0.15, "w": 0.90, "h": 0.52,
             "mode": "center",        "allow_crop": True,  "optional": False, "max_objs": 1},
            {"zone_id": "text",  "roles": ["headline", "body_text", "price", "discount"],
             "x": 0.05, "y": 0.69, "w": 0.90, "h": 0.18,
             "mode": "stack_v",       "allow_crop": False, "optional": False, "max_objs": 2},
            {"zone_id": "cta",   "roles": ["cta"],
             "x": 0.10, "y": 0.88, "w": 0.80, "h": 0.09,
             "mode": "bottom_center", "allow_crop": False, "optional": False, "max_objs": 1},
        ],
    },
    {
        "id": "vertical_text_top_product_middle_cta_bottom",
        "zones": [
            {"zone_id": "logo",  "roles": ["logo"],
             "x": 0.05, "y": 0.03, "w": 0.30, "h": 0.08,
             "mode": "top_left",      "allow_crop": False, "optional": True,  "max_objs": 1},
            {"zone_id": "text",  "roles": ["headline", "body_text", "price", "discount"],
             "x": 0.05, "y": 0.13, "w": 0.90, "h": 0.25,
             "mode": "stack_v",       "allow_crop": False, "optional": False, "max_objs": 3},
            {"zone_id": "image", "roles": ["main_image", "person"],
             "x": 0.05, "y": 0.40, "w": 0.90, "h": 0.45,
             "mode": "center",        "allow_crop": True,  "optional": False, "max_objs": 1},
            {"zone_id": "cta",   "roles": ["cta"],
             "x": 0.10, "y": 0.87, "w": 0.80, "h": 0.09,
             "mode": "bottom_center", "allow_crop": False, "optional": False, "max_objs": 1},
        ],
    },
    {
        "id": "vertical_product_top_text_middle_cta_bottom",
        "zones": [
            {"zone_id": "logo",  "roles": ["logo"],
             "x": 0.05, "y": 0.03, "w": 0.30, "h": 0.08,
             "mode": "top_left",      "allow_crop": False, "optional": True,  "max_objs": 1},
            {"zone_id": "image", "roles": ["main_image", "person"],
             "x": 0.05, "y": 0.12, "w": 0.90, "h": 0.42,
             "mode": "center",        "allow_crop": True,  "optional": False, "max_objs": 1},
            {"zone_id": "text",  "roles": ["headline", "body_text", "price", "discount"],
             "x": 0.05, "y": 0.56, "w": 0.90, "h": 0.28,
             "mode": "stack_v",       "allow_crop": False, "optional": False, "max_objs": 3},
            {"zone_id": "cta",   "roles": ["cta"],
             "x": 0.10, "y": 0.86, "w": 0.80, "h": 0.10,
             "mode": "bottom_center", "allow_crop": False, "optional": False, "max_objs": 1},
        ],
    },
    {
        "id": "vertical_story_safe",
        "zones": [
            {"zone_id": "logo",  "roles": ["logo"],
             "x": 0.08, "y": 0.14, "w": 0.30, "h": 0.07,
             "mode": "top_left",      "allow_crop": False, "optional": True,  "max_objs": 1},
            {"zone_id": "image", "roles": ["main_image", "person"],
             "x": 0.05, "y": 0.22, "w": 0.90, "h": 0.42,
             "mode": "center",        "allow_crop": True,  "optional": False, "max_objs": 1},
            {"zone_id": "text",  "roles": ["headline", "body_text", "price", "discount"],
             "x": 0.08, "y": 0.66, "w": 0.84, "h": 0.18,
             "mode": "stack_v",       "allow_crop": False, "optional": False, "max_objs": 2},
            {"zone_id": "cta",   "roles": ["cta"],
             "x": 0.12, "y": 0.85, "w": 0.76, "h": 0.08,
             "mode": "bottom_center", "allow_crop": False, "optional": False, "max_objs": 1},
        ],
    },
    {
        "id": "vertical_minimal",
        "zones": [
            {"zone_id": "logo",     "roles": ["logo"],
             "x": 0.05, "y": 0.04, "w": 0.35, "h": 0.08,
             "mode": "top_left",      "allow_crop": False, "optional": True,  "max_objs": 1},
            {"zone_id": "headline", "roles": ["headline"],
             "x": 0.05, "y": 0.14, "w": 0.90, "h": 0.20,
             "mode": "center",        "allow_crop": False, "optional": False, "max_objs": 1},
            {"zone_id": "image",    "roles": ["main_image", "person"],
             "x": 0.08, "y": 0.36, "w": 0.84, "h": 0.44,
             "mode": "center",        "allow_crop": True,  "optional": True,  "max_objs": 1},
            {"zone_id": "cta",      "roles": ["cta"],
             "x": 0.12, "y": 0.82, "w": 0.76, "h": 0.12,
             "mode": "bottom_center", "allow_crop": False, "optional": False, "max_objs": 1},
        ],
    },
]


# ─── 유틸리티 ─────────────────────────────────────────────────────────────────

def _ratio_type(w: int, h: int) -> str:
    """canvas 비율로 레이아웃 유형 판별.

    >>> _ratio_type(1200, 628)
    'horizontal'
    >>> _ratio_type(1080, 1080)
    'square'
    >>> _ratio_type(1080, 1920)
    'vertical'
    """
    r = w / max(h, 1)
    if r >= 1.3:
        return "horizontal"
    if r >= 0.77:
        return "square"
    return "vertical"


def _get_templates(ratio_type: str) -> list:
    return {
        "horizontal": _HORIZONTAL_TEMPLATES + _HORIZONTAL_TEMPLATES_EXTRA,
        "square":     _SQUARE_TEMPLATES + _SQUARE_TEMPLATES_EXTRA,
        "vertical":   _VERTICAL_TEMPLATES,
    }.get(ratio_type, _HORIZONTAL_TEMPLATES + _HORIZONTAL_TEMPLATES_EXTRA)


def _objects_by_role(objects: list) -> dict:
    """role → [object, ...] dict. zIndex 내림차순 정렬."""
    result: dict = {}
    for obj in objects:
        role = obj.get("role", "unknown")
        result.setdefault(role, []).append(obj)
    for role in result:
        result[role] = sorted(result[role], key=lambda o: -o.get("zIndex", 0))
    return result


def _objects_by_id(objects: list) -> dict:
    return {obj["id"]: obj for obj in objects if obj.get("id")}


def _zone_px(zone: dict, canvas_w: int, canvas_h: int) -> tuple:
    """zone 비율 좌표 → 픽셀."""
    x = max(0, int(zone["x"] * canvas_w))
    y = max(0, int(zone["y"] * canvas_h))
    w = max(1, int(zone["w"] * canvas_w))
    h = max(1, int(zone["h"] * canvas_h))
    return x, y, min(w, canvas_w - x), min(h, canvas_h - y)


# ─── 단일 object 배치 ────────────────────────────────────────────────────────

def _place_in_zone(obj: dict, zone_x: int, zone_y: int, zone_w: int, zone_h: int,
                   allow_crop: bool = True, padding_ratio: float = 0.04,
                   h_align: str = "center", v_align: str = "center") -> dict:
    """Object 1개를 zone에 배치. contain/cover 스케일 결정.

    - allow_crop=True AND obj.canCrop=True → cover (crop 발생 가능)
    - 그 외 → contain (crop 없음)

    >>> obj = {"id":"o1","role":"main_image","bbox":{"width":400,"height":400},
    ...        "canCrop":True,"minScale":0.3,"maxScale":3.0}
    >>> p = _place_in_zone(obj, 0, 0, 600, 300, allow_crop=True, padding_ratio=0.0)
    >>> p["width"] == 600 and p["height"] == 300
    True
    >>> p["crop"] is not None
    True
    """
    bbox   = obj.get("bbox") or {}
    obj_w  = max(1, int(bbox.get("width",  zone_w)))
    obj_h  = max(1, int(bbox.get("height", zone_h)))
    min_s  = float(obj.get("minScale", 0.3))
    max_s  = float(obj.get("maxScale", 3.0))
    can_crop = bool(obj.get("canCrop", False)) and allow_crop

    pad_x   = max(2, int(zone_w * padding_ratio))
    pad_y   = max(2, int(zone_h * padding_ratio))
    avail_w = max(1, zone_w - pad_x * 2)
    avail_h = max(1, zone_h - pad_y * 2)

    scale = (max(avail_w / obj_w, avail_h / obj_h) if can_crop
             else min(avail_w / obj_w, avail_h / obj_h))
    scale = max(min_s, min(max_s, scale))

    scaled_w = max(1, int(obj_w * scale))
    scaled_h = max(1, int(obj_h * scale))

    crop = None
    if can_crop and (scaled_w > avail_w or scaled_h > avail_h):
        cl = max(0, (scaled_w - avail_w) // 2)
        ct = max(0, (scaled_h - avail_h) // 2)
        cr = max(0, scaled_w - avail_w - cl)
        cb = max(0, scaled_h - avail_h - ct)
        if cl + ct + cr + cb > 0:
            crop = {"left": cl, "top": ct, "right": cr, "bottom": cb}
        final_w = min(scaled_w, avail_w)
        final_h = min(scaled_h, avail_h)
    else:
        final_w, final_h = scaled_w, scaled_h

    if h_align == "left":
        px = zone_x + pad_x
    elif h_align == "right":
        px = zone_x + zone_w - pad_x - final_w
    else:
        px = zone_x + pad_x + max(0, (avail_w - final_w) // 2)

    if v_align == "top":
        py = zone_y + pad_y
    elif v_align == "bottom":
        py = zone_y + zone_h - pad_y - final_h
    else:
        py = zone_y + pad_y + max(0, (avail_h - final_h) // 2)

    return {
        "objectId": obj.get("id",   ""),
        "role":     obj.get("role", ""),
        "x":        max(0, px),
        "y":        max(0, py),
        "width":    max(1, final_w),
        "height":   max(1, final_h),
        "scale":    round(scale, 4),
        "crop":     crop,
        "dropped":  False,
    }


def _stack_v_in_zone(objs: list, zone_x: int, zone_y: int,
                     zone_w: int, zone_h: int,
                     gap_px: int = 6, pad_ratio: float = 0.03) -> list:
    """여러 object를 zone 안에 수직 스택 (모두 contain)."""
    if not objs:
        return []
    n = len(objs)
    pad_x  = max(2, int(zone_w * pad_ratio))
    pad_y  = max(2, int(zone_h * pad_ratio))
    avail_h = max(1, zone_h - pad_y * 2 - gap_px * (n - 1))
    slot_h  = max(8, avail_h // n)

    result = []
    y = zone_y + pad_y
    for i, obj in enumerate(objs):
        is_last = (i == n - 1)
        h = max(8, (zone_y + zone_h - pad_y) - y) if is_last else slot_h
        p = _place_in_zone(obj, zone_x, y, zone_w, max(8, h),
                           allow_crop=False, padding_ratio=pad_ratio,
                           h_align="center", v_align="center")
        result.append(p)
        y += slot_h + gap_px
    return result


def _apply_zone_mode(mode: str, objs: list,
                     zone_x: int, zone_y: int, zone_w: int, zone_h: int,
                     allow_crop: bool) -> list:
    """zone mode에 따라 배치 실행."""
    if not objs:
        return []
    if mode == "stack_v":
        return _stack_v_in_zone(objs, zone_x, zone_y, zone_w, zone_h)
    if mode == "top_left":
        return [_place_in_zone(objs[0], zone_x, zone_y, zone_w, zone_h,
                               allow_crop=False, padding_ratio=0.02,
                               h_align="left", v_align="top")]
    if mode == "top_right":
        return [_place_in_zone(objs[0], zone_x, zone_y, zone_w, zone_h,
                               allow_crop=False, padding_ratio=0.02,
                               h_align="right", v_align="top")]
    if mode == "bottom_center":
        return [_place_in_zone(objs[0], zone_x, zone_y, zone_w, zone_h,
                               allow_crop=False, padding_ratio=0.02,
                               h_align="center", v_align="bottom")]
    # center (default)
    return [_place_in_zone(objs[0], zone_x, zone_y, zone_w, zone_h,
                           allow_crop=allow_crop, padding_ratio=0.04,
                           h_align="center", v_align="center")]


# ─── 후보 생성 ────────────────────────────────────────────────────────────────

def _generate_from_template(template: dict, objs_by_role: dict,
                             canvas_w: int, canvas_h: int,
                             safe_zones: dict) -> dict:
    """Template에 따라 CandidateLayout 생성."""
    placements: list = []
    warnings:   list = []
    placed_ids: set  = set()

    for zone in template.get("zones", []):
        zx, zy, zw, zh = _zone_px(zone, canvas_w, canvas_h)
        mode       = zone.get("mode", "center")
        allow_crop = zone.get("allow_crop", True)
        optional   = zone.get("optional", False)
        max_objs   = zone.get("max_objs", 1)
        zone_roles = zone["roles"]

        zone_objects: list = []
        if mode == "stack_v":
            # 역할별로 하나씩 수집
            for role in zone_roles:
                if len(zone_objects) >= max_objs:
                    break
                for obj in objs_by_role.get(role, []):
                    if obj.get("id") not in placed_ids:
                        zone_objects.append(obj)
                        break
        else:
            # 역할 우선순위에서 첫 매칭 1개
            for role in zone_roles:
                for obj in objs_by_role.get(role, []):
                    if obj.get("id") not in placed_ids:
                        zone_objects.append(obj)
                        break
                if zone_objects:
                    break

        if not zone_objects:
            if not optional:
                warnings.append(
                    f"zone={zone['zone_id']}: no objects for roles {zone_roles}"
                )
            continue

        zone_ps = _apply_zone_mode(mode, zone_objects[:max_objs], zx, zy, zw, zh, allow_crop)
        placements.extend(zone_ps)
        for p in zone_ps:
            if p.get("objectId"):
                placed_ids.add(p["objectId"])

    # 배치되지 않은 required object 경고
    for role in REQUIRED_ROLES:
        for obj in objs_by_role.get(role, []):
            if obj.get("id") not in placed_ids and not obj.get("canDrop", False):
                warnings.append(
                    f"required object {obj.get('id')} (role={role}) not placed"
                )

    return {
        "candidateId":    template["id"],
        "targetWidth":    canvas_w,
        "targetHeight":   canvas_h,
        "placements":     placements,
        "score":          0.0,
        "hardFail":       False,
        "hardFailReasons": [],
        "warnings":       warnings,
    }


# ─── 점수 계산 ────────────────────────────────────────────────────────────────

def _active(placements: list) -> list:
    return [p for p in placements if not p.get("dropped", False)]


def _rect_iou(a: dict, b: dict) -> float:
    """두 placement rect의 IoU.

    >>> a = {"x":0,"y":0,"width":100,"height":100}
    >>> b = {"x":50,"y":50,"width":100,"height":100}
    >>> round(_rect_iou(a,b), 4)
    0.1429
    >>> _rect_iou(a, {"x":200,"y":200,"width":50,"height":50})
    0.0
    """
    ax2 = a["x"] + a["width"]
    ay2 = a["y"] + a["height"]
    bx2 = b["x"] + b["width"]
    by2 = b["y"] + b["height"]
    ix  = max(0, min(ax2, bx2) - max(a["x"], b["x"]))
    iy  = max(0, min(ay2, by2) - max(a["y"], b["y"]))
    inter = ix * iy
    if inter == 0:
        return 0.0
    union = a["width"] * a["height"] + b["width"] * b["height"] - inter
    return inter / max(union, 1)


def _intersection_area(a: dict, b: dict) -> int:
    ix = max(0, min(a["x"] + a["width"],  b["x"] + b["width"])  - max(a["x"], b["x"]))
    iy = max(0, min(a["y"] + a["height"], b["y"] + b["height"]) - max(a["y"], b["y"]))
    return ix * iy


def compute_safe_zone_score(placements: list, safe_zones: dict,
                             canvas_w: int, canvas_h: int) -> float:
    """safe zone 준수 점수 (0~100)."""
    score = 100.0
    for p in _active(placements):
        role = p["role"]
        if role in ("background", "decoration"):
            continue
        sz = get_object_safe_zone(role, safe_zones)
        if not sz:
            continue
        rect = {"x": p["x"], "y": p["y"], "width": p["width"], "height": p["height"]}
        if not rect_inside_safe_zone(rect, sz, canvas_w, canvas_h):
            score -= 35.0 if role in HARD_FAIL_ROLES else 15.0
    return max(0.0, score)


def compute_no_crop_score(placements: list) -> float:
    """crop 최소화 점수 (0~100)."""
    score = 100.0
    for p in _active(placements):
        crop = p.get("crop")
        if not crop:
            continue
        tw = p["width"]  + crop.get("left", 0) + crop.get("right",  0)
        th = p["height"] + crop.get("top",  0) + crop.get("bottom", 0)
        rx = (crop.get("left", 0) + crop.get("right",  0)) / max(tw, 1)
        ry = (crop.get("top",  0) + crop.get("bottom", 0)) / max(th, 1)
        ratio   = max(rx, ry)
        penalty = 60.0 if p["role"] in NO_CROP_ROLES else 20.0
        score  -= ratio * penalty
    return max(0.0, score)


def compute_readability_score(placements: list, canvas_w: int, canvas_h: int) -> float:
    """텍스트/CTA 최소 가독 크기 점수 (0~100)."""
    _MIN_H = {
        "cta":       max(40, int(canvas_h * 0.05)),
        "headline":  max(28, int(canvas_h * 0.04)),
        "body_text": max(18, int(canvas_h * 0.03)),
        "price":     max(18, int(canvas_h * 0.03)),
        "discount":  max(18, int(canvas_h * 0.03)),
        "logo":      max(20, int(canvas_h * 0.03)),
    }
    score = 100.0
    for p in _active(placements):
        min_h = _MIN_H.get(p["role"])
        if min_h and p["height"] < min_h:
            score -= ((min_h - p["height"]) / max(min_h, 1)) * 35.0
    return max(0.0, score)


def compute_overlap_score(placements: list) -> float:
    """겹침 최소화 점수 (0~100)."""
    score  = 100.0
    active = _active(placements)
    for i in range(len(active)):
        for j in range(i + 1, len(active)):
            a, b = active[i], active[j]
            iou  = _rect_iou(a, b)
            if iou <= 0:
                continue
            both = (a["role"] in HARD_FAIL_ROLES and b["role"] in HARD_FAIL_ROLES)
            one  = (a["role"] in HARD_FAIL_ROLES or  b["role"] in HARD_FAIL_ROLES)
            score -= iou * (50.0 if both else 30.0 if one else 15.0)
    return max(0.0, score)


def compute_visual_balance_score(placements: list, canvas_w: int, canvas_h: int) -> float:
    """시각적 무게중심 균형 점수 (0~100)."""
    active = _active(placements)
    if not active:
        return 50.0
    total, cx_s, cy_s = 0, 0.0, 0.0
    for p in active:
        area  = p["width"] * p["height"]
        cx_s += (p["x"] + p["width"]  / 2) * area
        cy_s += (p["y"] + p["height"] / 2) * area
        total += area
    if total == 0:
        return 50.0
    dx = abs(cx_s / total - canvas_w / 2) / max(canvas_w / 2, 1)
    dy = abs(cy_s / total - canvas_h / 2) / max(canvas_h / 2, 1)
    return max(0.0, 100.0 - (dx + dy) / 2 * 60.0)


def compute_background_clean_score(placements: list) -> float:
    """텍스트가 이미지 위에 올라가지 않을수록 높은 점수 (0~100)."""
    text_ps  = [p for p in _active(placements) if p["role"] in TEXT_ROLES | {"cta"}]
    image_ps = [p for p in _active(placements) if p["role"] in IMAGE_ROLES]
    if not text_ps or not image_ps:
        return 80.0
    score = 100.0
    for tp in text_ps:
        for ip in image_ps:
            inter  = _intersection_area(tp, ip)
            tp_area = tp["width"] * tp["height"]
            if tp_area > 0 and inter > 0:
                score -= (inter / tp_area) * 40.0
    return max(0.0, score)


def compute_original_intent_score(placements: list, objs_by_id: dict) -> float:
    """원본 minScale/maxScale 경계 침범 점수 (0~100)."""
    score = 100.0
    for p in _active(placements):
        obj = objs_by_id.get(p.get("objectId", ""))
        if not obj:
            continue
        s     = p.get("scale", 1.0)
        min_s = float(obj.get("minScale", 0.3))
        max_s = float(obj.get("maxScale", 3.0))
        if s < min_s:
            score -= ((min_s - s) / max(min_s, 1e-6)) * 25.0
        elif s > max_s:
            score -= ((s - max_s) / max(max_s, 1e-6)) * 20.0
    return max(0.0, score)


def score_candidate(candidate: dict, safe_zones: dict,
                    canvas_w: int, canvas_h: int,
                    objs_by_id: dict) -> float:
    """7-component 가중 점수 (0~100)."""
    ps = candidate["placements"]
    s = (
          compute_safe_zone_score(ps, safe_zones, canvas_w, canvas_h) * _W_SAFE_ZONE
        + compute_no_crop_score(ps)                                    * _W_NO_CROP
        + compute_readability_score(ps, canvas_w, canvas_h)            * _W_READABILITY
        + compute_overlap_score(ps)                                     * _W_OVERLAP
        + compute_visual_balance_score(ps, canvas_w, canvas_h)         * _W_VISUAL_BALANCE
        + compute_background_clean_score(ps)                           * _W_BG_CLEAN
        + compute_original_intent_score(ps, objs_by_id)                * _W_ORIGINAL_INTENT
    )
    return round(min(100.0, max(0.0, s)), 1)


# ─── Hard fail 검사 ───────────────────────────────────────────────────────────

def detect_overlaps(placements: list) -> list:
    """겹치는 placement 쌍 (a, b, iou) 반환 (iou > 0.05)."""
    active = _active(placements)
    return [
        (active[i], active[j], _rect_iou(active[i], active[j]))
        for i in range(len(active))
        for j in range(i + 1, len(active))
        if _rect_iou(active[i], active[j]) > 0.05
    ]


def hard_fail_candidate(candidate: dict, safe_zones: dict,
                        canvas_w: int, canvas_h: int) -> tuple:
    """8가지 hard fail 조건 검사.
    반환: (is_hard_fail: bool, reasons: list[str])
    """
    reasons: list = []
    ps     = candidate["placements"]
    active = _active(ps)

    cta_min_h      = max(40, int(canvas_h * 0.05))
    headline_min_h = max(28, int(canvas_h * 0.04))

    for p in active:
        role = p["role"]
        rect = {"x": p["x"], "y": p["y"], "width": p["width"], "height": p["height"]}

        # 1. required object가 canvas 밖
        if role in REQUIRED_ROLES:
            if p["x"] + p["width"] <= 0 or p["x"] >= canvas_w:
                reasons.append(f"{role}({p['objectId']}) outside canvas (x)")
            if p["y"] + p["height"] <= 0 or p["y"] >= canvas_h:
                reasons.append(f"{role}({p['objectId']}) outside canvas (y)")

        # 2. CTA safe zone 밖
        if role == "cta":
            sz = get_object_safe_zone("cta", safe_zones)
            if sz and not rect_inside_safe_zone(rect, sz, canvas_w, canvas_h):
                reasons.append(f"cta({p['objectId']}) outside cta safe zone")

        # 3. text/logo safe zone 밖
        if role in ("headline", "body_text", "price", "discount", "logo"):
            sz = get_object_safe_zone(role, safe_zones)
            if sz and not rect_inside_safe_zone(rect, sz, canvas_w, canvas_h):
                reasons.append(f"{role}({p['objectId']}) outside text safe zone")

        # 6. min readable size 미만
        if role == "cta" and p["height"] < cta_min_h:
            reasons.append(
                f"cta height {p['height']}px < min {cta_min_h}px"
            )
        if role == "headline" and p["height"] < headline_min_h:
            reasons.append(
                f"headline height {p['height']}px < min {headline_min_h}px"
            )

        # 7. required object crop ratio > 50%
        if role in REQUIRED_ROLES and p.get("crop"):
            crop = p["crop"]
            tw = p["width"]  + crop.get("left", 0) + crop.get("right",  0)
            th = p["height"] + crop.get("top",  0) + crop.get("bottom", 0)
            rx = (crop.get("left", 0) + crop.get("right",  0)) / max(tw, 1)
            ry = (crop.get("top",  0) + crop.get("bottom", 0)) / max(th, 1)
            if max(rx, ry) > 0.50:
                reasons.append(
                    f"{role}({p['objectId']}) crop ratio {max(rx, ry):.0%} > 50%"
                )

    # 4. CTA ↔ headline/body_text 겹침
    cta_ps  = [p for p in active if p["role"] == "cta"]
    text_ps = [p for p in active if p["role"] in ("headline", "body_text")]
    for c in cta_ps:
        for t in text_ps:
            if _rect_iou(c, t) > 0.10:
                reasons.append(
                    f"cta({c['objectId']}) overlaps {t['role']}({t['objectId']})"
                )

    # 5. logo ↔ CTA 겹침
    logo_ps = [p for p in active if p["role"] == "logo"]
    for lg in logo_ps:
        for c in cta_ps:
            if _rect_iou(lg, c) > 0.10:
                reasons.append(
                    f"logo({lg['objectId']}) overlaps cta({c['objectId']})"
                )

    # 8. 배치된 required object가 없는데 성공 처리
    placed_roles = {p["role"] for p in active}
    if not (placed_roles & REQUIRED_ROLES):
        reasons.append("no required objects placed")

    return bool(reasons), reasons


# ─── Emergency layout ─────────────────────────────────────────────────────────

def _emergency_layout(objs_by_role: dict, canvas_w: int, canvas_h: int,
                      safe_zones: dict) -> dict:
    """전체 후보 hard fail 시 최소 보장 layout — required objects 세로 스택."""
    req_order = ("logo", "headline", "main_image", "cta")
    req_objs  = []
    placed    = set()
    for role in req_order:
        for obj in objs_by_role.get(role, [])[:1]:
            if obj.get("id") not in placed:
                req_objs.append(obj)
                placed.add(obj.get("id"))

    gsz = safe_zones.get("general", {})
    x0  = gsz.get("left",   int(canvas_w * 0.05))
    y0  = gsz.get("top",    int(canvas_h * 0.05))
    x1  = canvas_w - gsz.get("right",  int(canvas_w * 0.05))
    y1  = canvas_h - gsz.get("bottom", int(canvas_h * 0.05))
    aw  = max(1, x1 - x0)
    ah  = max(1, y1 - y0)

    placements = _stack_v_in_zone(req_objs, x0, y0, aw, ah) if req_objs else []

    return {
        "candidateId":    "emergency_fallback",
        "targetWidth":    canvas_w,
        "targetHeight":   canvas_h,
        "placements":     placements,
        "score":          0.0,
        "hardFail":       False,
        "hardFailReasons": [],
        "warnings":       ["emergency fallback: all template candidates failed hard fail"],
        "fallbackUsed":   True,
    }


# ─── 9단계: 중복 제거 / CTA 그룹화 / repair ────────────────────────────────────

def _deduplicate_main_images(
    objs_by_role: dict,
    objects: list,
) -> tuple:
    """main_image 2개 이상 → bbox area 기준 최대 1개만 유지.

    반환: (filtered_objs_by_role, filtered_objects, dropped_ids)
    """
    main_imgs = objs_by_role.get("main_image", [])
    if len(main_imgs) <= 1:
        return objs_by_role, objects, []

    def _area(obj):
        bb = obj.get("bbox") or {}
        return bb.get("width", 0) * bb.get("height", 0)

    best = max(main_imgs, key=_area)
    dropped_ids = [o["id"] for o in main_imgs if o["id"] != best["id"]]
    dropped_set = set(dropped_ids)

    filtered_objects = [o for o in objects if o.get("id") not in dropped_set]
    updated_role = dict(objs_by_role)
    updated_role["main_image"] = [best]
    return updated_role, filtered_objects, dropped_ids


def _merge_cta_group(objs_by_role: dict) -> tuple:
    """CTA 객체 2개 이상 → area 기준 최대 1개만 유지.

    반환: (updated_objs_by_role, cta_group_created: bool)
    """
    ctas = objs_by_role.get("cta", [])
    if len(ctas) <= 1:
        return objs_by_role, False

    def _area(obj):
        bb = obj.get("bbox") or {}
        return bb.get("width", 0) * bb.get("height", 0)

    best_cta = max(ctas, key=_area)
    updated = dict(objs_by_role)
    updated["cta"] = [best_cta]
    return updated, True


def _repair_candidate(
    candidate: dict,
    safe_zones: dict,
    canvas_w: int,
    canvas_h: int,
) -> dict:
    """safe zone 밖 객체를 safe zone 안으로 이동/축소하여 hard fail 해소 시도.

    원본 candidate를 변경하지 않고 수정된 복사본 반환.
    각 역할의 safe zone 기준: get_object_safe_zone() 결과 사용.
    객체가 너무 크면 safe zone 폭/높이 내로 축소 후 위치 조정.
    """
    placements = [dict(p) for p in candidate.get("placements", [])]
    repair_reasons: list = []
    repaired_ids: list = []

    for p in placements:
        if p.get("dropped"):
            continue
        role = p.get("role", "unknown")
        # background/decoration/unknown은 safe zone 제약 없음
        if role in ("background", "decoration", "unknown"):
            continue

        sz = get_object_safe_zone(role, safe_zones)

        # safe zone 없으면 canvas 경계만 클램프
        if not sz:
            new_x = max(0, min(p["x"], canvas_w - p["width"]))
            new_y = max(0, min(p["y"], canvas_h - p["height"]))
            if new_x != p["x"] or new_y != p["y"]:
                p["x"], p["y"] = new_x, new_y
                repair_reasons.append(f"{role} clamped to canvas boundary")
                repaired_ids.append(p.get("objectId", ""))
            continue

        rect = {"x": p["x"], "y": p["y"], "width": p["width"], "height": p["height"]}
        if rect_inside_safe_zone(rect, sz, canvas_w, canvas_h):
            continue  # 이미 안에 있음

        safe_x1 = sz.get("left", 0)
        safe_y1 = sz.get("top", 0)
        safe_x2 = canvas_w - sz.get("right", 0)
        safe_y2 = canvas_h - sz.get("bottom", 0)
        safe_w  = max(1, safe_x2 - safe_x1)
        safe_h  = max(1, safe_y2 - safe_y1)

        new_w = p["width"]
        new_h = p["height"]

        # 객체가 safe zone보다 넓으면 축소
        if new_w > safe_w:
            factor = safe_w / max(new_w, 1)
            new_h  = max(8, int(new_h * factor))
            new_w  = safe_w
            p["scale"] = round(p.get("scale", 1.0) * factor, 4)

        # 객체가 safe zone보다 높으면 축소
        if new_h > safe_h:
            factor = safe_h / max(new_h, 1)
            new_w  = max(8, int(new_w * factor))
            new_h  = safe_h
            p["scale"] = round(p.get("scale", 1.0) * factor, 4)

        # 위치 클램프
        new_x = max(safe_x1, min(p["x"], safe_x2 - new_w))
        new_y = max(safe_y1, min(p["y"], safe_y2 - new_h))

        changed = (
            new_x != p["x"] or new_y != p["y"]
            or new_w != p["width"] or new_h != p["height"]
        )
        if changed:
            reason = (
                f"{role}({p.get('objectId', '')})"
                f" moved [{p['x']},{p['y']},{p['width']},{p['height']}]"
                f"→[{new_x},{new_y},{new_w},{new_h}]"
            )
            p["x"], p["y"]       = new_x, new_y
            p["width"], p["height"] = new_w, new_h
            repair_reasons.append(reason)
            repaired_ids.append(p.get("objectId", ""))

    repaired = dict(candidate)
    repaired["placements"]      = placements
    repaired["repairAttempted"] = True
    repaired["repairApplied"]   = bool(repair_reasons)
    repaired["repairReasons"]   = repair_reasons
    repaired["repairedObjects"] = list(dict.fromkeys(repaired_ids))  # 순서 유지 + 중복 제거
    return repaired


def score_candidate_with_breakdown(
    candidate: dict,
    safe_zones: dict,
    canvas_w: int,
    canvas_h: int,
    objs_by_id: dict,
) -> tuple:
    """score_candidate와 동일하지만 구성요소별 breakdown도 반환.

    반환: (total_score: float, breakdown: dict)
    """
    ps = candidate["placements"]
    sz = compute_safe_zone_score(ps, safe_zones, canvas_w, canvas_h)
    nc = compute_no_crop_score(ps)
    rd = compute_readability_score(ps, canvas_w, canvas_h)
    ov = compute_overlap_score(ps)
    vb = compute_visual_balance_score(ps, canvas_w, canvas_h)
    bg = compute_background_clean_score(ps)
    oi = compute_original_intent_score(ps, objs_by_id)

    total = round(min(100.0, max(0.0,
        sz * _W_SAFE_ZONE
        + nc * _W_NO_CROP
        + rd * _W_READABILITY
        + ov * _W_OVERLAP
        + vb * _W_VISUAL_BALANCE
        + bg * _W_BG_CLEAN
        + oi * _W_ORIGINAL_INTENT
    )), 1)

    breakdown = {
        "safeZoneScore":        round(sz, 1),
        "noCropScore":          round(nc, 1),
        "readabilityScore":     round(rd, 1),
        "overlapScore":         round(ov, 1),
        "visualBalanceScore":   round(vb, 1),
        "backgroundCleanScore": round(bg, 1),
        "originalIntentScore":  round(oi, 1),
        "weights": {
            "safeZone":        _W_SAFE_ZONE,
            "noCrop":          _W_NO_CROP,
            "readability":     _W_READABILITY,
            "overlap":         _W_OVERLAP,
            "visualBalance":   _W_VISUAL_BALANCE,
            "backgroundClean": _W_BG_CLEAN,
            "originalIntent":  _W_ORIGINAL_INTENT,
        },
    }
    return total, breakdown


# ─── 메인 진입점 ──────────────────────────────────────────────────────────────

def compile_layout(
    creative_object_set: dict,
    target_width: int,
    target_height: int,
    safe_zones: dict | None = None,
    layout_profile: str | None = None,
) -> dict:
    """CreativeObjectSet → multi-candidate layout 생성·점수화·선택.

    safe_zones: normalize_safe_zone() 결과 dict. None이면 비율 기반 기본값 사용.
    layout_profile: 미래 확장용 (현재 미사용).

    반환:
    {
      "best": CandidateLayout,
      "topCandidates": [CandidateLayout, ...],
      "allCandidates": [...],
      "metadata": {
        "candidateCount", "validCount", "selectedCandidateId",
        "layoutScore", "ratioType", "hardFailures", "warnings", "fallbackUsed",
        "repairAttempted", "repairApplied", "repairReasons", "repairedObjects",
        "duplicateObjectsRemoved", "ctaGroupCreated"
      }
    }
    """
    objects = (creative_object_set or {}).get("objects", [])

    if safe_zones is None:
        safe_zones = normalize_safe_zone({}, target_width, target_height)

    objs_by_role = _objects_by_role(objects)

    # 9단계: 중복 main_image 제거
    objs_by_role, objects, dup_dropped_ids = _deduplicate_main_images(objs_by_role, objects)

    # 9단계: 복수 CTA 통합
    objs_by_role, cta_group_created = _merge_cta_group(objs_by_role)

    objs_by_id = _objects_by_id(objects)

    ratio_type = _ratio_type(target_width, target_height)
    templates  = _get_templates(ratio_type)

    candidates: list = []
    for tmpl in templates:
        try:
            cand = _generate_from_template(
                tmpl, objs_by_role, target_width, target_height, safe_zones
            )
            is_fail, reasons = hard_fail_candidate(
                cand, safe_zones, target_width, target_height
            )
            cand["hardFail"]        = is_fail
            cand["hardFailReasons"] = reasons
            cand["score"]           = score_candidate(
                cand, safe_zones, target_width, target_height, objs_by_id
            )
        except Exception as e:
            cand = {
                "candidateId":     tmpl["id"],
                "targetWidth":     target_width,
                "targetHeight":    target_height,
                "placements":      [],
                "score":           0.0,
                "hardFail":        True,
                "hardFailReasons": [f"generation error: {e}"],
                "warnings":        [],
            }
        candidates.append(cand)

    valid_candidates = [c for c in candidates if not c.get("hardFail")]

    # 9단계: repair 단계 — valid candidate가 없을 때만 실행
    repair_attempted    = False
    repaired_candidates: list = []

    if not valid_candidates:
        repair_attempted = True
        for cand in candidates:
            if not cand.get("hardFail"):
                continue
            try:
                repaired = _repair_candidate(cand, safe_zones, target_width, target_height)
                is_fail, reasons = hard_fail_candidate(
                    repaired, safe_zones, target_width, target_height
                )
                repaired["hardFail"]        = is_fail
                repaired["hardFailReasons"] = reasons
                if not is_fail:
                    # repair 후보는 자연 통과 후보보다 5% 낮은 점수 부여
                    repaired["score"] = max(0.0, round(
                        score_candidate(repaired, safe_zones, target_width, target_height, objs_by_id)
                        * 0.95, 1
                    ))
                    repaired_candidates.append(repaired)
            except Exception:
                pass

        if repaired_candidates:
            valid_candidates = repaired_candidates

    if not valid_candidates:
        emg = _emergency_layout(objs_by_role, target_width, target_height, safe_zones)
        emg["score"] = score_candidate(
            emg, safe_zones, target_width, target_height, objs_by_id
        )
        candidates.append(emg)
        valid_candidates = [emg]

    best           = max(valid_candidates, key=lambda c: c["score"])
    top_candidates = sorted(valid_candidates, key=lambda c: -c["score"])[:3]

    # scoring breakdown 저장 (debug overlay에서 사용)
    try:
        _, best["scoringBreakdown"] = score_candidate_with_breakdown(
            best, safe_zones, target_width, target_height, objs_by_id
        )
    except Exception:
        best["scoringBreakdown"] = None

    all_hard_failures = list({
        r
        for c in candidates + repaired_candidates
        if c.get("hardFail")
        for r in c.get("hardFailReasons", [])
    })
    all_warnings = list({
        w
        for c in candidates + repaired_candidates
        for w in c.get("warnings", [])
    })

    return {
        "best":          best,
        "topCandidates": top_candidates,
        "allCandidates": candidates + repaired_candidates,
        "metadata": {
            "candidateCount":          len(candidates),
            "validCount":              len(valid_candidates),
            "selectedCandidateId":     best["candidateId"],
            "layoutScore":             best["score"],
            "ratioType":               ratio_type,
            "hardFailures":            all_hard_failures,
            "warnings":                all_warnings,
            "fallbackUsed":            best.get("fallbackUsed", False),
            # 9단계 추가 메타
            "repairAttempted":         repair_attempted,
            "repairApplied":           best.get("repairApplied", False),
            "repairReasons":           best.get("repairReasons", []),
            "repairedObjects":         best.get("repairedObjects", []),
            "duplicateObjectsRemoved": dup_dropped_ids,
            "ctaGroupCreated":         cta_group_created,
        },
    }
