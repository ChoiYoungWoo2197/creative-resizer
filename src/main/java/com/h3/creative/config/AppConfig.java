package com.h3.creative.config;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Primary;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.web.client.RestTemplate;

@Configuration
public class AppConfig {

    @Value("${creative.worker.connect-timeout-seconds:20}")
    private int connectTimeoutSeconds;

    @Value("${creative.worker.analysis-read-timeout-seconds:120}")
    private int analysisReadTimeoutSeconds;

    @Value("${creative.worker.generate-read-timeout-seconds:900}")
    private int generateReadTimeoutSeconds;

    /**
     * 분석용 RestTemplate (analyze-psd, extract-artboard, match-layers, compare 등).
     * read timeout: WORKER_ANALYSIS_READ_TIMEOUT_SECONDS (default 120s).
     */
    @Bean
    @Primary
    public RestTemplate restTemplate() {
        SimpleClientHttpRequestFactory factory = new SimpleClientHttpRequestFactory();
        factory.setConnectTimeout(connectTimeoutSeconds * 1_000);
        factory.setReadTimeout(analysisReadTimeoutSeconds * 1_000);
        return new RestTemplate(factory);
    }

    /**
     * /generate 전용 RestTemplate.
     * AI 이미지 생성은 최대 9회 외부 호출(3 spec × 3 attempt) → 120s로 부족.
     * read timeout: WORKER_GENERATE_READ_TIMEOUT_SECONDS (default 900s).
     */
    @Bean("generateRestTemplate")
    public RestTemplate generateRestTemplate() {
        SimpleClientHttpRequestFactory factory = new SimpleClientHttpRequestFactory();
        factory.setConnectTimeout(connectTimeoutSeconds * 1_000);
        factory.setReadTimeout(generateReadTimeoutSeconds * 1_000);
        return new RestTemplate(factory);
    }

    @Bean
    public ObjectMapper objectMapper() {
        ObjectMapper mapper = new ObjectMapper();
        mapper.registerModule(new JavaTimeModule());
        mapper.disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS);
        return mapper;
    }
}
