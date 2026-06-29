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
