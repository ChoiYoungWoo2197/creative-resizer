package com.h3.creative.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.h3.creative.domain.BannerAiCompare;
import com.h3.creative.domain.BannerJob;
import com.h3.creative.domain.BannerSpec;
import com.h3.creative.mongo.BannerCompareMongoService;
import com.h3.creative.mongo.BannerMongoService;
import com.h3.creative.mongo.SpecMongoService;
import com.h3.creative.worker.CompareWorkerRequest;
import com.h3.creative.worker.CompareWorkerResponse;
import com.h3.creative.worker.WorkerClient;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestTemplate;

import java.io.File;
import java.io.IOException;
import java.nio.file.Files;
import java.util.*;

@Slf4j
@Service
@RequiredArgsConstructor
public class BannerCompareService {

    private final BannerMongoService bannerMongoService;
    private final SpecMongoService specMongoService;
    private final BannerCompareMongoService compareMongoService;
    private final WorkerClient workerClient;
    private final RestTemplate restTemplate;
    private final ObjectMapper objectMapper;

    @Value("${creative.openai.api-key:}")
    private String openAiApiKey;

    private static final String OPENAI_URL = "https://api.openai.com/v1/chat/completions";

    private static final String COMPARE_PROMPT = """
            다음 이미지들을 순서대로 분석해줘:
            1번: 원본 이미지 (리사이징 기준)
            2번: safe 강도 리사이징 결과 (원본 최대 보존, 여백 발생 가능)
            3번: balanced 강도 리사이징 결과 (균형)
            4번: fill 강도 리사이징 결과 (최대 확대, 일부 잘림 가능)

            각 후보(2~4번)의 리사이징 품질을 평가해줘.

            평가 기준:
            - 원본의 핵심 메시지(텍스트/로고/CTA)가 유지되는가
            - 주요 텍스트가 잘리지 않았는가
            - 제품/인물/로고가 잘 보이는가
            - 여백이 과하거나 답답하지 않은가
            - 광고 배너로 자연스러운가

            반환은 반드시 JSON 형식으로만 해줘. 다른 텍스트 포함하지 마.

            {
              "bestCandidate": "balanced",
              "summary": "한국어로 최적 후보 선정 이유 (1문장)",
              "candidates": [
                { "strength": "safe", "score": 0, "pros": ["..."], "cons": ["..."] },
                { "strength": "balanced", "score": 0, "pros": ["..."], "cons": [] },
                { "strength": "fill", "score": 0, "pros": ["..."], "cons": ["..."] }
              ]
            }
            """;

    private static final java.util.Set<String> VALID_CANDIDATES = java.util.Set.of("safe", "balanced", "fill");

    @SuppressWarnings("unchecked")
    public BannerAiCompare compare(String jobId, String specId) throws IOException {
        BannerJob job = bannerMongoService.findById(jobId);
        if (job == null) throw new IllegalArgumentException("Job not found: " + jobId);

        List<BannerSpec> specs = specMongoService.findByIds(List.of(specId));
        if (specs.isEmpty()) throw new IllegalArgumentException("Spec not found: " + specId);
        BannerSpec spec = specs.get(0);

        String compareId = UUID.randomUUID().toString();
        String focalPosition = job.getFocalPosition() != null ? job.getFocalPosition() : "center";

        CompareWorkerRequest workerReq = CompareWorkerRequest.builder()
                .compareId(compareId)
                .psdPath(job.getPsdPath())
                .spec(CompareWorkerRequest.SpecItem.builder()
                        .media(spec.getMedia())
                        .slug(spec.getSlug() != null ? spec.getSlug() : "")
                        .width(spec.getWidth())
                        .height(spec.getHeight())
                        .build())
                .resizeMode("smart-fit")
                .focalPosition(focalPosition)
                .strengths(List.of("safe", "balanced", "fill"))
                .build();

        log.info("Compare 요청: compareId={} jobId={} specId={} spec={}x{}", compareId, jobId, specId, spec.getWidth(), spec.getHeight());
        CompareWorkerResponse workerResp = workerClient.compare(workerReq);
        if (!workerResp.isSuccess()) {
            throw new IllegalStateException("Worker compare 실패: " + workerResp.getError());
        }

        Map<String, Object> aiResult = callOpenAiCompare(workerResp.getOriginalFilePath(), workerResp.getCandidates());

        String bestCandidateRaw = (String) aiResult.getOrDefault("bestCandidate", "balanced");
        final String bestCandidate = VALID_CANDIDATES.contains(bestCandidateRaw) ? bestCandidateRaw : "balanced";
        String summary = (String) aiResult.getOrDefault("summary", "");
        List<Map<String, Object>> aiCandidates = (List<Map<String, Object>>) aiResult.getOrDefault("candidates", List.of());

        List<BannerAiCompare.CandidateResult> results = new ArrayList<>();
        for (CompareWorkerResponse.CandidateItem ci : workerResp.getCandidates()) {
            BannerAiCompare.CandidateResult cr = new BannerAiCompare.CandidateResult();
            cr.setStrength(ci.getStrength());
            cr.setFileName(ci.getFileName());
            cr.setFilePath(ci.getFilePath());
            cr.setPreviewUrl("/api/banner/compare/" + compareId + "/files/" + ci.getFileName());

            final String strength = ci.getStrength();
            aiCandidates.stream()
                    .filter(m -> strength.equals(m.get("strength")))
                    .findFirst()
                    .ifPresent(m -> {
                        Object score = m.get("score");
                        cr.setScore(score instanceof Number ? ((Number) score).intValue() : 0);
                        Object pros = m.get("pros");
                        cr.setPros(pros instanceof List ? (List<String>) pros : List.of());
                        Object cons = m.get("cons");
                        cr.setCons(cons instanceof List ? (List<String>) cons : List.of());
                    });
            results.add(cr);
        }

        int bestScore = results.stream()
                .filter(r -> r.getStrength().equals(bestCandidate))
                .mapToInt(BannerAiCompare.CandidateResult::getScore)
                .findFirst().orElse(0);

        BannerAiCompare compare = new BannerAiCompare();
        compare.setId(compareId);
        compare.setJobId(jobId);
        compare.setSpecId(specId);
        compare.setMedia(spec.getMedia());
        compare.setWidth(spec.getWidth());
        compare.setHeight(spec.getHeight());
        compare.setResizeMode("smart-fit");
        compare.setFocalPosition(focalPosition);
        compare.setBestCandidate(bestCandidate);
        compare.setBestScore(bestScore);
        compare.setSummary(summary);
        compare.setCandidates(results);

        return compareMongoService.save(compare);
    }

    public BannerAiCompare getCompare(String compareId) {
        return compareMongoService.findById(compareId);
    }

    public BannerJob apply(String jobId, String compareId, String specId, String candidate) {
        BannerAiCompare compare = compareMongoService.findById(compareId);
        if (compare == null) throw new IllegalArgumentException("Compare not found: " + compareId);

        BannerAiCompare.CandidateResult chosen = compare.getCandidates().stream()
                .filter(c -> candidate.equals(c.getStrength()))
                .findFirst()
                .orElseThrow(() -> new IllegalArgumentException("Candidate not found: " + candidate));

        log.info("AI 후보 적용: jobId={} specId={} candidate={}", jobId, specId, candidate);
        return bannerMongoService.applyCompareToResult(jobId, specId, compareId, candidate, chosen.getFilePath());
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> callOpenAiCompare(String originalFilePath,
            List<CompareWorkerResponse.CandidateItem> candidates) throws IOException {

        List<Map<String, Object>> content = new ArrayList<>();

        List<String> imagePaths = new ArrayList<>();
        imagePaths.add(originalFilePath);
        for (CompareWorkerResponse.CandidateItem ci : candidates) {
            imagePaths.add(ci.getFilePath());
        }

        for (String imagePath : imagePaths) {
            byte[] bytes = Files.readAllBytes(new File(imagePath).toPath());
            String base64 = Base64.getEncoder().encodeToString(bytes);
            Map<String, Object> imgContent = new LinkedHashMap<>();
            imgContent.put("type", "image_url");
            imgContent.put("image_url", Map.of("url", "data:image/png;base64," + base64, "detail", "low"));
            content.add(imgContent);
        }

        Map<String, Object> textContent = new LinkedHashMap<>();
        textContent.put("type", "text");
        textContent.put("text", COMPARE_PROMPT);
        content.add(textContent);

        Map<String, Object> message = new LinkedHashMap<>();
        message.put("role", "user");
        message.put("content", content);

        Map<String, Object> requestBody = new LinkedHashMap<>();
        requestBody.put("model", "gpt-4.1-mini");
        requestBody.put("messages", List.of(message));
        requestBody.put("max_tokens", 1200);
        requestBody.put("response_format", Map.of("type", "json_object"));

        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);
        headers.setBearerAuth(openAiApiKey);

        log.info("OpenAI Compare 요청: 이미지 {}장", imagePaths.size());
        ResponseEntity<Map> response = restTemplate.postForEntity(
                OPENAI_URL, new HttpEntity<>(requestBody, headers), Map.class);

        Map<String, Object> body = response.getBody();
        if (body == null) throw new IllegalStateException("OpenAI 응답이 비어있습니다.");

        List<Map<String, Object>> choices = (List<Map<String, Object>>) body.get("choices");
        Map<String, Object> msgResp = (Map<String, Object>) choices.get(0).get("message");
        String responseContent = (String) msgResp.get("content");
        log.info("OpenAI Compare 응답: {}", responseContent);

        return objectMapper.readValue(responseContent, Map.class);
    }
}
