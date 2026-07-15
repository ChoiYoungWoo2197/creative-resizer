package com.h3.creative.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.web.client.HttpClientErrorException;
import org.springframework.web.client.RestTemplate;

import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class OpenAiChatClientTest {

    private RestTemplate restTemplate;
    private OpenAiChatClient client;

    private static final String PRIMARY = "gpt-5-mini";
    private static final String FALLBACK = "gpt-4.1-mini";

    @BeforeEach
    void setUp() {
        restTemplate = mock(RestTemplate.class);
        client = new OpenAiChatClient(restTemplate, new ObjectMapper());
    }

    // ── tokenParamFor ───────────────────────────────────────────────────────

    @Test
    void tokenParamFor_gpt5mini() {
        assertThat(OpenAiChatClient.tokenParamFor("gpt-5-mini")).isEqualTo("max_completion_tokens");
    }

    @Test
    void tokenParamFor_gpt41mini() {
        assertThat(OpenAiChatClient.tokenParamFor("gpt-4.1-mini")).isEqualTo("max_tokens");
    }

    @Test
    void tokenParamFor_null() {
        assertThat(OpenAiChatClient.tokenParamFor(null)).isEqualTo("max_tokens");
    }

    @Test
    void tokenParamFor_o1() {
        assertThat(OpenAiChatClient.tokenParamFor("o1-mini")).isEqualTo("max_completion_tokens");
    }

    @Test
    void tokenParamFor_o3() {
        assertThat(OpenAiChatClient.tokenParamFor("o3")).isEqualTo("max_completion_tokens");
    }

    // ── 성공 케이스 ─────────────────────────────────────────────────────────

    @SuppressWarnings({"unchecked", "rawtypes"})
    @Test
    void call_gpt5mini_success_noFallback() {
        Map<String, Object> fakeBody = Map.of("choices", List.of(
                Map.of("message", Map.of("content", "{}"))));
        when(restTemplate.postForEntity(anyString(), any(), eq(Map.class)))
                .thenReturn(org.springframework.http.ResponseEntity.ok(fakeBody));

        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);
        Map<String, Object> msg = Map.of("role", "user", "content", "hi");

        OpenAiCallResult result = client.call(PRIMARY, FALLBACK, headers, msg, 2000, null);

        assertThat(result.getUsedModel()).isEqualTo(PRIMARY);
        assertThat(result.getRequestedModel()).isEqualTo(PRIMARY);
        assertThat(result.isFallbackUsed()).isFalse();
        assertThat(result.getTokenParameter()).isEqualTo("max_completion_tokens");
        assertThat(result.getTokenLimit()).isEqualTo(2000);
        assertThat(result.getBody()).isEqualTo(fakeBody);
    }

    // ── unsupported_parameter → 파라미터 교체 재시도, 모델 변경 없음 ─────────

    @SuppressWarnings({"unchecked", "rawtypes"})
    @Test
    void call_unsupportedParameter_correctedParamRetry_noModelFallback() {
        String errorJson = """
                {"error":{"code":"unsupported_parameter","message":"max_tokens not supported"}}
                """;
        HttpClientErrorException paramError = HttpClientErrorException.create(
                HttpStatus.BAD_REQUEST, "Bad Request",
                HttpHeaders.EMPTY, errorJson.getBytes(StandardCharsets.UTF_8), StandardCharsets.UTF_8);

        Map<String, Object> fakeBody = Map.of("choices", List.of(
                Map.of("message", Map.of("content", "{}"))));

        when(restTemplate.postForEntity(anyString(), any(), eq(Map.class)))
                .thenThrow(paramError)
                .thenReturn(org.springframework.http.ResponseEntity.ok(fakeBody));

        HttpHeaders headers = new HttpHeaders();
        Map<String, Object> msg = Map.of("role", "user", "content", "hi");

        OpenAiCallResult result = client.call(PRIMARY, FALLBACK, headers, msg, 2000, null);

        assertThat(result.getUsedModel()).isEqualTo(PRIMARY);   // 모델은 PRIMARY 그대로
        assertThat(result.isFallbackUsed()).isFalse();           // fallback 없음
        assertThat(result.getTokenParameter()).isEqualTo("max_tokens"); // 파라미터만 교체됨
        verify(restTemplate, times(2)).postForEntity(anyString(), any(), eq(Map.class));
    }

    // ── model_not_found → fallback 모델 사용 ────────────────────────────────

    @SuppressWarnings({"unchecked", "rawtypes"})
    @Test
    void call_modelNotFound_useFallbackModel() {
        String errorJson = """
                {"error":{"code":"model_not_found","message":"The model does not exist"}}
                """;
        HttpClientErrorException notFoundError = HttpClientErrorException.create(
                HttpStatus.NOT_FOUND, "Not Found",
                HttpHeaders.EMPTY, errorJson.getBytes(StandardCharsets.UTF_8), StandardCharsets.UTF_8);

        Map<String, Object> fakeBody = Map.of("choices", List.of(
                Map.of("message", Map.of("content", "{}"))));

        when(restTemplate.postForEntity(anyString(), any(), eq(Map.class)))
                .thenThrow(notFoundError)
                .thenReturn(org.springframework.http.ResponseEntity.ok(fakeBody));

        HttpHeaders headers = new HttpHeaders();
        Map<String, Object> msg = Map.of("role", "user", "content", "hi");

        OpenAiCallResult result = client.call(PRIMARY, FALLBACK, headers, msg, 2000, null);

        assertThat(result.getUsedModel()).isEqualTo(FALLBACK);   // fallback 모델 사용
        assertThat(result.getRequestedModel()).isEqualTo(PRIMARY);
        assertThat(result.isFallbackUsed()).isTrue();
        assertThat(result.getFallbackReason()).isEqualTo("model_not_found");
        assertThat(result.getTokenParameter()).isEqualTo("max_tokens"); // fallback=gpt-4.1-mini → max_tokens
    }

    // ── rate_limit 등 → 즉시 예외 ───────────────────────────────────────────

    @SuppressWarnings({"unchecked", "rawtypes"})
    @Test
    void call_rateLimit_throwsImmediately() {
        String errorJson = """
                {"error":{"code":"rate_limit_exceeded","message":"Too many requests"}}
                """;
        HttpClientErrorException rateLimitError = HttpClientErrorException.create(
                HttpStatus.TOO_MANY_REQUESTS, "Too Many Requests",
                HttpHeaders.EMPTY, errorJson.getBytes(StandardCharsets.UTF_8), StandardCharsets.UTF_8);

        when(restTemplate.postForEntity(anyString(), any(), eq(Map.class)))
                .thenThrow(rateLimitError);

        HttpHeaders headers = new HttpHeaders();
        Map<String, Object> msg = Map.of("role", "user", "content", "hi");

        assertThatThrownBy(() -> client.call(PRIMARY, FALLBACK, headers, msg, 2000, null))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("rate_limit_exceeded");

        verify(restTemplate, times(1)).postForEntity(anyString(), any(), eq(Map.class));
    }
}
