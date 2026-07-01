package com.h3.creative.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.h3.creative.domain.BannerAiAnalysis;
import com.h3.creative.mongo.BannerAnalysisMongoService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestTemplate;
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

    private final RestTemplate restTemplate;
    private final BannerAnalysisMongoService mongoService;
    private final ObjectMapper objectMapper;

    @Value("${creative.openai.api-key:}")
    private String openAiApiKey;

    private static final String OPENAI_URL = "https://api.openai.com/v1/chat/completions";

    private static final String PROMPT = """
            이 광고 배너 소재 이미지를 리사이징 관점에서 분석해줘.
            반환은 반드시 JSON 형식으로만 해줘. 다른 텍스트는 포함하지 마.

            [이미지 분석 필드]
            creativeType: text_heavy (텍스트/로고/CTA가 주를 이룸), product_focused (제품/인물이 주를 이룸), balanced_mix (텍스트와 제품이 균형)
            textDensity: high (텍스트가 많고 빽빽함), medium (적당한 텍스트), low (텍스트 거의 없음)
            edgeRisk: high (가장자리에 중요 요소 있어 잘림 위험 높음), medium (주의 필요), low (잘림 위험 없음)
            mainSubjectPosition: 주요 피사체(제품/인물)가 이미지에서 위치하는 방향 (center, top, bottom, left, right, left-top, right-top, left-bottom, right-bottom)
            mainSubjectDescription: 주요 피사체를 한국어로 한 문장으로 설명

            [추천 설정 필드]
            resizeMode: smart-fit, cover, contain, blur-bg
            smartFitStrength: safe, balanced, fill
            focalPosition: center, top, bottom, left, right, left-top, right-top, left-bottom, right-bottom

            판단 기준:
            - 텍스트/로고/가격/CTA가 가장자리에 많으면 edgeRisk high, smartFitStrength safe
            - 제품/인물이 명확하면 product_focused, 해당 위치를 mainSubjectPosition과 focalPosition에 반영
            - 텍스트가 거의 없고 제품 중심이면 fill 가능
            - 잘림 위험이 높으면 smart-fit safe 또는 contain 추천
            - focalPosition은 mainSubjectPosition과 동일하게 맞추는 것을 우선으로 함

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
              "confidence": 0.00
            }
            """;

    private static final java.util.Set<String> VALID_RESIZE_MODES =
            java.util.Set.of("smart-fit", "cover", "contain", "blur-bg");
    private static final java.util.Set<String> VALID_STRENGTHS =
            java.util.Set.of("safe", "balanced", "fill");
    private static final java.util.Set<String> VALID_FOCAL_POSITIONS =
            java.util.Set.of("center", "top", "bottom", "left", "right",
                             "left-top", "right-top", "left-bottom", "right-bottom");
    private static final java.util.Set<String> VALID_CREATIVE_TYPES =
            java.util.Set.of("text_heavy", "product_focused", "balanced_mix");
    private static final java.util.Set<String> VALID_DENSITIES =
            java.util.Set.of("high", "medium", "low");

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
        imageContent.put("image_url", Map.of("url", dataUrl, "detail", "low"));

        Map<String, Object> textContent = new LinkedHashMap<>();
        textContent.put("type", "text");
        textContent.put("text", PROMPT);

        Map<String, Object> message = new LinkedHashMap<>();
        message.put("role", "user");
        message.put("content", List.of(imageContent, textContent));

        Map<String, Object> requestBody = new LinkedHashMap<>();
        requestBody.put("model", "gpt-4.1-mini");
        requestBody.put("messages", List.of(message));
        requestBody.put("max_tokens", 600);
        requestBody.put("response_format", Map.of("type", "json_object"));

        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);
        headers.setBearerAuth(openAiApiKey);

        log.info("OpenAI Vision 분석 요청: file={} size={}bytes", file.getOriginalFilename(), imageBytes.length);

        ResponseEntity<Map> response = restTemplate.postForEntity(
                OPENAI_URL, new HttpEntity<>(requestBody, headers), Map.class);

        Map<String, Object> body = response.getBody();
        if (body == null) throw new IllegalStateException("OpenAI 응답이 비어있습니다.");

        List<Map<String, Object>> choices = (List<Map<String, Object>>) body.get("choices");
        if (choices == null || choices.isEmpty()) throw new IllegalStateException("OpenAI choices가 없습니다.");

        Map<String, Object> msgResp = (Map<String, Object>) choices.get(0).get("message");
        String content = (String) msgResp.get("content");
        log.info("OpenAI 응답: {}", content);

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

        analysis.setCreatedAt(LocalDateTime.now());
        return mongoService.save(analysis);
    }
}
