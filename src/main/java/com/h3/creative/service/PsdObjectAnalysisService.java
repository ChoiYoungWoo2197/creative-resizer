package com.h3.creative.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.h3.creative.domain.PsdObjectAnalysis;
import com.h3.creative.mongo.PsdObjectAnalysisMongoService;
import com.h3.creative.worker.WorkerClient;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Service;
import org.springframework.web.multipart.MultipartFile;

import java.io.File;
import java.io.IOException;
import java.time.LocalDateTime;
import java.util.*;

@Slf4j
@Service
@RequiredArgsConstructor
public class PsdObjectAnalysisService {

    private final OpenAiChatClient openAiChatClient;
    private final WorkerClient workerClient;
    private final PsdObjectAnalysisMongoService mongoService;
    private final ObjectMapper objectMapper;

    @Value("${creative.storage.upload-dir}")
    private String uploadDir;

    @Value("${creative.openai.api-key:}")
    private String openAiApiKey;

    @Value("${creative.openai.object-model:gpt-5-mini}")
    private String openAiObjectModel;

    @Value("${creative.openai.fallback-model:gpt-4.1-mini}")
    private String openAiFallbackModel;

    @Value("${creative.openai.image-detail:high}")
    private String openAiImageDetail;

    private static final Set<String> VALID_ROLES =
            Set.of("background", "title", "body_text", "main_image", "cta", "logo", "badge", "decoration", "unknown");
    private static final Set<String> VALID_IMPORTANCES =
            Set.of("required", "priority", "optional");
    private static final Set<String> VALID_REFLOW_BEHAVIORS =
            Set.of("fill_canvas", "keep_aspect", "move_only", "optional_drop");
    private static final Set<String> REQUIRED_ROLES = Set.of("background", "title", "main_image");

    @SuppressWarnings("unchecked")
    public PsdObjectAnalysis analyze(MultipartFile psdFile, String selectedArtboardId,
                                     int abX, int abY, int abW, int abH) throws IOException {
        if (openAiApiKey == null || openAiApiKey.isBlank()) {
            throw new IllegalStateException("OpenAI API Key가 설정되지 않았습니다.");
        }

        // PSD 파일 임시 저장
        String filename = UUID.randomUUID() + "_" + psdFile.getOriginalFilename();
        File dest = new File(uploadDir, filename);
        dest.getParentFile().mkdirs();
        psdFile.transferTo(dest);
        log.info("PSD 객체 분석 시작: file={} artboard={}x{}", filename, abW, abH);

        try {
            Map<String, Integer> artboardBox = new LinkedHashMap<>();
            artboardBox.put("x", abX);
            artboardBox.put("y", abY);
            artboardBox.put("width", abW);
            artboardBox.put("height", abH);

            // 1. Worker: 아트보드 프리뷰 + 레이어 추출
            Map<String, Object> extracted = workerClient.extractArtboard(dest.getAbsolutePath(), artboardBox);
            if (extracted.containsKey("error")) {
                throw new IllegalStateException("Worker extract-artboard 실패: " + extracted.get("error"));
            }

            String previewBase64 = (String) extracted.get("previewBase64");
            List<Map<String, Object>> layers = (List<Map<String, Object>>) extracted.getOrDefault("layers", List.of());
            int canvasW = extracted.get("canvasWidth") instanceof Number
                    ? ((Number) extracted.get("canvasWidth")).intValue() : abW;
            int canvasH = extracted.get("canvasHeight") instanceof Number
                    ? ((Number) extracted.get("canvasHeight")).intValue() : abH;

            // 2. OpenAI: 객체 맵 분석
            List<Map<String, Object>> aiObjects = callOpenAiObjectMap(previewBase64, abW, abH);
            log.info("OpenAI 객체 분석 완료: {}개 객체", aiObjects.size());

            // 3. Worker: 레이어 매칭
            Map<String, Object> matchResult = workerClient.matchLayers(aiObjects, layers, artboardBox, canvasW, canvasH);
            List<Map<String, Object>> matchedObjects = (List<Map<String, Object>>) matchResult.getOrDefault("matchedObjects", aiObjects);

            // 4. 결과 빌드
            List<PsdObjectAnalysis.ObjectResult> objectResults = parseObjectResults(matchedObjects);
            boolean reflowReady = computeReflowReady(objectResults);
            List<String> missingRoles = computeMissingRequiredRoles(objectResults);

            PsdObjectAnalysis doc = new PsdObjectAnalysis();
            doc.setPsdPath(dest.getAbsolutePath());
            doc.setSelectedArtboardId(selectedArtboardId);
            doc.setArtboardBox(artboardBox);
            doc.setCanvasWidth(canvasW);
            doc.setCanvasHeight(canvasH);
            doc.setObjects(objectResults);
            doc.setReflowReady(reflowReady);
            doc.setMissingRequiredRoles(missingRoles);
            doc.setCreatedAt(LocalDateTime.now());
            doc.setPreviewBase64(previewBase64);  // @Transient — 저장 안 됨

            return mongoService.save(doc);
        } finally {
            // temp PSD 삭제
            if (dest.exists()) dest.delete();
        }
    }

    @SuppressWarnings("unchecked")
    private List<Map<String, Object>> callOpenAiObjectMap(String previewBase64, int width, int height) throws IOException {
        String prompt = buildObjectMapPrompt(width, height);
        String dataUrl = "data:image/jpeg;base64," + previewBase64;

        Map<String, Object> imageContent = new LinkedHashMap<>();
        imageContent.put("type", "image_url");
        imageContent.put("image_url", Map.of("url", dataUrl, "detail", openAiImageDetail));

        Map<String, Object> textContent = new LinkedHashMap<>();
        textContent.put("type", "text");
        textContent.put("text", prompt);

        Map<String, Object> message = new LinkedHashMap<>();
        message.put("role", "user");
        message.put("content", List.of(imageContent, textContent));

        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);
        headers.setBearerAuth(openAiApiKey);

        log.info("OpenAI 객체 분석 요청: openAiRequestedModel={}", openAiObjectModel);
        OpenAiCallResult callResult = openAiChatClient.call(
                openAiObjectModel, openAiFallbackModel, headers, message, 2500,
                Map.of("response_format", Map.of("type", "json_object")));

        log.info("OpenAI 객체 분석 성공: openAiRequestedModel={} openAiUsedModel={} openAiFallbackUsed={} " +
                 "openAiFallbackReason={} openAiTokenParameter={} openAiTokenLimit={}",
                callResult.getRequestedModel(), callResult.getUsedModel(), callResult.isFallbackUsed(),
                callResult.getFallbackReason(), callResult.getTokenParameter(), callResult.getTokenLimit());
        log.info("OpenAI 객체 분석 성공 model={}", callResult.getUsedModel());

        Map<String, Object> body = callResult.getBody();
        if (body == null) throw new IllegalStateException("OpenAI 응답이 비어있습니다.");

        List<Map<String, Object>> choices = (List<Map<String, Object>>) body.get("choices");
        if (choices == null || choices.isEmpty()) throw new IllegalStateException("OpenAI choices가 없습니다.");

        Map<String, Object> msgResp = (Map<String, Object>) choices.get(0).get("message");
        String content = (String) msgResp.get("content");
        log.info("OpenAI 객체 맵 응답 raw length: {}", content != null ? content.length() : "null");

        // Guard: empty or null response — return empty list instead of 500 MismatchedInputException
        if (content == null || content.trim().isEmpty()) {
            log.warn("OBJECT_ANALYSIS_EMPTY_RESPONSE: OpenAI returned null or empty content");
            return List.of();
        }

        // Strip code fences if present (e.g. ```json ... ```)
        String jsonContent = content.trim();
        if (jsonContent.startsWith("```")) {
            jsonContent = jsonContent.replaceAll("(?s)^```[a-zA-Z]*\\s*", "").replaceAll("(?s)```\\s*$", "").trim();
        }

        if (!jsonContent.startsWith("{") && !jsonContent.startsWith("[")) {
            log.warn("OBJECT_ANALYSIS_INVALID_JSON_START: content does not start with {{ or [ — content_prefix={}",
                    jsonContent.substring(0, Math.min(50, jsonContent.length())));
            return List.of();
        }

        Map<String, Object> result = objectMapper.readValue(jsonContent, Map.class);
        Object objects = result.get("objects");
        if (!(objects instanceof List)) return List.of();
        return (List<Map<String, Object>>) objects;
    }

    private static String normalizeAiRole(String rawRole) {
        if (rawRole == null) return "unknown";
        switch (rawRole.toLowerCase().trim()) {
            case "visual": case "visual_element": case "product": case "product_image":
            case "image": case "key_visual": case "keyvisual": case "hero":
                return "main_image";
            case "headline": case "header": case "main_copy":
                return "title";
            case "sub_text": case "body": case "copy": case "description":
                return "body_text";
            case "btn": case "button":
                return "cta";
            case "brand":
                return "logo";
            default:
                return rawRole;
        }
    }

    private List<PsdObjectAnalysis.ObjectResult> parseObjectResults(List<Map<String, Object>> raw) {
        List<PsdObjectAnalysis.ObjectResult> results = new ArrayList<>();
        for (Map<String, Object> m : raw) {
            PsdObjectAnalysis.ObjectResult r = new PsdObjectAnalysis.ObjectResult();
            r.setId((String) m.getOrDefault("id", ""));
            String role = normalizeAiRole((String) m.getOrDefault("role", "unknown"));
            r.setRole(VALID_ROLES.contains(role) ? role : "unknown");
            r.setLabel((String) m.getOrDefault("label", ""));
            String importance = (String) m.getOrDefault("importance", "optional");
            r.setImportance(VALID_IMPORTANCES.contains(importance) ? importance : "optional");
            Object bboxRaw = m.get("bbox");
            if (bboxRaw instanceof Map) {
                Map<String, Object> bm = (Map<String, Object>) bboxRaw;
                Map<String, Integer> bbox = new LinkedHashMap<>();
                bbox.put("x", bm.get("x") instanceof Number ? ((Number) bm.get("x")).intValue() : 0);
                bbox.put("y", bm.get("y") instanceof Number ? ((Number) bm.get("y")).intValue() : 0);
                bbox.put("width", bm.get("width") instanceof Number ? ((Number) bm.get("width")).intValue() : 0);
                bbox.put("height", bm.get("height") instanceof Number ? ((Number) bm.get("height")).intValue() : 0);
                r.setBbox(bbox);
            }
            Object conf = m.get("confidence");
            if (conf instanceof Number) r.setConfidence(((Number) conf).doubleValue());
            String rb = (String) m.getOrDefault("reflowBehavior", "keep_aspect");
            r.setReflowBehavior(VALID_REFLOW_BEHAVIORS.contains(rb) ? rb : "keep_aspect");
            Object szr = m.get("safeZoneRequired");
            r.setSafeZoneRequired(szr instanceof Boolean ? (Boolean) szr : Boolean.FALSE);
            Object cs = m.get("canScale");
            r.setCanScale(cs instanceof Boolean ? (Boolean) cs : Boolean.TRUE);
            Object cc = m.get("canCrop");
            r.setCanCrop(cc instanceof Boolean ? (Boolean) cc : Boolean.FALSE);
            r.setRecommendedLayerName((String) m.get("recommendedLayerName"));
            // 매칭 결과
            r.setMatchedLayerId((String) m.get("matchedLayerId"));
            r.setMatchedLayerName((String) m.get("matchedLayerName"));
            Object ms = m.get("matchScore");
            if (ms instanceof Number) r.setMatchScore(((Number) ms).doubleValue());
            r.setMatchStatus((String) m.getOrDefault("matchStatus", "missing_layer"));
            results.add(r);
        }
        return results;
    }

    private boolean computeReflowReady(List<PsdObjectAnalysis.ObjectResult> objects) {
        Set<String> matched = new HashSet<>();
        for (PsdObjectAnalysis.ObjectResult r : objects) {
            if ("ready".equals(r.getMatchStatus()) || "matched_low_confidence".equals(r.getMatchStatus())) {
                matched.add(r.getRole());
            }
        }
        return matched.containsAll(REQUIRED_ROLES);
    }

    private List<String> computeMissingRequiredRoles(List<PsdObjectAnalysis.ObjectResult> objects) {
        Set<String> matched = new HashSet<>();
        for (PsdObjectAnalysis.ObjectResult r : objects) {
            if ("ready".equals(r.getMatchStatus()) || "matched_low_confidence".equals(r.getMatchStatus())) {
                matched.add(r.getRole());
            }
        }
        List<String> missing = new ArrayList<>();
        for (String role : REQUIRED_ROLES) {
            if (!matched.contains(role)) missing.add(role);
        }
        return missing;
    }

    private String buildObjectMapPrompt(int width, int height) {
        return String.format("""
                이 광고 배너 소재 이미지(%dx%dpx)에서 레이아웃 객체를 탐지해줘.
                반환은 반드시 JSON 형식으로만 해줘. 다른 텍스트는 포함하지 마.

                객체 role 종류:
                - background: 배경 이미지/색상 블록 (전체 또는 대부분을 덮는 배경)
                - title: 헤드라인/주제목 텍스트 (가장 크고 중요한 카피)
                - body_text: 부제목/설명 텍스트
                - main_image: 제품/인물/주요 시각 이미지
                - cta: CTA 버튼 또는 클릭 유도 텍스트
                - logo: 브랜드/기업 로고
                - badge: 배지/태그/혜택 라벨 (할인율, 신규 등)
                - decoration: 장식 요소 (선, 아이콘, 패턴 등)
                - unknown: 분류 불가

                각 객체 반환 필드:
                - id: "obj_1" 형태 (순번)
                - role: 위 role 중 하나
                - label: 한국어로 간단한 설명
                - importance: required/priority/optional
                - bbox: 이미지 기준 픽셀 좌표 {x, y, width, height} (정수값)
                - confidence: 0.0~1.0
                - reflowBehavior: fill_canvas/keep_aspect/move_only/optional_drop
                - safeZoneRequired: boolean
                - canScale: boolean
                - canCrop: boolean
                - recommendedLayerName: PSD에서 찾으면 좋을 추천 레이어명

                importance 기준:
                - required: background / 메인 타이틀 / 주요 이미지
                - priority: CTA / 로고 / 가격·할인 정보
                - optional: 장식 요소 / 배경 패턴

                반환 JSON 형식:
                {"objects": [...]}

                주의:
                - bbox는 반드시 이미지 크기(%dx%d) 안에 있어야 함
                - background는 전체 또는 대부분을 덮는 레이어 하나만 지정
                - 객체는 최소 2개 최대 10개
                """, width, height, width, height);
    }
}
