package com.h3.creative.worker;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestTemplate;

@Slf4j
@Component
public class WorkerClient {

    /** 분석용 (120s read): analyze-psd, extract-artboard, match-layers, compare */
    private final RestTemplate restTemplate;

    /** /generate 전용 (900s read): AI 이미지 생성 최대 9회 외부 호출 */
    private final RestTemplate generateRestTemplate;

    @Value("${creative.worker.url}")
    private String workerUrl;

    @Value("${creative.worker.generate-read-timeout-seconds:900}")
    private int generateReadTimeoutSeconds;

    @Value("${creative.worker.analysis-read-timeout-seconds:120}")
    private int analysisReadTimeoutSeconds;

    private static final ObjectMapper WORKER_MAPPER;
    static {
        WORKER_MAPPER = new ObjectMapper();
        WORKER_MAPPER.registerModule(new JavaTimeModule());
        WORKER_MAPPER.disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS);
    }

    public WorkerClient(RestTemplate restTemplate,
                        @Qualifier("generateRestTemplate") RestTemplate generateRestTemplate) {
        this.restTemplate = restTemplate;
        this.generateRestTemplate = generateRestTemplate;
    }

    public WorkerResponse generate(WorkerRequest request) {
        String url = workerUrl + "/generate";
        log.info("[AI_ONLY_CALL] jobId={} url={} readTimeoutSec={}",
                request.getJobId(), url, generateReadTimeoutSeconds);

        String rawBody = null;
        long t0 = System.currentTimeMillis();
        try {
            ResponseEntity<String> rawResponse =
                    generateRestTemplate.postForEntity(url, request, String.class);
            rawBody = rawResponse.getBody();
            if (rawBody == null) {
                throw new IllegalStateException("Worker returned empty response");
            }
            WorkerResponse res = WORKER_MAPPER.readValue(rawBody, WorkerResponse.class);
            log.info("[AI_ONLY_DONE] jobId={} elapsedMs={}", request.getJobId(),
                    System.currentTimeMillis() - t0);
            return res;
        } catch (Exception e) {
            long elapsed = System.currentTimeMillis() - t0;
            if (rawBody != null) {
                log.error("Worker response parse failed jobId={} rawBody(1000)={}",
                        request.getJobId(),
                        rawBody.substring(0, Math.min(1000, rawBody.length())));
            }
            log.error("Worker call failed jobId={} elapsedMs={} error={}",
                    request.getJobId(), elapsed, e.getMessage());
            WorkerResponse error = new WorkerResponse();
            error.setJobId(request.getJobId());
            error.setError(e.getMessage());
            return error;
        }
    }

    public CompareWorkerResponse compare(CompareWorkerRequest request) {
        String url = workerUrl + "/compare";
        log.info("Calling worker compare: {} compareId={}", url, request.getCompareId());
        try {
            ResponseEntity<CompareWorkerResponse> response =
                    restTemplate.postForEntity(url, request, CompareWorkerResponse.class);
            CompareWorkerResponse body = response.getBody();
            if (body == null) throw new IllegalStateException("Worker returned empty response");
            return body;
        } catch (Exception e) {
            log.error("Worker compare failed compareId={} error={}", request.getCompareId(), e.getMessage());
            CompareWorkerResponse error = new CompareWorkerResponse();
            error.setError(e.getMessage());
            return error;
        }
    }

    public PsdAnalyzeResponse analyzePsd(String filePath) {
        String url = workerUrl + "/analyze-psd";
        log.info("Calling worker analyze-psd: {}", filePath);
        try {
            PsdAnalyzeRequest req = new PsdAnalyzeRequest();
            req.setFilePath(filePath);
            ResponseEntity<PsdAnalyzeResponse> response =
                    restTemplate.postForEntity(url, req, PsdAnalyzeResponse.class);
            PsdAnalyzeResponse body = response.getBody();
            if (body == null) throw new IllegalStateException("Worker returned empty response");
            return body;
        } catch (Exception e) {
            log.error("Worker analyze-psd failed filePath={} error={}", filePath, e.getMessage());
            PsdAnalyzeResponse error = new PsdAnalyzeResponse();
            error.setError(e.getMessage());
            return error;
        }
    }

    @SuppressWarnings("unchecked")
    public java.util.Map<String, Object> extractArtboard(String psdPath, java.util.Map<String, Integer> artboardBox) {
        String url = workerUrl + "/extract-artboard";
        log.info("Calling worker extract-artboard: psdPath={}", psdPath);
        try {
            java.util.Map<String, Object> req = new java.util.LinkedHashMap<>();
            req.put("psdPath", psdPath);
            if (artboardBox != null) req.put("artboardBox", artboardBox);
            org.springframework.http.ResponseEntity<java.util.Map> resp =
                    restTemplate.postForEntity(url, req, java.util.Map.class);
            if (resp.getBody() == null) throw new IllegalStateException("Worker returned empty response");
            return resp.getBody();
        } catch (Exception e) {
            log.error("Worker extract-artboard failed: {}", e.getMessage());
            return java.util.Map.of("error", e.getMessage());
        }
    }

    @SuppressWarnings("unchecked")
    public java.util.Map<String, Object> matchLayers(
            java.util.List<java.util.Map<String, Object>> aiObjects,
            java.util.List<java.util.Map<String, Object>> layers,
            java.util.Map<String, Integer> artboardBox,
            int canvasWidth, int canvasHeight) {
        String url = workerUrl + "/match-layers";
        log.info("Calling worker match-layers: objects={}", aiObjects.size());
        try {
            java.util.Map<String, Object> req = new java.util.LinkedHashMap<>();
            req.put("aiObjects", aiObjects);
            req.put("layers", layers);
            if (artboardBox != null) req.put("artboardBox", artboardBox);
            req.put("canvasWidth", canvasWidth);
            req.put("canvasHeight", canvasHeight);
            org.springframework.http.ResponseEntity<java.util.Map> resp =
                    restTemplate.postForEntity(url, req, java.util.Map.class);
            if (resp.getBody() == null) throw new IllegalStateException("Worker returned empty response");
            return resp.getBody();
        } catch (Exception e) {
            log.error("Worker match-layers failed: {}", e.getMessage());
            return java.util.Map.of("error", e.getMessage());
        }
    }

    public boolean isHealthy() {
        try {
            restTemplate.getForEntity(workerUrl + "/health", String.class);
            return true;
        } catch (Exception e) {
            log.warn("Worker health check failed: {}", e.getMessage());
            return false;
        }
    }
}
