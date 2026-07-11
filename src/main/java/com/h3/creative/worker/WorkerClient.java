package com.h3.creative.worker;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestTemplate;

@Slf4j
@Component
@RequiredArgsConstructor
public class WorkerClient {

    private final RestTemplate restTemplate;

    @Value("${creative.worker.url}")
    private String workerUrl;

    // WorkerResponse 전용 ObjectMapper — raw body를 직접 파싱해 진단 로그 확보
    private static final ObjectMapper WORKER_MAPPER;
    static {
        WORKER_MAPPER = new ObjectMapper();
        WORKER_MAPPER.registerModule(new JavaTimeModule());
        WORKER_MAPPER.disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS);
    }

    public WorkerResponse generate(WorkerRequest request) {
        String url = workerUrl + "/generate";
        log.info("Calling worker: {} jobId={}", url, request.getJobId());

        String rawBody = null;
        try {
            // String.class로 raw body를 먼저 확보 → 역직렬화 실패 시 진단 로그 출력 가능
            ResponseEntity<String> rawResponse = restTemplate.postForEntity(url, request, String.class);
            rawBody = rawResponse.getBody();
            if (rawBody == null) {
                throw new IllegalStateException("Worker returned empty response");
            }
            return WORKER_MAPPER.readValue(rawBody, WorkerResponse.class);
        } catch (Exception e) {
            if (rawBody != null) {
                // 역직렬화 실패: raw body(최대 1000자) 로그 출력 → 계약 불일치 파악
                log.error("Worker response parse failed jobId={} rawBody(1000)={}",
                        request.getJobId(),
                        rawBody.substring(0, Math.min(1000, rawBody.length())));
            }
            log.error("Worker call failed jobId={} error={}", request.getJobId(), e.getMessage());
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
