package com.h3.creative.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.h3.creative.domain.BannerAiAnalysis;
import com.h3.creative.mongo.BannerAnalysisMongoService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Service;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;
import java.time.LocalDateTime;
import java.util.Base64;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

@Slf4j
@Service
@RequiredArgsConstructor
public class BannerAnalysisService {

    private final OpenAiChatClient openAiChatClient;
    private final BannerAnalysisMongoService mongoService;
    private final ObjectMapper objectMapper;

    @Value("${creative.openai.api-key:}")
    private String openAiApiKey;

    @Value("${creative.openai.analysis-model:gpt-5-mini}")
    private String openAiAnalysisModel;

    @Value("${creative.openai.fallback-model:gpt-4.1-mini}")
    private String openAiFallbackModel;

    @Value("${creative.openai.image-detail:high}")
    private String openAiImageDetail;

    private static final String PROMPT = """
            이 광고 배너 소재 이미지를 리사이징 관점에서 분석해줘.
            반환은 반드시 JSON 형식으로만 해줘. 다른 텍스트는 포함하지 마.

            [이미지 분석 필드]
            creativeType: text_heavy (텍스트/로고/CTA가 주를 이룸), product_focused (제품/인물이 주를 이룸), balanced_mix (텍스트와 제품이 균형), poster_info (정보형 포스터 — 날짜/기간/설명이 상중하에 분산)
            textDensity: high (텍스트가 많고 빽빽함), medium (적당한 텍스트), low (텍스트 거의 없음)
            edgeRisk: high (가장자리에 중요 요소 있어 잘림 위험 높음), medium (주의 필요), low (잘림 위험 없음)
            mainSubjectPosition: 주요 피사체(제품/인물)가 이미지에서 위치하는 방향 (center, top, bottom, left, right, left-top, right-top, left-bottom, right-bottom)
            mainSubjectDescription: 주요 피사체를 한국어로 한 문장으로 설명

            [추천 설정 필드]
            resizeMode: smart-fit, cover, contain, blur-bg
            smartFitStrength: safe, balanced, fill
            focalPosition: center, top, bottom, left, right, left-top, right-top, left-bottom, right-bottom

            [품질 체크 필드]
            cropRiskAreas: 잘림 위험이 있는 영역 목록 (예: ["우측 상단 로고", "하단 가격 텍스트"]) — 없으면 빈 배열
            recommendedBecause: 추천 설정의 근거 bullet (예: ["제품이 좌측에 치우쳐 left 포커스 적용", "텍스트 밀도가 높아 safe 강도 권장"]) — 2~4항목
            avoidOptions: 이 소재에서 피해야 할 설정 목록 (예: ["fill 강도 — 가장자리 텍스트 잘림 위험", "cover 모드 — 배경 손실 가능"]) — 없으면 빈 배열

            [요소 분석 필드]
            이미지 안의 주요 시각 요소를 분석하세요. 다음 요소 타입을 구분하세요:
            - product (제품 이미지), person (사람/얼굴), text (텍스트/카피), logo (브랜드 로고),
              cta (CTA 버튼/링크), price (가격 정보), discount (할인율/배지), badge (기타 배지),
              decoration (장식 요소), background (배경)

            각 요소에 대해 반환:
            - id: "el_1", "el_2" 형태
            - type: 위 타입 중 하나
            - label: 한국어로 간단히 설명
            - group: main_product / main_copy / sub_copy / price_discount / cta / logo / decorations / background 중 하나
            - importance: required (광고 메시지 전달에 반드시 필요) / priority (가능하면 유지) / optional (잘려도 큰 영향 없음)
            - bbox: 이미지 기준 픽셀 좌표 {x, y, width, height}. 확신이 낮으면 null.

            importance 기준:
            - required: 제품/사람 얼굴/메인 카피/가격·할인 정보
            - priority: CTA 버튼·날짜·신청기간·마감일·기한 텍스트·기관 로고·하단 설명 문구·주요 안내 문구 — 반드시 priority 이상으로 분류
            - optional: 장식 아이콘/배경 패턴/없어도 메시지 전달에 큰 영향 없는 요소

            elementGroups: 요소를 그룹으로 묶어서 반환 (id, name, importance, elementIds)
            requiredGroups: required 그룹 ID 목록
            priorityGroups: priority 그룹 ID 목록
            optionalGroups: optional 그룹 ID 목록

            [포스터 레이아웃 분석]
            이미지가 포스터형/정보형인지 판단하세요.

            포스터형 조건 (아래 대부분 해당하면 poster_info):
            - 텍스트/날짜/기간/안내 정보가 상단·중단·하단에 분산됨
            - 배경이 가로 색상 블록으로 구분됨
            - 날짜·신청기간·마감·기관 로고·CTA 포함
            - 단순 제품 사진이 아니라 정보 전달형 레이아웃

            poster-reflow 모드는 원본 전체 보존을 목표로 하지 않습니다.
            타겟 배너 규격에 맞게 포스터의 핵심 메시지를 재구성하는 것이 목표입니다.
            contentBands를 분석할 때 각 band에 다음 정보를 포함하세요.

            포스터형이면:
            - layoutType: horizontal_bands 또는 poster_info
            - reflowRecommended: true
            - contentBands: 이미지를 y1~y2 기준으로 3~4개 가로 영역으로 분리
              각 band 필드:
              * id: "top_main" / "middle_date" / "bottom_desc" 등
              * name: 한국어 설명
              * role: logo / headline / date_cta / subcopy / decoration / background / visual 중 하나
              * y1, y2: 원본 이미지 픽셀 기준 정수값
              * importance: required / priority / optional
              * reflowPriority: hero (가장 중요 메시지, 배너에서 크게 노출) / support (보조 정보, 공간 있으면 유지) / optional (장식/부가 정보, 공간 부족 시 삭제 가능)
              * canDrop: true/false (공간 부족 시 삭제 가능 여부)
              * canCrop: true/false (일부 crop 가능 여부)
              * targetPlacement: top / center / bottom / left / right (타겟 배너에서 배치 위치)

              reflowPriority 판단 기준:
              - 메인 제목/핵심 카피 → hero
              - 신청기간, 날짜, CTA → support 이상
              - 하단 설명 문구 → optional 또는 support
              - 장식 요소 → optional
              - 기관 로고 → support

            비포스터형(단순 제품/인물 이미지)이면:
            - layoutType: single_subject 또는 product_visual
            - reflowRecommended: false
            - contentBands: []

            판단 기준:
            - 텍스트/로고/가격/CTA가 가장자리에 많으면 edgeRisk high, smartFitStrength safe
            - 제품/인물이 명확하면 product_focused, 해당 위치를 mainSubjectPosition과 focalPosition에 반영
            - 텍스트가 거의 없고 제품 중심이면 fill 가능
            - 잘림 위험이 높으면 smart-fit safe 또는 contain 추천
            - focalPosition은 mainSubjectPosition과 동일하게 맞추는 것을 우선으로 함
            - textDensity=high 이고 edgeRisk=high 이면 → focalPosition=center 우선 (텍스트 분산 보호)
            - creativeType=text_heavy 이면 → smartFitStrength=safe 우선, fill 절대 지양
            - creativeType=product_focused 이고 textDensity=low 이면 → smartFitStrength=balanced 또는 fill 적극 추천
            - 제품이 한쪽에 치우쳐 있어도 텍스트가 이미지 여러 방향에 분산되어 있으면 → focalPosition=center 추천
            - type=cta, type=text(날짜/기간/안내문구 포함), type=logo 는 importance를 priority 이상으로 분류
            - 날짜·신청기간·마감일·기한이 포함된 텍스트 요소는 반드시 priority 또는 required로 분류

            반환 JSON:
            {
              "creativeType": "...",
              "textDensity": "...",
              "edgeRisk": "...",
              "mainSubjectPosition": "...",
              "mainSubjectDescription": "...",
              "resizeMode": "...",
              "smartFitStrength": "...",
              "focalPosition": "...",
              "reason": "한국어로 분석 이유 (1~2문장)",
              "warnings": ["주의사항"],
              "confidence": 0.00,
              "cropRiskAreas": ["..."],
              "recommendedBecause": ["...", "..."],
              "avoidOptions": ["..."],
              "detectedElements": [
                { "id": "el_1", "type": "product", "label": "메인 제품", "group": "main_product", "importance": "required", "bbox": {"x": 0, "y": 0, "width": 100, "height": 100} }
              ],
              "elementGroups": [
                { "id": "main_product", "name": "메인 제품", "importance": "required", "elementIds": ["el_1"] }
              ],
              "requiredGroups": ["main_product"],
              "priorityGroups": ["cta", "logo"],
              "optionalGroups": ["decorations"],
              "layoutType": "single_subject",
              "reflowRecommended": false,
              "contentBands": [
                { "id": "top_main", "name": "메인 타이틀", "role": "headline", "y1": 0, "y2": 300, "importance": "required", "reflowPriority": "hero", "canDrop": false, "canCrop": false, "targetPlacement": "center" },
                { "id": "middle_date", "name": "신청기간", "role": "date_cta", "y1": 300, "y2": 500, "importance": "required", "reflowPriority": "support", "canDrop": false, "canCrop": false, "targetPlacement": "bottom" },
                { "id": "bottom_desc", "name": "설명 문구", "role": "subcopy", "y1": 500, "y2": 700, "importance": "priority", "reflowPriority": "optional", "canDrop": true, "canCrop": true, "targetPlacement": "bottom" }
              ]
            }
            비포스터형이면 contentBands는 빈 배열 []로 반환하세요.
            """;

    private static final java.util.Set<String> VALID_RESIZE_MODES =
            java.util.Set.of("smart-fit", "cover", "contain", "blur-bg");
    private static final java.util.Set<String> VALID_STRENGTHS =
            java.util.Set.of("safe", "balanced", "fill");
    private static final java.util.Set<String> VALID_FOCAL_POSITIONS =
            java.util.Set.of("center", "top", "bottom", "left", "right",
                             "left-top", "right-top", "left-bottom", "right-bottom");
    private static final java.util.Set<String> VALID_CREATIVE_TYPES =
            java.util.Set.of("text_heavy", "product_focused", "balanced_mix", "poster_info");
    private static final java.util.Set<String> VALID_LAYOUT_TYPES =
            java.util.Set.of("single_subject", "product_visual", "poster_info",
                             "horizontal_bands", "vertical_bands", "mixed_layout");
    private static final java.util.Set<String> VALID_BAND_ROLES =
            java.util.Set.of("main_title", "date_info", "description", "product_visual",
                             "sub_copy", "cta", "logo", "decoration",
                             "headline", "date_cta", "subcopy", "background", "visual");
    private static final java.util.Set<String> VALID_REFLOW_PRIORITIES =
            java.util.Set.of("hero", "support", "optional");
    private static final java.util.Set<String> VALID_TARGET_PLACEMENTS =
            java.util.Set.of("top", "center", "bottom", "left", "right");
    private static final java.util.Set<String> VALID_DENSITIES =
            java.util.Set.of("high", "medium", "low");

    private static final java.util.Set<String> VALID_ELEMENT_TYPES =
            java.util.Set.of("product", "person", "text", "logo", "cta", "price", "discount", "badge", "decoration", "background");
    private static final java.util.Set<String> VALID_IMPORTANCES =
            java.util.Set.of("required", "priority", "optional");
    private static final java.util.Set<String> VALID_GROUPS =
            java.util.Set.of("main_product", "main_copy", "sub_copy", "price_discount", "cta", "logo", "decorations", "background");

    @SuppressWarnings("unchecked")
    private List<BannerAiAnalysis.DetectedElement> parseDetectedElements(Map<String, Object> result) {
        Object raw = result.get("detectedElements");
        if (!(raw instanceof List)) return List.of();
        List<Map<String, Object>> list = (List<Map<String, Object>>) raw;
        List<BannerAiAnalysis.DetectedElement> elements = new java.util.ArrayList<>();
        for (Map<String, Object> m : list) {
            BannerAiAnalysis.DetectedElement el = new BannerAiAnalysis.DetectedElement();
            el.setId((String) m.getOrDefault("id", ""));
            String type = (String) m.getOrDefault("type", "");
            el.setType(VALID_ELEMENT_TYPES.contains(type) ? type : "decoration");
            el.setLabel((String) m.getOrDefault("label", ""));
            String group = (String) m.getOrDefault("group", "");
            el.setGroup(VALID_GROUPS.contains(group) ? group : "decorations");
            String importance = (String) m.getOrDefault("importance", "");
            el.setImportance(VALID_IMPORTANCES.contains(importance) ? importance : "optional");
            Object bboxRaw = m.get("bbox");
            if (bboxRaw instanceof Map) {
                Map<String, Object> bm = (Map<String, Object>) bboxRaw;
                BannerAiAnalysis.Bbox bbox = new BannerAiAnalysis.Bbox();
                bbox.setX(bm.get("x") instanceof Number ? ((Number) bm.get("x")).intValue() : 0);
                bbox.setY(bm.get("y") instanceof Number ? ((Number) bm.get("y")).intValue() : 0);
                bbox.setWidth(bm.get("width") instanceof Number ? ((Number) bm.get("width")).intValue() : 0);
                bbox.setHeight(bm.get("height") instanceof Number ? ((Number) bm.get("height")).intValue() : 0);
                el.setBbox(bbox);
            }
            elements.add(el);
        }
        return elements;
    }

    @SuppressWarnings("unchecked")
    private List<BannerAiAnalysis.ElementGroup> parseElementGroups(Map<String, Object> result) {
        Object raw = result.get("elementGroups");
        if (!(raw instanceof List)) return List.of();
        List<Map<String, Object>> list = (List<Map<String, Object>>) raw;
        List<BannerAiAnalysis.ElementGroup> groups = new java.util.ArrayList<>();
        for (Map<String, Object> m : list) {
            BannerAiAnalysis.ElementGroup g = new BannerAiAnalysis.ElementGroup();
            g.setId((String) m.getOrDefault("id", ""));
            g.setName((String) m.getOrDefault("name", ""));
            String importance = (String) m.getOrDefault("importance", "");
            g.setImportance(VALID_IMPORTANCES.contains(importance) ? importance : "optional");
            Object ids = m.get("elementIds");
            g.setElementIds(ids instanceof List ? (List<String>) ids : List.of());
            groups.add(g);
        }
        return groups;
    }

    @SuppressWarnings("unchecked")
    private List<BannerAiAnalysis.ContentBand> parseContentBands(Map<String, Object> result) {
        Object raw = result.get("contentBands");
        if (!(raw instanceof List)) return List.of();
        List<Map<String, Object>> list = (List<Map<String, Object>>) raw;
        List<BannerAiAnalysis.ContentBand> bands = new java.util.ArrayList<>();
        for (Map<String, Object> m : list) {
            Object y1 = m.get("y1");
            Object y2 = m.get("y2");
            if (!(y1 instanceof Number) || !(y2 instanceof Number)) continue;
            int yi1 = ((Number) y1).intValue();
            int yi2 = ((Number) y2).intValue();
            if (yi2 <= yi1) continue;
            BannerAiAnalysis.ContentBand band = new BannerAiAnalysis.ContentBand();
            band.setId((String) m.getOrDefault("id", ""));
            band.setName((String) m.getOrDefault("name", ""));
            String role = (String) m.getOrDefault("role", "");
            band.setRole(VALID_BAND_ROLES.contains(role) ? role : "description");
            band.setY1(yi1);
            band.setY2(yi2);
            String importance = (String) m.getOrDefault("importance", "");
            band.setImportance(VALID_IMPORTANCES.contains(importance) ? importance : "priority");
            String reflowPriority = (String) m.getOrDefault("reflowPriority", "");
            band.setReflowPriority(VALID_REFLOW_PRIORITIES.contains(reflowPriority) ? reflowPriority : "support");
            Object canDrop = m.get("canDrop");
            band.setCanDrop(canDrop instanceof Boolean ? (Boolean) canDrop : Boolean.FALSE);
            Object canCrop = m.get("canCrop");
            band.setCanCrop(canCrop instanceof Boolean ? (Boolean) canCrop : Boolean.FALSE);
            String targetPlacement = (String) m.getOrDefault("targetPlacement", "");
            band.setTargetPlacement(VALID_TARGET_PLACEMENTS.contains(targetPlacement) ? targetPlacement : "center");
            bands.add(band);
        }
        return bands;
    }

    private String normalizeResizeMode(String v) {
        return VALID_RESIZE_MODES.contains(v) ? v : "smart-fit";
    }
    private String normalizeStrength(String v) {
        return VALID_STRENGTHS.contains(v) ? v : "balanced";
    }
    private String normalizeFocalPosition(String v) {
        return VALID_FOCAL_POSITIONS.contains(v) ? v : "center";
    }
    private String normalizeCreativeType(String v) {
        return VALID_CREATIVE_TYPES.contains(v) ? v : "balanced_mix";
    }
    private String normalizeLayoutType(String v) {
        return VALID_LAYOUT_TYPES.contains(v) ? v : "single_subject";
    }
    private String normalizeDensity(String v) {
        return VALID_DENSITIES.contains(v) ? v : "medium";
    }

    @SuppressWarnings("unchecked")
    public BannerAiAnalysis analyze(MultipartFile file) throws IOException {
        if (openAiApiKey == null || openAiApiKey.isBlank()) {
            throw new IllegalStateException("OpenAI API Key가 설정되지 않았습니다. OPENAI_API_KEY 환경변수를 확인하세요.");
        }

        byte[] imageBytes = file.getBytes();
        String base64 = Base64.getEncoder().encodeToString(imageBytes);
        String mimeType = file.getContentType() != null ? file.getContentType() : "image/png";
        String dataUrl = "data:" + mimeType + ";base64," + base64;

        Map<String, Object> imageContent = new LinkedHashMap<>();
        imageContent.put("type", "image_url");
        imageContent.put("image_url", Map.of("url", dataUrl, "detail", openAiImageDetail));

        Map<String, Object> textContent = new LinkedHashMap<>();
        textContent.put("type", "text");
        textContent.put("text", PROMPT);

        Map<String, Object> message = new LinkedHashMap<>();
        message.put("role", "user");
        message.put("content", List.of(imageContent, textContent));

        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);
        headers.setBearerAuth(openAiApiKey);

        log.info("OpenAI Vision 분석 요청: file={} size={}bytes openAiRequestedModel={}",
                file.getOriginalFilename(), imageBytes.length, openAiAnalysisModel);

        OpenAiCallResult callResult = openAiChatClient.call(
                openAiAnalysisModel, openAiFallbackModel, headers, message, 2000,
                Map.of("response_format", Map.of("type", "json_object")));

        log.info("OpenAI 분석 완료: openAiRequestedModel={} openAiUsedModel={} openAiFallbackUsed={} " +
                 "openAiFallbackReason={} openAiTokenParameter={} openAiTokenLimit={}",
                callResult.getRequestedModel(), callResult.getUsedModel(), callResult.isFallbackUsed(),
                callResult.getFallbackReason(), callResult.getTokenParameter(), callResult.getTokenLimit());

        Map<String, Object> body = callResult.getBody();
        if (body == null) throw new IllegalStateException("OpenAI 응답이 비어있습니다.");

        List<Map<String, Object>> choices = (List<Map<String, Object>>) body.get("choices");
        if (choices == null || choices.isEmpty()) throw new IllegalStateException("OpenAI choices가 없습니다.");

        Map<String, Object> msgResp = (Map<String, Object>) choices.get(0).get("message");
        String content = (String) msgResp.get("content");
        log.info("OpenAI Vision 응답 raw: {}", content);

        Map<String, Object> result = objectMapper.readValue(content, Map.class);

        BannerAiAnalysis analysis = new BannerAiAnalysis();
        analysis.setSourceFileName(file.getOriginalFilename());
        // 이미지 분석
        analysis.setCreativeType(normalizeCreativeType((String) result.getOrDefault("creativeType", "")));
        analysis.setTextDensity(normalizeDensity((String) result.getOrDefault("textDensity", "")));
        analysis.setEdgeRisk(normalizeDensity((String) result.getOrDefault("edgeRisk", "")));
        analysis.setMainSubjectPosition(normalizeFocalPosition((String) result.getOrDefault("mainSubjectPosition", "")));
        analysis.setMainSubjectDescription((String) result.getOrDefault("mainSubjectDescription", ""));
        // 추천 설정
        analysis.setResizeMode(normalizeResizeMode((String) result.getOrDefault("resizeMode", "")));
        analysis.setSmartFitStrength(normalizeStrength((String) result.getOrDefault("smartFitStrength", "")));
        analysis.setFocalPosition(normalizeFocalPosition((String) result.getOrDefault("focalPosition", "")));
        analysis.setReason((String) result.getOrDefault("reason", ""));

        Object warningsObj = result.get("warnings");
        analysis.setWarnings(warningsObj instanceof List ? (List<String>) warningsObj : List.of());

        Object conf = result.get("confidence");
        if (conf instanceof Number) analysis.setConfidence(((Number) conf).doubleValue());

        Object cropRiskObj = result.get("cropRiskAreas");
        analysis.setCropRiskAreas(cropRiskObj instanceof List ? (List<String>) cropRiskObj : List.of());

        Object recommendedObj = result.get("recommendedBecause");
        analysis.setRecommendedBecause(recommendedObj instanceof List ? (List<String>) recommendedObj : List.of());

        Object avoidObj = result.get("avoidOptions");
        analysis.setAvoidOptions(avoidObj instanceof List ? (List<String>) avoidObj : List.of());

        // AI 요소 분석 매핑 (3.5차)
        analysis.setDetectedElements(parseDetectedElements(result));
        analysis.setElementGroups(parseElementGroups(result));
        Object reqGrps = result.get("requiredGroups");
        analysis.setRequiredGroups(reqGrps instanceof List ? (List<String>) reqGrps : List.of());
        Object priGrps = result.get("priorityGroups");
        analysis.setPriorityGroups(priGrps instanceof List ? (List<String>) priGrps : List.of());
        Object optGrps = result.get("optionalGroups");
        analysis.setOptionalGroups(optGrps instanceof List ? (List<String>) optGrps : List.of());

        // Poster Reflow (4차)
        analysis.setLayoutType(normalizeLayoutType((String) result.getOrDefault("layoutType", "")));
        Object reflowObj = result.get("reflowRecommended");
        if (reflowObj instanceof Boolean) {
            analysis.setReflowRecommended((Boolean) reflowObj);
        } else if (reflowObj instanceof String) {
            analysis.setReflowRecommended("true".equalsIgnoreCase((String) reflowObj));
        } else {
            analysis.setReflowRecommended(Boolean.FALSE);
        }
        List<BannerAiAnalysis.ContentBand> parsedBands = parseContentBands(result);
        analysis.setContentBands(parsedBands);
        log.info("AI 분석 poster 결과: layoutType={} reflowRecommended={} contentBands={}개",
                analysis.getLayoutType(), analysis.getReflowRecommended(), parsedBands.size());

        analysis.setCreatedAt(LocalDateTime.now());
        return mongoService.save(analysis);
    }

}
