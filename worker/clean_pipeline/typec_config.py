"""TYPE C 파이프라인 설정.

TYPE B 기반 + P2 계층 텍스트 그룹화 + P3 SAM2 정밀 마스크.
SAM2 서비스가 없으면 P3는 자동으로 polygon 마스크(TYPE B 방식)로 폴백.

환경변수 우선:
  CLEAN_PIPELINE_TYPE   — "B" or "C" (기본: "C")
  SAM2_SERVICE_URL      — SAM2 서비스 URL (기본: http://creative-segmentation:8090)
"""
import os

# "B" or "C"
PIPELINE_TYPE: str = os.environ.get("CLEAN_PIPELINE_TYPE", "C").upper()

# SAM2 세그멘테이션 서비스 URL (Docker 내부망 또는 로컬)
# 서비스 미기동 시 P3가 polygon mask로 폴백 → 에러 없이 진행
SAM2_SERVICE_URL: str = os.environ.get("SAM2_SERVICE_URL", "http://creative-segmentation:8090")

# SAM2 응답 중 이 품질 점수 이상인 감지 결과만 채택 (0~100)
SAM2_MIN_QUALITY_SCORE: float = 50.0

# SAM2 마스크를 채택하려면 GPT-4o bbox와 최소 이 IoU 이상이어야 함
SAM2_MIN_IOU: float = 0.25

# SAM2를 적용할 role 목록 (text는 GDINO 정확도 낮아서 제외)
SAM2_ELIGIBLE_ROLES: set[str] = {"product", "logo"}
