"""TYPE D 파이프라인 튜닝 파라미터.

수치 조정은 이 파일만 수정. 로직(outpainter)은 건드리지 않아도 됨.
환경변수로 런타임 오버라이드 가능.
"""
import os

# Rule 1: 마스크를 원본 안쪽으로 확장 (AI가 경계 픽셀까지 재생성)
# 0 = 비활성 / Method B: 40px (AI에게 경계 context 제공, 복원은 Rule 3이 담당)
MASK_INWARD_EXPAND_PX: int = int(os.environ.get("TYPE_D_MASK_EXPAND_PX", "40"))

# Rule 3: 생성 후 alpha blend 그라데이션 폭 (경계 페더링)
# 0 = 비활성 / Method B: 40px (AI 생성 결과 위에 원본 복원 + 경계 부드럽게 blend)
BLEND_FEATHER_PX: int = int(os.environ.get("TYPE_D_BLEND_FEATHER_PX", "40"))

# Rule 4: 좌우 평균 밝기 차이가 이 값 이상이면 L/R 각각 별도 API 호출
# 0 = 비활성 (항상 통합 처리)
SEPARATE_LR_BRIGHTNESS_DIFF: int = int(os.environ.get("TYPE_D_SEPARATE_LR_DIFF", "0"))

# 순차 L/R 처리: 좌측 완료 결과를 우측 입력으로 사용 (컨텍스트 간섭 차단)
# True이면 SEPARATE_LR_BRIGHTNESS_DIFF보다 우선 적용
SEQUENTIAL_LR_CALLS: bool = os.environ.get("TYPE_D_SEQUENTIAL_LR", "true").lower() == "true"

# Rule 5: 인접 픽셀 std 이하이면 AI 없이 단색 직접 채움
# 0.0 = 비활성
DARK_BG_STD_THRESHOLD: float = float(os.environ.get("TYPE_D_DARK_STD", "0.0"))

# 좌우 최소 확장 폭 강제 (px): 원본을 추가 축소하여 각 사이드 확장량을 이 값으로 보장
# 0 = 비활성 (자동 contain-scale 유지)
FORCE_SIDE_EXTEND_PX: int = int(os.environ.get("TYPE_D_FORCE_EXTEND_PX", "0"))

# outpaint 최소 면적 비율 미만이면 API 완전 스킵
MIN_EMPTY_RATIO: float = 0.01

# 단색 채움 시 샘플링할 인접 픽셀 폭 (px)
SOLID_FILL_SAMPLE_WIDTH: int = 10
