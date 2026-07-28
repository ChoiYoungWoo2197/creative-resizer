# clean_v1 클린업 인벤토리

> **작성일**: 2026-07-28  
> **기준 커밋**: `37dc685` (master HEAD)  
> **목적**: clean_v1 전용 저장소로 전환 전 파일 분류. 이 단계에서는 파일을 삭제하지 않는다.

---

## 1. 실제 clean_v1 운영 호출 경로

```
브라우저 Upload
  → Java BannerController.submit()
  → BannerService.submit()  [pipelineVersion = "clean_v1" 정규화]
  → RabbitMQ (BannerProducer → banner.generate queue)
  → BannerConsumer → BannerService.process()
  → WorkerClient.generate()  [POST /generate]
  → worker/app.py  :: /generate endpoint
      pipeline_version == "clean_v1" 분기
      → clean_pipeline/orchestrator.py :: run()
          P1 source/canonical_source.py
          P2 analysis/openai_analyzer.py
          P3 extraction/object_extractor.py
          P4 removal/removal_mask_builder.py
          P5 scene/scene_plate_generator.py
          P6 validation/scene_validator.py   [_AI_VALIDATION_ENABLED=False 임시 비활성]
          P7 layout/layout_validator.py
          P8 render/compositor.py + render_validator.py
      → clean_pipeline/bridge/response_adapter.py
  → BannerService 결과 저장 (MongoDB)
  → ZIP 생성 → 브라우저 다운로드

PSD 분석 사이드 경로 (선택적):
  → BannerService.analyzePsd()
  → WorkerClient.analyzePsd()  [POST /analyze-psd]
  → worker/app.py :: /analyze-psd endpoint
  → worker/psd_analyzer.py (UNKNOWN: clean_v1과 공존 필요 여부 미결)
```

**clean_pipeline 외부 의존성**: stdlib + PIL/Pillow + numpy + cv2 + openai 뿐.  
worker 최상위 모듈(`resizer`, `background`, `verdict` 등) 중 단 하나도 import하지 않는다.

---

## 2–5. 파일 분류 테이블

범례: `runtimeUsed` = 실제 배너 생성 경로에 진입 여부, `testOnly` = 테스트만 참조 여부

### A. clean_pipeline/ (KEEP — 50개 파일)

| path | type | classification | runtimeUsed | testOnly | referencedBy | reason | action | risk |
|------|------|----------------|-------------|----------|--------------|--------|--------|------|
| `worker/clean_pipeline/` | Python pkg | **KEEP** | YES | NO | app.py → orchestrator | clean_v1 본체 전체 | keep as-is | — |
| `worker/clean_pipeline/orchestrator.py` | Python | **KEEP** | YES | NO | app.py `_run_clean_v1()` | P1-P8 진입점 | keep as-is | — |
| `worker/clean_pipeline/contracts.py` | Python | **KEEP** | YES | NO | 모든 P1-P8 모듈 | 공유 타입 정의 | keep as-is | — |
| `worker/clean_pipeline/bridge/response_adapter.py` | Python | **KEEP** | YES | NO | app.py | Worker→Java 응답 변환 (37dc685 수정) | keep as-is | — |
| `worker/clean_pipeline/source/` | Python pkg | **KEEP** | YES | NO | orchestrator P1 | 소스 정규화 | keep as-is | — |
| `worker/clean_pipeline/analysis/` | Python pkg | **KEEP** | YES | NO | orchestrator P2 | OpenAI 장면 분석 | keep as-is | — |
| `worker/clean_pipeline/extraction/` | Python pkg | **KEEP** | YES | NO | orchestrator P3 | 객체 추출 | keep as-is | — |
| `worker/clean_pipeline/removal/` | Python pkg | **KEEP** | YES | NO | orchestrator P4 | 마스크 생성 | keep as-is | — |
| `worker/clean_pipeline/scene/` | Python pkg | **KEEP** | YES | NO | orchestrator P5 | 배경 생성 | keep as-is | — |
| `worker/clean_pipeline/validation/` | Python pkg | **KEEP** | YES | NO | orchestrator P6 | 장면 검증 (P6 임시 비활성) | keep as-is | — |
| `worker/clean_pipeline/layout/` | Python pkg | **KEEP** | YES | NO | orchestrator P7 | 레이아웃 검증 | keep as-is | — |
| `worker/clean_pipeline/render/` | Python pkg | **KEEP** | YES | NO | orchestrator P8 | 최종 합성·검증 | keep as-is | — |

### B. Worker 프레임 (MIGRATE — 2개)

| path | type | classification | runtimeUsed | testOnly | referencedBy | reason | action | risk |
|------|------|----------------|-------------|----------|--------------|--------|--------|------|
| `worker/app.py` | Python | **MIGRATE** | YES | NO | Docker CMD | Flask 진입점. clean_v1 라우팅 포함하나 상단에 `import resizer`, `import psd_analyzer`, `import layer_object_matcher` 레거시 오염. `/compare`, `/extract-artboard`, `/match-layers` 엔드포인트도 레거시 경유 | legacy 제거 후 상단 import 3개 + 레거시 엔드포인트 제거 | HIGH (현재 운영 중) |
| `docker-compose.yml` | YAML | **MIGRATE** | YES | NO | 배포 | clean_v1 서비스 정의 포함하나 `BACKGROUND_AI_API_KEY`, `UNIFIED_PIPELINE_V2_ENABLED=true` 등 레거시 환경변수 잔존 | legacy env var 제거 | MEDIUM |

### C. PSD 유틸리티 (UNKNOWN — 4개)

> Java `BannerService.analyzePsd()` → Worker `/analyze-psd`,  
> `PsdObjectAnalysisService` → Worker `/extract-artboard`, `/match-layers`  
> 이 호출 체인이 clean_v1 배포에서도 유지될지 결정 필요.

| path | type | classification | runtimeUsed | testOnly | referencedBy | reason | action | risk |
|------|------|----------------|-------------|----------|--------------|--------|--------|------|
| `worker/psd_analyzer.py` | Python | **UNKNOWN** | MAYBE | NO | app.py `/analyze-psd` → Java BannerService | PSD 업로드 시 아트보드 분석. clean_v1에서도 PSD를 받을 경우 필요 | PSD 분석 기능 유지 여부 결정 후 KEEP/DELETE | HIGH |
| `worker/psd_compat.py` | Python | **UNKNOWN** | MAYBE | NO | psd_analyzer.py | psd_analyzer 헬퍼 | psd_analyzer와 동일 결정 | HIGH |
| `worker/psd_layer_parser.py` | Python | **UNKNOWN** | MAYBE | NO | app.py `/extract-artboard` → Java PsdObjectAnalysisService | PSD 레이어 파싱. Object Analysis 기능 유지 여부에 따라 결정 | Object Analysis 기능 유지 여부 결정 후 KEEP/DELETE | MEDIUM |
| `worker/layer_object_matcher.py` | Python | **UNKNOWN** | MAYBE | NO | app.py `/match-layers` → Java PsdObjectAnalysisService | 레이어-객체 매칭. Object Analysis 기능 유지 여부에 따라 결정 | 위와 동일 | MEDIUM |

### D. Worker 레거시 최상위 모듈 (DELETE — 21개, ~8,424줄)

| path | lines | classification | runtimeUsed | referencedBy | reason | risk |
|------|-------|----------------|-------------|--------------|--------|------|
| `worker/resizer.py` | 3,718 | **DELETE** | NO | app.py (legacy 분기만) | Stage E 이전 메인 파이프라인. clean_v1에서 절대 진입 불가 | LOW |
| `worker/ai_render_context.py` | 213 | **DELETE** | NO | resizer.py | AI 렌더 컨텍스트 — resizer 전용 | LOW |
| `worker/background_builder.py` | 498 | **DELETE** | NO | resizer.py | 배경 빌더 — resizer 전용 | LOW |
| `worker/creative_object_extractor.py` | 1,030 | **DELETE** | NO | resizer.py | 객체 추출기 — resizer 전용 | LOW |
| `worker/debug_overlay.py` | 494 | **DELETE** | NO | resizer.py | 디버그 오버레이 — resizer 전용 | LOW |
| `worker/external_mask_selector.py` | 217 | **DELETE** | NO | resizer.py | 외부 마스크 선택기 — resizer 전용 | LOW |
| `worker/external_segmentation_client.py` | 178 | **DELETE** | NO | resizer.py | GDINO+SAM2 클라이언트 — resizer 전용 | LOW |
| `worker/inpaint_outpaint_poc.py` | 436 | **DELETE** | NO | resizer.py | Inpaint PoC — resizer 전용 | LOW |
| `worker/layer_compositor.py` | 107 | **DELETE** | NO | resizer.py | 레이어 합성기 — resizer 전용 | LOW |
| `worker/layer_reflow_engine.py` | 234 | **DELETE** | NO | resizer.py | 레이어 리플로우 엔진 — resizer 전용 | LOW |
| `worker/layer_role_classifier.py` | 261 | **DELETE** | NO | resizer.py | 레이어 역할 분류기 — resizer 전용 | LOW |
| `worker/layout_compiler.py` | 1,690 | **DELETE** | NO | resizer.py | 레이아웃 컴파일러 — resizer 전용 | LOW |
| `worker/layout_compositor.py` | 332 | **DELETE** | NO | resizer.py | 레이아웃 합성기 — resizer 전용 | LOW |
| `worker/mask_utils.py` | 282 | **DELETE** | NO | resizer.py, verdict/ | 마스크 유틸 — legacy 전용 | LOW |
| `worker/object_map_applicator.py` | 306 | **DELETE** | NO | resizer.py | Object Map 적용기 — resizer 전용 | LOW |
| `worker/object_reflow_compositor.py` | 135 | **DELETE** | NO | resizer.py | Object Reflow 합성기 — resizer 전용 | LOW |
| `worker/object_reflow_engine.py` | 112 | **DELETE** | NO | resizer.py | Object Reflow 엔진 — resizer 전용 | LOW |
| `worker/object_source_resolver.py` | 117 | **DELETE** | NO | resizer.py | 객체 소스 리졸버 — resizer 전용 | LOW |
| `worker/psd_layer_reflow.py` | 119 | **DELETE** | NO | resizer.py | PSD 레이어 리플로우 — resizer 전용 | LOW |
| `worker/safe_zone.py` | 287 | **DELETE** | NO | resizer.py, layout_compositor | 레거시 safe zone (clean_pipeline은 자체 `clean_pipeline/layout/safe_zone.py` 사용) | LOW |
| `worker/segmentation_poc.py` | 378 | **DELETE** | NO | resizer.py | Segmentation PoC — resizer 전용 | LOW |
| `worker/stage21_golden_batch.py` | 998 | **DELETE** | NO | 수동 실행 스크립트 | Stage 21 배치 테스트 도구 — legacy | LOW |

### E. Worker 레거시 패키지 (DELETE — 92개 파일, ~17,825줄)

| package | files | lines | classification | reason | risk |
|---------|-------|-------|----------------|--------|------|
| `worker/background/` | 16 | 4,885 | **DELETE** | 배경 생성 Stage E 전용. clean_pipeline은 자체 `clean_pipeline/scene/` 사용 | LOW |
| `worker/foreground/` | 9 | 1,343 | **DELETE** | 전경 추출 Stage 21 전용. clean_pipeline은 자체 `clean_pipeline/extraction/` 사용 | LOW |
| `worker/layout/` | 8 | 1,648 | **DELETE** | 레거시 레이아웃 (worker/layout/ ≠ clean_pipeline/layout/). resizer 전용 | LOW |
| `worker/scene_cleanup/` | 16 | 2,195 | **DELETE** | 배경 정리 Stage D 전용. clean_pipeline이 대체함 | LOW |
| `worker/typography/` | 12 | 1,792 | **DELETE** | 타이포그래피 Stage 20 전용. clean_pipeline 미사용 | LOW |
| `worker/unified_v2/` | 6 | 891 | **DELETE** | Stage E 통합 파이프라인. `UNIFIED_PIPELINE_V2_ENABLED` 분기만 진입 | LOW |
| `worker/verdict/` | 15 | 3,602 | **DELETE** | Stage C1 판정 모델. resizer/unified_v2 전용 | LOW |
| `worker/virtual_foreground/` | 10 | 1,469 | **DELETE** | Stage D-2 가상 전경. resizer 전용 | LOW |
| **합계** | **92** | **17,825** | — | — | — |

### F. Worker 테스트 파일 (DELETE — 47개, ~22,202줄)

> 모두 레거시 모듈(`scene_cleanup`, `verdict`, `foreground`, `background`, `typography`, `resizer`, Stage E/21/20/19/18/17/16/11) 테스트.  
> clean_pipeline 자체 테스트는 `clean_pipeline/` 내부 또는 미커밋 파일에 있음.

| path | classification | 테스트 대상 | 기존 실패 여부 | risk |
|------|----------------|------------|--------------|------|
| `worker/test_ai_only_rendering.py` | **DELETE** | AI only rendering (Stage 20.3) | — | LOW |
| `worker/test_background_plate.py` | **DELETE** | background_plate_builder | — | LOW |
| `worker/test_candidate_score_d3.py` | **DELETE** | D-3 semantic routing | — | LOW |
| `worker/test_clean_pipeline_foundation.py` | **UNKNOWN** | clean_pipeline (미커밋) | COLLECT ERROR: `JobResult` import 오류 | — |
| `worker/test_decorative_composition_policy.py` | **DELETE** | decorative_policy (legacy) | — | LOW |
| `worker/test_diagnostic_logging.py` | **DELETE** | verdict.diagnostic_logger | 4 FAIL (기존) | LOW |
| `worker/test_external_segmentation.py` | **DELETE** | external_segmentation_client | — | LOW |
| `worker/test_external_segmentation_smoke.py` | **DELETE** | external_segmentation_client | — | LOW |
| `worker/test_foreground_compositor.py` | **DELETE** | foreground compositor | — | LOW |
| `worker/test_foreground_mask_quality.py` | **DELETE** | foreground mask quality | — | LOW |
| `worker/test_golden_psd.py` | **DELETE** | PSD golden set (legacy path) | — | LOW |
| `worker/test_layout_repair.py` | **DELETE** | layout repair (Stage 9) | — | LOW |
| `worker/test_pixel_restore_integrity.py` | **DELETE** | pixel_restorer (Stage E P0) | — | LOW |
| `worker/test_reflow_safezone.py` | **DELETE** | layer reflow safe zone | — | LOW |
| `worker/test_render_provenance.py` | **DELETE** | render provenance (Stage 20.3) | sys.exit — pytest INTERNALERROR | LOW |
| `worker/test_role_contradiction_d3.py` | **DELETE** | role contradiction (D-3) | — | LOW |
| `worker/test_semantic_default_routing.py` | **DELETE** | semantic routing (D-3) | — | LOW |
| `worker/test_semantic_inventory.py` | **DELETE** | semantic inventory | — | LOW |
| `worker/test_semantic_scene_cleanup.py` | **DELETE** | scene_cleanup semantic | — | LOW |
| `worker/test_stage11_quality_v3.py` | **DELETE** | Stage 11 competitor templates | — | LOW |
| `worker/test_stage14_15.py` | **DELETE** | Stages 14-15 product candidate | — | LOW |
| `worker/test_stage16_17.py` | **DELETE** | Stages 16-17 segmentation/inpaint | — | LOW |
| `worker/test_stage19.py` | **DELETE** | Stage 19 background pipeline | — | LOW |
| `worker/test_stage20_2.py` | **DELETE** | Stage 20 typography | — | LOW |
| `worker/test_stage20_actual_ai.py` | **DELETE** | Stage 20 actual AI call | — | LOW |
| `worker/test_stage20_typography.py` | **DELETE** | typography pipeline | — | LOW |
| `worker/test_stage21_verdict_c1.py` | **DELETE** | verdict C1 | — | LOW |
| `worker/test_stage_e1_full_image_authority.py` | **DELETE** | Stage E1 | — | LOW |
| `worker/test_stage_e2_unified_semantic_manifest.py` | **DELETE** | Stage E2 manifest | — | LOW |
| `worker/test_stage_e3_no_legacy_fallback.py` | **DELETE** | Stage E3 | — | LOW |
| `worker/test_stage_e4_fail_closed_visual.py` | **DELETE** | Stage E4 | — | LOW |
| `worker/test_stage_e_cross_format_golden.py` | **DELETE** | Stage E cross-format | — | LOW |
| `worker/test_stage_e_p0_authority_sha.py` | **DELETE** | Stage E P0 | — | LOW |
| `worker/test_stage_e_p0_cache_preflight.py` | **DELETE** | Stage E P0 | — | LOW |
| `worker/test_stage_e_p0_immutable_mask.py` | **DELETE** | Stage E P0 | — | LOW |
| `worker/test_stage_e_p0_sequence.py` | **DELETE** | Stage E P0 | — | LOW |
| `worker/test_stage_e_p1_layout_groups.py` | **DELETE** | Stage E P1 | — | LOW |
| `worker/test_stage_e_p1_outpaint_transform.py` | **DELETE** | Stage E P1 | — | LOW |
| `worker/test_stage_e_p1_retry_legacy.py` | **DELETE** | Stage E P1 | — | LOW |
| `worker/test_stage_e_p1_visual_metrics.py` | **DELETE** | Stage E P1 | — | LOW |
| `worker/test_stage_e_production_wiring.py` | **DELETE** | Stage E production wiring | 9 FAIL (기존) | LOW |
| `worker/test_stage_e_risk_hardening.py` | **DELETE** | Stage E risk hardening | — | LOW |
| `worker/test_subject_preserving_transform.py` | **DELETE** | scene_cleanup.models | — | LOW |
| `worker/test_success_count_alignment.py` | **DELETE** | verdict.diagnostic_logger | — | LOW |
| `worker/test_timeout_policy.py` | **DELETE** | timeout policy | sys.exit — pytest INTERNALERROR | LOW |
| `worker/test_unified_manifest_contract.py` | **DELETE** | verdict.models | — | LOW |
| `worker/test_virtual_foreground_extraction.py` | **DELETE** | virtual_foreground | 2 FAIL (기존) | LOW |

### G. Scripts (DELETE 대다수, KEEP 일부)

| path | classification | reason | risk |
|------|----------------|--------|------|
| `scripts/Dockerfile.smoke-runner` | **DELETE** | Stage 8 스모크 전용 Docker | LOW |
| `scripts/check_clean_pipeline_boundary.py` | **KEEP** | 미커밋. clean_v1 경계 검증 — 유용한 진단 도구 | — |
| `scripts/export_psd_object_analysis.sh` | **DELETE** | Stage 21 Object Analysis 내보내기 — legacy | LOW |
| `scripts/http_smoke_test.py` | **DELETE** | Stage 8 HTTP 스모크 — legacy | LOW |
| `scripts/run-stage8-smoke-server.sh` | **DELETE** | Stage 8 전용 | LOW |
| `scripts/run-stage8-smoke.ps1` | **DELETE** | Stage 8 전용 | LOW |
| `scripts/run-stage8-smoke.sh` | **DELETE** | Stage 8 전용 | LOW |
| `scripts/run_all_smoke.sh` | **DELETE** | Stage 8 스모크 묶음 실행 | LOW |
| `scripts/seed_naver_specs.py` | **KEEP** | 배너 규격 DB 시드 — clean_v1 배포에도 필요 | LOW |
| `scripts/stage19_golden_test.py` | **DELETE** | Stage 19 전용 | LOW |
| `scripts/stage20_actual_ai_golden.py` | **DELETE** | Stage 20 전용 | LOW |
| `scripts/stage20_golden_test.py` | **DELETE** | Stage 20 전용 | LOW |
| `scripts/test_verify_stage18_nvidia_count.sh` | **DELETE** | Stage 18 전용 | LOW |
| `scripts/verify_stage18_server.py` | **DELETE** | Stage 18 전용 | LOW |
| `scripts/verify_stage18_server.sh` | **DELETE** | Stage 18 전용 | LOW |
| `scripts/verify_stage19_server.py` | **DELETE** | Stage 19 전용 | LOW |
| `scripts/verify_stage19_server.sh` | **DELETE** | Stage 19 전용 | LOW |
| `scripts/verify_stage20_actual_ai_golden.sh` | **DELETE** | Stage 20 전용 | LOW |
| `scripts/verify_stage20_server.py` | **DELETE** | Stage 20 전용 | LOW |
| `scripts/verify_stage20_server.sh` | **DELETE** | Stage 20 전용 | LOW |
| `scripts/verify_stage21_final.sh` | **DELETE** | Stage 21 전용 | LOW |
| `scripts/verify_stage21_golden_psd_batch.sh` | **DELETE** | Stage 21 전용 | LOW |
| `scripts/worker_contract_smoke_test.py` | **MIGRATE** | Worker HTTP 계약 스모크 — clean_v1 엔드포인트 커버 부분 유지, legacy 엔드포인트 제거 | LOW |

### H. Services (DELETE — 1개)

| path | classification | reason | risk |
|------|----------------|--------|------|
| `services/segmentation-ai/` | **DELETE** | GDINO+SAM2 외부 AI 서비스 (Flask 8090). clean_v1 호출 경로에 전혀 없음. external_segmentation_client.py 통해서만 접근하며 그 파일도 삭제 대상 | LOW |

### I. Java (src/main/) — 전체 KEEP + 일부 UNKNOWN

| path | classification | reason | risk |
|------|----------------|--------|------|
| `src/main/java/com/h3/creative/service/BannerService.java` | **KEEP** | clean_v1 핵심 서비스 | — |
| `src/main/java/com/h3/creative/service/BannerCompareService.java` | **UNKNOWN** | Worker `/compare` 엔드포인트 → `resizer.generate_candidates()` 호출. resizer 삭제 시 이 서비스도 불필요해짐 | MEDIUM |
| `src/main/java/com/h3/creative/service/BannerAnalysisService.java` | **UNKNOWN** | AI 분석 서비스. clean_v1 직접 경로에 없음 — 리뷰 필요 | MEDIUM |
| `src/main/java/com/h3/creative/service/PsdObjectAnalysisService.java` | **UNKNOWN** | Worker `/extract-artboard`, `/match-layers` 호출. psd_layer_parser.py, layer_object_matcher.py에 의존. PSD Object Analysis 기능 유지 여부 결정 필요 | MEDIUM |
| `src/main/java/com/h3/creative/client/OpenAiChatClient.java` | **UNKNOWN** | BannerAnalysisService에서 사용 — 그 서비스 결정에 연동 | LOW |
| `src/main/java/com/h3/creative/dto/OpenAiCallResult.java` | **UNKNOWN** | OpenAiChatClient 결과 DTO — 위와 동일 | LOW |
| `src/main/java/com/h3/creative/controller/SmokeController.java` | **DELETE** | `@Profile("smoke")` 한정. 운영 배포에 포함되면 안 됨 | LOW |
| 나머지 모든 `src/main/` | **KEEP** | BannerController, WorkerClient, Domain, Config, Queue 등 | — |

### J. Frontend (frontend/src/) — 전체 KEEP

| path | classification | reason |
|------|----------------|--------|
| `frontend/src/` 전체 | **KEEP** | Vue3 프론트엔드. clean_v1 파이프라인과 독립적으로 유지 |

### K. Docker/Config/문서 (KEEP 대다수)

| path | classification | reason | risk |
|------|----------------|--------|------|
| `Dockerfile` (root, Java) | **KEEP** | Java 빌드 | — |
| `worker/Dockerfile` | **KEEP** | Worker Python 빌드 | — |
| `worker/Dockerfile.verify` | **DELETE** | Stage 20 검증 전용 Dockerfile | LOW |
| `worker/requirements.txt` | **KEEP** | 의존성 목록 (legacy 패키지 제거 후 정리 가능) | — |
| `worker/requirements-stage19-verify.txt` | **DELETE** | Stage 19 검증 전용 | LOW |
| `worker/conftest.py` | **KEEP** | pytest 루트 설정 | — |
| `docker-compose.yml` | **MIGRATE** | 위 섹션 B 참고 | — |
| `docker-compose.external-ai.yml` | **DELETE** | segmentation-ai 서비스 전용. clean_v1 미사용 | LOW |
| `.env.example` | **KEEP** | 환경변수 템플릿 | — |
| `CHECKPOINT_BANNERSPEC_STAGE8.md` | **DELETE** | Stage 8 체크포인트 기록 — 역사적 아티팩트 | LOW |

### L. 미커밋 파일 (Untracked)

| path | classification | reason | action |
|------|----------------|--------|--------|
| `worker/_test_tmp_stage16/` | **DELETE** | Stage 16 임시 테스트 출력 디렉토리 | .gitignore에 추가하거나 삭제 |
| `worker/nul` | **DELETE** | `> nul` Windows 리다이렉션 오류로 생성된 파일 | 삭제 |
| `worker/probe_d2.json` | **DELETE** | 테스트 프로브 출력 | 삭제 |
| `worker/probe_output.json` | **DELETE** | 테스트 프로브 출력 | 삭제 |
| `worker/test_clean_pipeline_foundation.py` | **UNKNOWN** | clean_pipeline 테스트 (미커밋). `from clean_pipeline.contracts import JobResult` import 오류 — `JobResult`가 contracts.py에 없음 | 수정 후 커밋하거나 삭제 |
| `scripts/check_clean_pipeline_boundary.py` | **KEEP** | 경계 검증 도구 | 커밋 대상 |

---

## 6. test_ 파일 분류 요약

| 분류 | 개수 | 설명 |
|------|------|------|
| DELETE (레거시 테스트) | 46개 | scene_cleanup, verdict, foreground, background, typography, resizer, Stage E/21/20/19/18/17/16/11/9 테스트 |
| UNKNOWN (미커밋) | 1개 | `test_clean_pipeline_foundation.py` — import 오류 수정 필요 |
| 기존 실패 (삭제 전 인지) | 15개 | `test_stage_e_production_wiring` 9건, `test_diagnostic_logging` 4건, `test_virtual_foreground_extraction` 2건 |
| pytest 수집 오류 | 2개 | `test_render_provenance.py`, `test_timeout_policy.py` — 모듈 레벨 `sys.exit()` |

**clean_pipeline 자체 테스트 현황**: `worker/clean_pipeline/` 내에 `test_*.py` 없음. 현재 clean_v1 동작을 보호하는 자동화된 단위 테스트 부재.  
→ 클린업 전에 clean_pipeline 단위 테스트 작성을 권장.

---

## 7. legacy 이미지 처리 후보 (삭제 시 잃는 기능)

삭제하면 더 이상 사용할 수 없는 기능:

| 기능 | 관련 코드 | 비고 |
|------|-----------|------|
| `/compare` 엔드포인트 (다중 후보 비교) | `resizer.generate_candidates()` + `BannerCompareService.java` | clean_v1 대안 없음 |
| Object Analysis 인스펙터 (PSD 객체 감지·시각화) | `psd_layer_parser.py`, `layer_object_matcher.py`, `PsdObjectAnalysisService.java` | clean_v1 대안 없음 |
| PSD 아트보드 추출 미리보기 (`/extract-artboard`) | `psd_layer_parser.py` | clean_v1 대안 없음 |
| 다중 후보 AI 비교 (safe/balanced/fill) | `resizer.generate_candidates()` | clean_v1 대안 없음 |
| 타이포그래피 파이프라인 (Stage 20) | `worker/typography/` | clean_v1이 P7 레이아웃으로 대체 |

---

## 8. 삭제 시 위험도가 높은 파일

| path | risk | 이유 |
|------|------|------|
| `worker/app.py` | **HIGH** | 운영 중인 Flask 서버 진입점. 레거시 코드 제거 시 clean_v1 엔드포인트 훼손 위험 |
| `worker/psd_analyzer.py` | **HIGH** | 미결정: clean_v1 PSD 업로드 시 필요 여부 확인 전 삭제 금지 |
| `worker/psd_compat.py` | **HIGH** | psd_analyzer 의존 — 위와 동일 |
| `docker-compose.yml` | **MEDIUM** | 레거시 env var 제거 시 컨테이너 기동 오류 가능성 |
| `src/main/.../BannerCompareService.java` | **MEDIUM** | `/compare` 기능 완전 포기 확인 후 삭제 |
| `src/main/.../PsdObjectAnalysisService.java` | **MEDIUM** | Object Analysis 기능 완전 포기 확인 후 삭제 |

---

## 9. 동적 호출 때문에 판단 불가한 파일

| path | 불확실한 이유 |
|------|-------------|
| `worker/psd_analyzer.py` | Java가 PSD 업로드 시 `/analyze-psd`를 호출하는데, clean_v1 배포에서 PSD 입력을 받을 경우 여전히 필요할 수 있음 |
| `worker/psd_compat.py` | psd_analyzer 동적 의존 |
| `worker/psd_layer_parser.py` | `/extract-artboard` 엔드포인트가 clean_v1 이후에도 유지될지 미결 |
| `worker/layer_object_matcher.py` | `/match-layers` 엔드포인트가 clean_v1 이후에도 유지될지 미결 |
| `src/main/.../BannerCompareService.java` | `/compare` 기능 유지 정책 미결 |
| `src/main/.../PsdObjectAnalysisService.java` | Object Analysis 기능 유지 정책 미결 |
| `src/main/.../OpenAiChatClient.java` | BannerAnalysisService 유지 여부 미결 |

---

## 10. 다음 단계에서 먼저 보호해야 할 테스트

> 실제 DELETE 단계 진행 전, 아래를 선 확보해야 clean_v1 퇴행 감지 가능.

1. **clean_pipeline 단위 테스트 부재** — `clean_pipeline/` 내에 test 없음. P1-P8 각 단계의 단위 테스트를 최소 1개씩 작성해야 함.
2. **E2E 스모크 테스트** — 실제 이미지 업로드 → /generate 호출 → 결과 확인 자동화 필요. `scripts/worker_contract_smoke_test.py`를 clean_v1 대상으로 수정해 살릴 수 있음.
3. **`test_clean_pipeline_foundation.py`** — 미커밋 상태. `JobResult` import 오류 수정 후 커밋하면 clean_v1 foundation 테스트로 사용 가능.
4. **pre-deletion 스냅샷** — 삭제 전 `git tag pre-clean-v1-legacy-removal HEAD`로 복원점 보존 (사용자 승인 필요).

---

## 11. 산출물

이 문서 경로: **`docs/clean-v1-cleanup-inventory.md`**

---

## 통계 요약

| 분류 | 파일 수 | 대략 줄 수 |
|------|---------|-----------|
| KEEP (clean_pipeline) | 50 | ~4,145 |
| KEEP (기타 — Java, Frontend, Config, Scripts) | ~200+ | — |
| MIGRATE | 3 | — |
| DELETE (레거시 패키지) | 92 | ~17,825 |
| DELETE (레거시 최상위 모듈) | 22 | ~12,142 (resizer 포함) |
| DELETE (테스트) | 46 | ~22,202 |
| DELETE (scripts) | 19 | — |
| DELETE (services/segmentation-ai) | — | ~10,884 |
| DELETE (Docker/docs 잡파일) | 3 | — |
| **DELETE 소계** | **182+** | **~63,053** |
| UNKNOWN | 10 | ~911 (PSD 유틸) + Java 6개 |

> 삭제 가능한 Python 코드만 약 **63,000줄** 이상.  
> 삭제 전 UNKNOWN 10개 파일에 대한 정책 결정이 선행되어야 한다.
