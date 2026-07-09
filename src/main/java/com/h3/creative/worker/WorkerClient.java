package com.h3.creative.worker;

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

    public WorkerResponse generate(WorkerRequest request) {
        String url = workerUrl + "/generate";
        log.info("Calling worker: {} jobId={}", url, request.getJobId());

        try {
            ResponseEntity<WorkerResponse> response =
                    restTemplate.postForEntity(url, request, WorkerResponse.class);

            WorkerResponse body = response.getBody();
            if (body == null) {
                throw new IllegalStateException("Worker returned empty response");
            }
            return body;
        } catch (Exception e) {
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
