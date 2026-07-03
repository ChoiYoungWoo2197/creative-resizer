package com.h3.creative.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.h3.creative.domain.BannerAiAnalysis;
import com.h3.creative.domain.BannerAiCompare;
import com.h3.creative.domain.BannerJob;
import com.h3.creative.domain.BannerSpec;
import com.h3.creative.mongo.BannerAnalysisMongoService;
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
import java.util.stream.Collectors;

@Slf4j
@Service
@RequiredArgsConstructor
public class BannerCompareService {

    private final BannerMongoService bannerMongoService;
    private final SpecMongoService specMongoService;
    private final BannerCompareMongoService compareMongoService;
    private final BannerAnalysisMongoService analysisMongoService;
    private final WorkerClient workerClient;
    private final RestTemplate restTemplate;
    private final ObjectMapper objectMapper;

    @Value("${creative.openai.api-key:}")
    private String openAiApiKey;

    private static final String OPENAI_URL = "https://api.openai.com/v1/chat/completions";

    private static final String COMPARE_PROMPT_BASE = """
            다음 이미지들을 순서대로 분석해줘:
            1번: 원본 이미지 (리사이징 기준)
            2번: safe 강도 리사이징 결과 (원본 최대 보존, 여백 발생 가능)
            3번: balanced 강도 리사이징 결과 (균형)
            4번: fill 강도 리사이징 결과 (최대 확대, 일부 잘림 가능)

            각 후보(2번~)의 리사이징 품질을 평가해줘.
            이미지가 5장이면 5번(focus-fill)도 평가 대상이다.
            이미지가 6장이면 6번(poster-reflow)도 평가 대상이다.

            focus-fill 후보: AI가 감지한 필수 요소를 기준으로 crop하여 blur를 줄이고 꽉 찬 배너를 만든 결과.
            평가 시: required 요소가 잘리지 않았는지, blur 배경이 줄어들었는지, 광고 배너처럼 꽉 차고 자연스러운지 확인하세요.
            focus-fill이 required 요소를 모두 유지하면서 blur 영역을 줄였다면 높은 점수를 주세요. 단, required 텍스트나 제품이 잘리면 큰 감점 처리하세요.

            poster-reflow 후보: 정보형 포스터 이미지를 여러 가로 영역으로 나누고, 각 영역을 타겟 배너 높이에 맞게 압축/재조립한 결과.
            평가 시: 상단 메인 카피가 보이는지, 날짜/신청기간/핵심 정보가 유지되는지, 하단 설명 문구가 유지되는지,
            텍스트가 지나치게 찌그러지지 않는지, 원본의 정보 구조가 유지되는지, 광고 배너로 자연스러운지 확인하세요.

            평가 기준:
            - 원본의 핵심 메시지(텍스트/로고/CTA)가 유지되는가
            - 주요 텍스트가 잘리지 않았는가
            - 제품/인물/로고가 잘 보이는가
            - 여백이 과하거나 답답하지 않은가
            - 광고 배너로 자연스러운가

            반환은 반드시 JSON 형식으로만 해줘. 다른 텍스트 포함하지 마.
            candidates 배열에는 실제 전달된 이미지 순서대로 후보만 포함해. 이미지가 없는 후보(focus-fill, poster-reflow 등)는 제외해.

            아래 JSON 형식을 따르되, 실제 비교한 strength 값만 포함해:
            {
              "bestCandidate": "balanced",
              "summary": "한국어로 최적 후보 선정 이유 (1문장)",
              "candidates": [
                { "strength": "safe", "score": 0, "preservedRequiredGroups": [], "lostRequiredGroups": [], "preservedPriorityGroups": [], "lostPriorityGroups": [], "pros": ["..."], "cons": ["..."] },
                { "strength": "balanced", "score": 0, "preservedRequiredGroups": [], "lostRequiredGroups": [], "preservedPriorityGroups": [], "lostPriorityGroups": [], "pros": ["..."], "cons": [] },
                { "strength": "fill", "score": 0, "preservedRequiredGroups": [], "lostRequiredGroups": [], "preservedPriorityGroups": [], "lostPriorityGroups": [], "pros": ["..."], "cons": ["..."] },
                { "strength": "focus-fill", "score": 0, "preservedRequiredGroups": [], "lostRequiredGroups": [], "preservedPriorityGroups": [], "lostPriorityGroups": [], "pros": ["..."], "cons": ["..."] },
                { "strength": "poster-reflow", "score": 0, "preservedRequiredGroups": [], "lostRequiredGroups": [], "preservedPriorityGroups": [], "lostPriorityGroups": [], "pros": ["..."], "cons": ["..."] }
              ]
            }
            focus-fill / poster-reflow 이미지가 없으면 해당 항목은 candidates에서 제외하세요.
            """;

    private String buildComparePrompt(BannerAiAnalysis analysis) {
        if (analysis == null
                || analysis.getDetectedElements() == null
                || analysis.getDetectedElements().isEmpty()) {
            return COMPARE_PROMPT_BASE;
        }
        StringBuilder sb = new StringBuilder(COMPARE_PROMPT_BASE);
        sb.append("\n아래 detectedElements는 원본 이미지에서 AI가 감지한 중요 요소입니다.\n");
        sb.append("평가 시 반드시 확인하세요:\n");
        sb.append("1. required 요소가 후보 이미지에서 보존되었는가\n");
        sb.append("2. priority 요소가 잘리지 않았는가\n");
        sb.append("3. optional 요소가 잘려도 전체 광고 메시지에 영향이 적은가\n");
        sb.append("4. 제품/사람/메인 카피/가격/CTA가 명확한가\n");
        sb.append("5. 공간이 부족하면 optional 요소 손실은 허용하되 required 요소 손실은 큰 감점 처리하세요.\n");
        sb.append("\n감지된 요소:\n");
        for (BannerAiAnalysis.DetectedElement el : analysis.getDetectedElements()) {
            sb.append(String.format("- [%s] %s (중요도: %s, 그룹: %s)\n",
                    el.getType(), el.getLabel(), el.getImportance(), el.getGroup()));
        }
        if (analysis.getRequiredGroups() != null && !analysis.getRequiredGroups().isEmpty()) {
            sb.append("필수 그룹: ").append(String.join(", ", analysis.getRequiredGroups())).append("\n");
        }
        if (analysis.getPriorityGroups() != null && !analysis.getPriorityGroups().isEmpty()) {
            sb.append("우선순위 그룹: ").append(String.join(", ", analysis.getPriorityGroups())).append("\n");
        }
        sb.append("\n각 후보의 preservedRequiredGroups/lostRequiredGroups/preservedPriorityGroups/lostPriorityGroups를 위 그룹 ID 기준으로 채워줘.\n");
        return sb.toString();
    }

    private static final java.util.Set<String> VALID_CANDIDATES = java.util.Set.of("safe", "balanced", "fill", "focus-fill", "poster-reflow");

    @SuppressWarnings("unchecked")
    public BannerAiCompare compare(String jobId, String specId) throws IOException {
        BannerJob job = bannerMongoService.findById(jobId);
        if (job == null) throw new IllegalArgumentException("Job not found: " + jobId);

        List<BannerSpec> specs = specMongoService.findByIds(List.of(specId));
        if (specs.isEmpty()) throw new IllegalArgumentException("Spec not found: " + specId);
        BannerSpec spec = specs.get(0);

        String compareId = UUID.randomUUID().toString();
        String focalPosition = job.getFocalPosition() != null ? job.getFocalPosition() : "center";

        BannerAiAnalysis analysis = (job.getAiAnalysisId() != null)
                ? analysisMongoService.findById(job.getAiAnalysisId()) : null;

        // AI 분석이 있으면 focus-fill / poster-reflow 후보 추가
        List<String> strengths = new ArrayList<>(List.of("safe", "balanced", "fill"));
        List<CompareWorkerRequest.DetectedElementPayload> detectedPayloads = List.of();
        List<String> reqGroups = List.of();
        List<String> priGroups = List.of();
        List<CompareWorkerRequest.ContentBandPayload> contentBandPayloads = List.of();

        if (analysis != null && analysis.getDetectedElements() != null && !analysis.getDetectedElements().isEmpty()) {
            strengths.add("focus-fill");
            detectedPayloads = analysis.getDetectedElements().stream()
                    .map(el -> CompareWorkerRequest.DetectedElementPayload.builder()
                            .id(el.getId())
                            .type(el.getType())
                            .label(el.getLabel())
                            .group(el.getGroup())
                            .importance(el.getImportance())
                            .bbox(el.getBbox() != null ? CompareWorkerRequest.BboxPayload.builder()
                                    .x(el.getBbox().getX())
                                    .y(el.getBbox().getY())
                                    .width(el.getBbox().getWidth())
                                    .height(el.getBbox().getHeight())
                                    .build() : null)
                            .build())
                    .collect(Collectors.toList());
            reqGroups = analysis.getRequiredGroups() != null ? analysis.getRequiredGroups() : List.of();
            priGroups = analysis.getPriorityGroups() != null ? analysis.getPriorityGroups() : List.of();
        }

        // poster-reflow 후보: reflowRecommended=true 또는 layoutType이 포스터형 && contentBands 존재
        boolean isPosterType = analysis != null && (
                Boolean.TRUE.equals(analysis.getReflowRecommended())
                || "poster_info".equals(analysis.getLayoutType())
                || "horizontal_bands".equals(analysis.getLayoutType()));
        boolean hasContentBands = analysis != null
                && analysis.getContentBands() != null
                && !analysis.getContentBands().isEmpty();
        log.info("poster-reflow 조건: isPoster={} hasBands={} reflowRecommended={} layoutType={} bandsCount={}",
                isPosterType, hasContentBands,
                analysis != null ? analysis.getReflowRecommended() : null,
                analysis != null ? analysis.getLayoutType() : null,
                analysis != null && analysis.getContentBands() != null ? analysis.getContentBands().size() : 0);
        if (isPosterType && hasContentBands) {
            strengths.add("poster-reflow");
            contentBandPayloads = analysis.getContentBands().stream()
                    .map(b -> CompareWorkerRequest.ContentBandPayload.builder()
                            .id(b.getId())
                            .role(b.getRole())
                            .y1(b.getY1())
                            .y2(b.getY2())
                            .importance(b.getImportance())
                            .build())
                    .collect(Collectors.toList());
        }

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
                .strengths(strengths)
                .detectedElements(detectedPayloads)
                .requiredGroups(reqGroups)
                .priorityGroups(priGroups)
                .contentBands(contentBandPayloads)
                .build();

        log.info("Compare 요청: compareId={} jobId={} specId={} spec={}x{} strengths={}", compareId, jobId, specId, spec.getWidth(), spec.getHeight(), strengths);
        CompareWorkerResponse workerResp = workerClient.compare(workerReq);
        if (!workerResp.isSuccess()) {
            throw new IllegalStateException("Worker compare 실패: " + workerResp.getError());
        }

        Map<String, Object> aiResult = callOpenAiCompare(workerResp.getOriginalFilePath(), workerResp.getCandidates(), buildComparePrompt(analysis));

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
                        Object preReq = m.get("preservedRequiredGroups");
                        cr.setPreservedRequiredGroups(preReq instanceof List ? (List<String>) preReq : List.of());
                        Object lostReq = m.get("lostRequiredGroups");
                        cr.setLostRequiredGroups(lostReq instanceof List ? (List<String>) lostReq : List.of());
                        Object prePri = m.get("preservedPriorityGroups");
                        cr.setPreservedPriorityGroups(prePri instanceof List ? (List<String>) prePri : List.of());
                        Object lostPri = m.get("lostPriorityGroups");
                        cr.setLostPriorityGroups(lostPri instanceof List ? (List<String>) lostPri : List.of());
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

        if (!jobId.equals(compare.getJobId()))
            throw new IllegalArgumentException("Compare job mismatch");
        if (!specId.equals(compare.getSpecId()))
            throw new IllegalArgumentException("Compare spec mismatch");

        BannerAiCompare.CandidateResult chosen = compare.getCandidates().stream()
                .filter(c -> candidate.equals(c.getStrength()))
                .findFirst()
                .orElseThrow(() -> new IllegalArgumentException("Candidate not found: " + candidate));

        File candidateFile = new File(chosen.getFilePath());
        if (!candidateFile.exists())
            throw new IllegalArgumentException("Candidate file not found: " + chosen.getFilePath());

        log.info("AI 후보 적용: jobId={} specId={} candidate={}", jobId, specId, candidate);
        return bannerMongoService.applyCompareToResult(jobId, specId, compareId, candidate, chosen.getFilePath());
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> callOpenAiCompare(String originalFilePath,
            List<CompareWorkerResponse.CandidateItem> candidates, String prompt) throws IOException {

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
        textContent.put("text", prompt);
        content.add(textContent);

        Map<String, Object> message = new LinkedHashMap<>();
        message.put("role", "user");
        message.put("content", content);

        Map<String, Object> requestBody = new LinkedHashMap<>();
        requestBody.put("model", "gpt-4.1-mini");
        requestBody.put("messages", List.of(message));
        requestBody.put("max_tokens", 2000);
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
