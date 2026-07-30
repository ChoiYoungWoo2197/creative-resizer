"""TYPE C 파이프라인 설정 (SAM2 관련 상수).

TYPE B/C 간 분기에 사용되는 SAM2 클라이언트 설정.
파이프라인 타입 라우팅은 pipeline_type_selector.py 참조.

환경변수:
  SAM2_SERVICE_URL      — SAM2 서비스 URL (기본: http://creative-segmentation:8090)
"""
import os

# orchestrator P2/P3 분기용: TYPE B인지 C인지 (D는 pipeline_type_selector에서 처리)
PIPELINE_TYPE: str = os.environ.get("CLEAN_PIPELINE_TYPE", "B").upper()

# SAM2 세그멘테이션 서비스 URL (Docker 내부망 또는 로컬)
# 서비스 미기동 시 P3가 polygon mask로 폴백 → 에러 없이 진행
SAM2_SERVICE_URL: str = os.environ.get("SAM2_SERVICE_URL", "http://creative-segmentation:8090")

# SAM2 응답 중 이 품질 점수 이상인 감지 결과만 채택 (0~100)
SAM2_MIN_QUALITY_SCORE: float = 50.0

# SAM2 마스크를 채택하려면 GPT-4o bbox와 최소 이 IoU 이상이어야 함
SAM2_MIN_IOU: float = 0.25

# SAM2를 적용할 role 목록 (text는 GDINO 정확도 낮아서 제외)
SAM2_ELIGIBLE_ROLES: set[str] = {"product", "logo"}
