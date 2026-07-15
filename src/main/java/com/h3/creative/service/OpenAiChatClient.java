package com.h3.creative.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Component;
import org.springframework.web.client.HttpClientErrorException;
import org.springframework.web.client.RestTemplate;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * OpenAI Chat Completions 공통 클라이언트.
 *
 * 토큰 파라미터 정책:
 *  - gpt-5 / o-series → max_completion_tokens
 *  - gpt-4.x 및 기타  → max_tokens
 *
 * 오류 처리 정책:
 *  - unsupported_parameter: 반대 파라미터로 재시도 (모델 fallback 없음)
 *  - model_not_found / unsupported_model: fallbackModel로 재시도
 *  - 그 외 (rate_limit, context_length_exceeded 등): 즉시 예외
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class OpenAiChatClient {

    private final RestTemplate restTemplate;
    private final ObjectMapper objectMapper;

    private static final String OPENAI_URL = "https://api.openai.com/v1/chat/completions";

    /**
     * 모델명에 따라 적절한 토큰 파라미터명 반환.
     * gpt-5 / o1 / o3 / o4 계열 → "max_completion_tokens"
     * 그 외 → "max_tokens"
     */
    public static String tokenParamFor(String model) {
        if (model == null) return "max_tokens";
        String m = model.toLowerCase();
        if (m.startsWith("gpt-5") || m.startsWith("o1") || m.startsWith("o3") || m.startsWith("o4")) {
            return "max_completion_tokens";
        }
        return "max_tokens";
    }

    /**
     * OpenAI Chat Completions 호출 (자동 파라미터 수정 + 선택적 모델 fallback).
     *
     * @param primaryModel  우선 사용 모델 (예: gpt-5-mini)
     * @param fallbackModel 모델 미지원 시 fallback 모델 (예: gpt-4.1-mini)
     * @param headers       Authorization 포함 HttpHeaders
     * @param message       단일 user message Map
     * @param maxTokens     토큰 제한 수
     * @param extraParams   추가 파라미터 (예: response_format) — null 허용
     * @return OpenAiCallResult (body + metadata)
     */
    @SuppressWarnings("unchecked")
    public OpenAiCallResult call(String primaryModel, String fallbackModel,
                                  HttpHeaders headers,
                                  Map<String, Object> message,
                                  int maxTokens,
                                  Map<String, Object> extraParams) {

        String triedParam = tokenParamFor(primaryModel);

        try {
            Map<String, Object> body = doCall(primaryModel, headers, message, triedParam, maxTokens, extraParams);
            log.info("OpenAI 요청 성공: requestedModel={} usedModel={} fallbackUsed=false " +
                     "openAiTokenParameter={} openAiTokenLimit={}", primaryModel, primaryModel, triedParam, maxTokens);
            return OpenAiCallResult.builder()
                    .body(body)
                    .requestedModel(primaryModel)
                    .usedModel(primaryModel)
                    .fallbackUsed(false)
                    .fallbackReason(null)
                    .tokenParameter(triedParam)
                    .tokenLimit(maxTokens)
                    .build();

        } catch (HttpClientErrorException e) {
            String errorCode = parseErrorCode(e.getResponseBodyAsString());
            log.warn("OpenAI 오류: model={} errorCode={}", primaryModel, errorCode);

            // unsupported_parameter: 파라미터만 교체 후 재시도 (모델 변경 없음)
            if ("unsupported_parameter".equals(errorCode) || "unknown_parameter".equals(errorCode)) {
                String correctedParam = "max_completion_tokens".equals(triedParam) ? "max_tokens" : "max_completion_tokens";
                log.info("unsupported_parameter 수정 재시도: model={} param={} → {}",
                        primaryModel, triedParam, correctedParam);
                try {
                    Map<String, Object> body = doCall(primaryModel, headers, message, correctedParam, maxTokens, extraParams);
                    log.info("OpenAI 파라미터 수정 재시도 성공: requestedModel={} usedModel={} openAiFallbackUsed=false " +
                             "openAiFallbackReason=param_corrected openAiTokenParameter={} openAiTokenLimit={}",
                            primaryModel, primaryModel, correctedParam, maxTokens);
                    return OpenAiCallResult.builder()
                            .body(body)
                            .requestedModel(primaryModel)
                            .usedModel(primaryModel)
                            .fallbackUsed(false)
                            .fallbackReason("param_corrected:" + triedParam + "→" + correctedParam)
                            .tokenParameter(correctedParam)
                            .tokenLimit(maxTokens)
                            .build();
                } catch (HttpClientErrorException retryEx) {
                    throw new IllegalStateException(
                            "OpenAI 파라미터 수정 재시도 실패 model=" + primaryModel + ": " + retryEx.getMessage(), retryEx);
                }
            }

            // model_not_found / unsupported_model: fallback 모델로 재시도
            if ("model_not_found".equals(errorCode) || "unsupported_model".equals(errorCode)) {
                String fallbackParam = tokenParamFor(fallbackModel);
                log.warn("모델 미지원, fallback: openAiRequestedModel={} openAiUsedModel={} " +
                         "openAiFallbackUsed=true openAiFallbackReason={} openAiTokenParameter={} openAiTokenLimit={}",
                        primaryModel, fallbackModel, errorCode, fallbackParam, maxTokens);
                try {
                    Map<String, Object> body = doCall(fallbackModel, headers, message, fallbackParam, maxTokens, extraParams);
                    log.info("OpenAI fallback 성공: openAiRequestedModel={} openAiUsedModel={} openAiFallbackUsed=true " +
                             "openAiFallbackReason={} openAiTokenParameter={} openAiTokenLimit={}",
                            primaryModel, fallbackModel, errorCode, fallbackParam, maxTokens);
                    return OpenAiCallResult.builder()
                            .body(body)
                            .requestedModel(primaryModel)
                            .usedModel(fallbackModel)
                            .fallbackUsed(true)
                            .fallbackReason(errorCode)
                            .tokenParameter(fallbackParam)
                            .tokenLimit(maxTokens)
                            .build();
                } catch (HttpClientErrorException fbEx) {
                    throw new IllegalStateException(
                            "OpenAI fallback 실패 model=" + fallbackModel + ": " + fbEx.getMessage(), fbEx);
                }
            }

            // 그 외 오류(rate_limit, context_length_exceeded 등): 즉시 예외
            throw new IllegalStateException(
                    "OpenAI 요청 실패 [" + errorCode + "] model=" + primaryModel + ": " + e.getMessage(), e);
        }
    }

    @SuppressWarnings("rawtypes")
    private Map<String, Object> doCall(String model, HttpHeaders headers,
                                        Map<String, Object> message, String tokenParam,
                                        int tokenLimit, Map<String, Object> extraParams) {
        Map<String, Object> requestBody = new LinkedHashMap<>();
        requestBody.put("model", model);
        requestBody.put("messages", List.of(message));
        requestBody.put(tokenParam, tokenLimit);
        if (extraParams != null) requestBody.putAll(extraParams);

        ResponseEntity<Map> response = restTemplate.postForEntity(
                OPENAI_URL, new HttpEntity<>(requestBody, headers), Map.class);
        return response.getBody();
    }

    @SuppressWarnings("unchecked")
    private String parseErrorCode(String body) {
        if (body == null || body.isBlank()) return "unknown";
        try {
            Map<String, Object> parsed = objectMapper.readValue(body, Map.class);
            Object errObj = parsed.get("error");
            if (errObj instanceof Map) {
                Map<String, Object> err = (Map<String, Object>) errObj;
                if (err.get("code") != null) return err.get("code").toString();
                if (err.get("type") != null) return err.get("type").toString();
            }
        } catch (Exception ignored) {}
        return "unknown";
    }
}
