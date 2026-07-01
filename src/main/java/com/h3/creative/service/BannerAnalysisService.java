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

    @Value("${creative.openai.api-key}")
    private String openAiApiKey;

    private static final String OPENAI_URL = "https://api.openai.com/v1/chat/completions";

    private static final String PROMPT = """
            이 광고 배너 소재 이미지를 리사이징 관점에서 분석해줘.
            반환은 반드시 JSON 형식으로만 해줘. 다른 텍스트는 포함하지 마.

            추천 가능한 값:
            resizeMode: smart-fit, cover, contain, blur-bg
            smartFitStrength: safe, balanced, fill
            focalPosition: center, top, bottom, left, right, left-top, right-top, left-bottom, right-bottom

            판단 기준:
            - 텍스트/로고/가격/CTA가 가장자리에 많으면 safe
            - 제품/인물이 중앙에 있으면 center focalPosition
            - 제품이 오른쪽에 크면 right focalPosition
            - 하단 CTA가 중요하면 bottom focalPosition
            - 텍스트가 거의 없고 제품 중심이면 fill 가능
            - 잘림 위험이 있으면 smart-fit safe 또는 contain 추천

            반환 JSON:
            {
              "resizeMode": "...",
              "smartFitStrength": "...",
              "focalPosition": "...",
              "reason": "한국어로 분석 이유 (1~2문장)",
              "warnings": ["주의사항"],
              "confidence": 0.00
            }
            """;

    @SuppressWarnings("unchecked")
    public BannerAiAnalysis analyze(MultipartFile file) throws IOException {
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
        analysis.setResizeMode((String) result.getOrDefault("resizeMode", "smart-fit"));
        analysis.setSmartFitStrength((String) result.getOrDefault("smartFitStrength", "balanced"));
        analysis.setFocalPosition((String) result.getOrDefault("focalPosition", "center"));
        analysis.setReason((String) result.getOrDefault("reason", ""));

        Object warningsObj = result.get("warnings");
        analysis.setWarnings(warningsObj instanceof List ? (List<String>) warningsObj : List.of());

        Object conf = result.get("confidence");
        if (conf instanceof Number) analysis.setConfidence(((Number) conf).doubleValue());

        analysis.setCreatedAt(LocalDateTime.now());
        return mongoService.save(analysis);
    }
}
