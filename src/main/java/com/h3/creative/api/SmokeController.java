package com.h3.creative.api;

import com.h3.creative.worker.WorkerClient;
import com.h3.creative.worker.WorkerRequest;
import com.h3.creative.worker.WorkerResponse;
import lombok.RequiredArgsConstructor;
import org.springframework.context.annotation.Profile;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

@Profile("smoke")
@RestController
@RequestMapping("/api/smoke")
@RequiredArgsConstructor
public class SmokeController {

    private final WorkerClient workerClient;

    @GetMapping("/worker-health")
    public Map<String, Object> workerHealth() {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("workerHealthy", workerClient.isHealthy());
        return result;
    }

    // Spring Boot → Worker E2E: WorkerResponse 역직렬화 계약 검증
    // psdPath는 Worker 컨테이너 내부 fixture 경로
    @PostMapping("/worker-generate-test")
    public Map<String, Object> workerGenerateTest() {
        WorkerRequest request = WorkerRequest.builder()
                .jobId("smoke-e2e-" + System.currentTimeMillis())
                .psdPath("/app/fixtures/test_banner.jpg")
                .sourceType("image")
                .resizeMode("smart-fit")
                .smartFitStrength("balanced")
                .focalPosition("center")
                .outputFormat("jpg")
                .objectReflowEnabled(false)
                .specs(List.of(
                        WorkerRequest.SpecItem.builder()
                                .media("smoke")
                                .name("Smoke 300x250")
                                .slug("smoke-300x250")
                                .width(300)
                                .height(250)
                                .build()
                ))
                .build();

        WorkerResponse response = workerClient.generate(request);

        Map<String, Object> result = new LinkedHashMap<>();
        result.put("jobId", response.getJobId());
        result.put("count", response.getCount());
        result.put("error", response.getError());
        result.put("deserializationSuccess", response.isSuccess());

        if (response.getResults() != null && !response.getResults().isEmpty()) {
            WorkerResponse.ResultItem item = response.getResults().get(0);
            result.put("resultSlug", item.getSlug());
            result.put("resultValid", item.getValid());
            result.put("safeZoneViolationsType",
                    item.getSafeZoneViolations() != null ? "List<String>" : "null");
            result.put("safeZoneViolations", item.getSafeZoneViolations());
            result.put("renderSource", item.getRenderSource());
            result.put("layoutScore", item.getLayoutScore());
            result.put("safeZonePassed", item.getSafeZonePassed());
        }

        return result;
    }
}
