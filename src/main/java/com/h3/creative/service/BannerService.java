package com.h3.creative.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.h3.creative.domain.BannerJob;
import com.h3.creative.domain.BannerSpec;
import com.h3.creative.domain.PsdObjectAnalysis;
import java.util.ArrayList;
import com.h3.creative.mongo.BannerMongoService;
import com.h3.creative.mongo.PsdObjectAnalysisMongoService;
import com.h3.creative.mongo.SpecMongoService;
import com.h3.creative.queue.message.BannerMessage;
import com.h3.creative.queue.producer.BannerProducer;
import com.h3.creative.worker.WorkerClient;
import com.h3.creative.worker.WorkerRequest;
import com.h3.creative.worker.WorkerResponse;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.web.multipart.MultipartFile;

import java.io.File;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.security.MessageDigest;
import java.util.List;
import java.util.Map;
import java.util.UUID;

@Slf4j
@Service
@RequiredArgsConstructor
public class BannerService {

    private final BannerMongoService bannerMongoService;
    private final SpecMongoService specMongoService;
    private final BannerProducer bannerProducer;
    private final WorkerClient workerClient;
    private final PsdObjectAnalysisMongoService psdObjectAnalysisMongoService;
    private final ObjectMapper objectMapper;

    @Value("${creative.storage.upload-dir}")
    private String uploadDir;

    public BannerJob submit(MultipartFile psdFile, String advertiser, String campaignName,
                            List<String> specIds, String resizeMode, String smartFitStrength,
                            String focalPosition, String outputFormat,
                            String aiAnalysisId, Boolean aiApplied,
                            String aiRecommendedResizeMode, String aiRecommendedSmartFitStrength,
                            String aiRecommendedFocalPosition,
                            String psdMode, List<String> selectedArtboardIds,
                            String objectAnalysisId, Boolean objectReflowEnabled) throws IOException {
        if (smartFitStrength == null || smartFitStrength.isBlank()) smartFitStrength = "balanced";
        if (focalPosition == null || focalPosition.isBlank()) focalPosition = "center";
        if (psdMode == null || psdMode.isBlank()) psdMode = "artboard-first";

        String filename = UUID.randomUUID() + "_" + psdFile.getOriginalFilename();
        File dest = new File(uploadDir, filename);
        dest.getParentFile().mkdirs();
        psdFile.transferTo(dest);

        String sourceType = detectSourceType(psdFile.getOriginalFilename());

        List<BannerSpec> selectedSpecs = specMongoService.findByIds(specIds);
        List<String> targetMedia = selectedSpecs.stream()
                .map(BannerSpec::getMedia).distinct().toList();

        BannerJob job = new BannerJob();
        job.setAdvertiser(advertiser);
        job.setCampaignName(campaignName);
        job.setTargetMedia(targetMedia);
        job.setSpecIds(specIds);
        job.setResizeMode(resizeMode);
        job.setRenderPolicy("ai-auto".equals(resizeMode) ? "ai-only" : "legacy");
        job.setSmartFitStrength(smartFitStrength);
        job.setFocalPosition(focalPosition);
        job.setOutputFormat(outputFormat);
        job.setPsdPath(dest.getAbsolutePath());
        job.setSourceType(sourceType);
        job.setPsdMode("psd".equals(sourceType) ? normalizePsdMode(sourceType, psdMode) : null);
        job.setStatus("pending");
        job.setAiAnalysisId(aiAnalysisId);
        job.setAiApplied(aiApplied != null && aiApplied);
        job.setAiRecommendedResizeMode(aiRecommendedResizeMode);
        job.setAiRecommendedSmartFitStrength(aiRecommendedSmartFitStrength);
        job.setAiRecommendedFocalPosition(aiRecommendedFocalPosition);
        if (selectedArtboardIds != null && !selectedArtboardIds.isEmpty()) {
            job.setSelectedArtboardIds(selectedArtboardIds);
        }
        if (objectAnalysisId != null && !objectAnalysisId.isBlank()) {
            job.setObjectAnalysisId(objectAnalysisId);
        }
        job.setObjectReflowEnabled(objectReflowEnabled != null && objectReflowEnabled);

        // PSD이면 Worker에 분석 요청 → 결과를 job에 저장 (프론트 즉시 표시용)
        if ("psd".equals(sourceType)) {
            try {
                com.h3.creative.worker.PsdAnalyzeResponse analysis =
                        workerClient.analyzePsd(dest.getAbsolutePath());
                if (analysis.getError() == null) {
                    job.setPsdAnalysis(analysis.toPsdAnalysis());
                }
            } catch (Exception e) {
                log.warn("PSD analyze failed, continuing without psdAnalysis: {}", e.getMessage());
            }
        }

        BannerJob saved = bannerMongoService.save(job);

        BannerMessage message = BannerMessage.builder()
                .jobId(saved.getId())
                .psdPath(saved.getPsdPath())
                .specIds(specIds)
                .targetMedia(targetMedia)
                .resizeMode(resizeMode)
                .smartFitStrength(smartFitStrength)
                .focalPosition(focalPosition)
                .outputFormat(outputFormat)
                .sourceType(sourceType)
                .psdMode(job.getPsdMode())
                .selectedArtboardIds(job.getSelectedArtboardIds())
                .objectAnalysisId(job.getObjectAnalysisId())
                .objectReflowEnabled(job.getObjectReflowEnabled())
                .build();

        bannerProducer.publish(message);
        return saved;
    }

    private String detectSourceType(String originalFilename) {
        if (originalFilename != null && originalFilename.toLowerCase().endsWith(".psd")) {
            return "psd";
        }
        return "image";
    }

    private String normalizePsdMode(String sourceType, String psdMode) {
        if (!"psd".equals(sourceType)) return null;
        if ("flatten".equals(psdMode)) return "flatten";
        if ("layer-reflow".equals(psdMode)) return "layer-reflow";
        if ("object-reflow".equals(psdMode)) return "object-reflow";
        return "artboard-first";
    }

    public void process(BannerMessage message) {
        String jobId = message.getJobId();
        bannerMongoService.updateStatus(jobId, "processing");
        log.info("Processing job={} specIds={}", jobId, message.getSpecIds());

        List<BannerSpec> specs;
        if (message.getSpecIds() != null && !message.getSpecIds().isEmpty()) {
            specs = specMongoService.findByIds(message.getSpecIds());
        } else {
            specs = specMongoService.findByMediaIn(message.getTargetMedia());
        }

        if (specs.isEmpty()) {
            log.warn("No specs found for job={}", jobId);
            bannerMongoService.updateFail(jobId, "선택된 규격이 없습니다.");
            return;
        }

        List<WorkerRequest.SpecItem> specItems = specs.stream()
                .map(s -> {
                    // 8단계: parsed_text이고 safeZone Map이 없으면 개별 픽셀 필드로 구성
                    java.util.Map<String, Integer> safeZone = s.getSafeZone();
                    if (safeZone == null && "parsed_text".equals(s.getSafeZoneParseStatus())
                            && s.getSafeTop() != null && s.getSafeRight() != null
                            && s.getSafeBottom() != null && s.getSafeLeft() != null) {
                        safeZone = Map.of(
                                "top",    s.getSafeTop(),
                                "right",  s.getSafeRight(),
                                "bottom", s.getSafeBottom(),
                                "left",   s.getSafeLeft()
                        );
                    }
                    // 8단계: fileRules
                    java.util.Map<String, Object> fileRules = null;
                    if (s.getFileFormats() != null || s.getMaxFileSizeKb() != null) {
                        fileRules = new java.util.HashMap<>();
                        fileRules.put("fileFormats", s.getFileFormats());
                        fileRules.put("maxFileSizeKb", s.getMaxFileSizeKb());
                        fileRules.put("minFileSizeKb", s.getMinFileSizeKb());
                    }
                    return WorkerRequest.SpecItem.builder()
                            .media(s.getMedia())
                            .name(s.getPlacementName())
                            .slug(s.getSlug() != null ? s.getSlug() : "")
                            .width(s.getWidth())
                            .height(s.getHeight())
                            .safeZone(safeZone)
                            .textSafeZone(s.getTextSafeZone())
                            .ctaSafeZone(s.getCtaSafeZone())
                            .safeZoneParseStatus(s.getSafeZoneParseStatus())
                            .fileRules(fileRules)
                            .build();
                })
                .toList();

        // Stage 21: object-reflow 모드 또는 ai-auto 모드일 때 PsdObjectAnalysis 로드 → Map 스냅샷으로 전달
        Map<String, Object> objectAnalysisSnapshot = null;
        boolean objectReflowEnabled = Boolean.TRUE.equals(message.getObjectReflowEnabled());
        boolean isAiAutoMode = "ai-auto".equals(message.getResizeMode());
        if ((objectReflowEnabled || isAiAutoMode) && message.getObjectAnalysisId() != null) {
            try {
                PsdObjectAnalysis oa = psdObjectAnalysisMongoService.findById(message.getObjectAnalysisId());
                if (oa == null) {
                    log.warn("OBJECT_ANALYSIS_NOT_FOUND id={}", message.getObjectAnalysisId());
                } else if (!"READY".equals(oa.getStatus())) {
                    log.warn("OBJECT_ANALYSIS_NOT_READY id={} status={}", message.getObjectAnalysisId(), oa.getStatus());
                } else {
                    // Source hash validation: stored snapshot must match current PSD file
                    String storedSha = oa.getSourceFileSha256();
                    boolean shaValidated = false;
                    if (storedSha != null && !storedSha.isBlank() && !storedSha.startsWith("__")) {
                        String actualSha = computeFileSha256(message.getPsdPath());
                        if (!storedSha.equals(actualSha)) {
                            String s = storedSha.length() >= 16 ? storedSha.substring(0, 16) : storedSha;
                            String a = actualSha.length() >= 16 ? actualSha.substring(0, 16) : actualSha;
                            log.error("OBJECT_ANALYSIS_SOURCE_HASH_MISMATCH id={} stored={} actual={}",
                                message.getObjectAnalysisId(), s, a);
                            // fail-closed: mismatched snapshot is not applied
                        } else {
                            shaValidated = true;
                        }
                    } else {
                        shaValidated = true; // no hash stored — skip validation
                    }
                    if (shaValidated) {
                        objectAnalysisSnapshot = objectMapper.convertValue(oa, Map.class);
                        log.info("[PSD_OBJECT_ANALYSIS] trigger=generate source=stored-snapshot"
                            + " analysisCacheHit=true reused=true gptRequestCount=0"
                            + " analysisId={} analysisVersion={} model={}",
                            message.getObjectAnalysisId(), oa.getAnalysisVersion(), oa.getModel());
                    }
                }
            } catch (Exception e) {
                log.warn("PsdObjectAnalysis 로딩 실패 id={}: {}", message.getObjectAnalysisId(), e.getMessage());
            }
        }

        WorkerRequest request = WorkerRequest.builder()
                .jobId(jobId)
                .psdPath(message.getPsdPath())
                .specs(specItems)
                .resizeMode(message.getResizeMode())
                .smartFitStrength(message.getSmartFitStrength())
                .focalPosition(message.getFocalPosition())
                .outputFormat(message.getOutputFormat())
                .sourceType(message.getSourceType() != null ? message.getSourceType() : "image")
                .psdMode(message.getPsdMode() != null ? message.getPsdMode() : "artboard-first")
                .selectedArtboardIds(message.getSelectedArtboardIds())
                .objectReflowEnabled(objectReflowEnabled)
                .objectAnalysis(objectAnalysisSnapshot)
                .build();

        WorkerResponse response = workerClient.generate(request);

        if (response.isSuccess()) {
            log.info("Job done={} zip={} count={}", jobId, response.getZipPath(), response.getCount());
            List<BannerJob.BannerResult> results = response.getResults() != null
                    ? response.getResults().stream().map(r -> {
                        BannerJob.BannerResult br = new BannerJob.BannerResult();
                        // slug+width+height로 specId 역매핑
                        specs.stream()
                                .filter(s -> r.getWidth() == s.getWidth() && r.getHeight() == s.getHeight()
                                        && java.util.Objects.equals(r.getSlug(), s.getSlug() != null ? s.getSlug() : ""))
                                .findFirst()
                                .ifPresent(s -> br.setSpecId(s.getId()));
                        br.setMedia(r.getMedia());
                        br.setName(r.getName());
                        br.setSlug(r.getSlug());
                        br.setWidth(r.getWidth());
                        br.setHeight(r.getHeight());
                        br.setFileName(r.getFileName());
                        br.setFilePath(r.getFilePath());
                        br.setFileSize(r.getFileSize());
                        br.setValid(r.getValid());
                        br.setValidationMessage(r.getValidationMessage());
                        br.setSelectedArtboardId(r.getSelectedArtboardId());
                        br.setSelectedArtboardName(r.getSelectedArtboardName());
                        br.setSelectedArtboardType(r.getSelectedArtboardType());
                        br.setSelectedArtboardBox(r.getSelectedArtboardBox());
                        br.setArtboardMatchScore(r.getArtboardMatchScore());
                        br.setSelectedSourceArtboardSize(r.getSelectedSourceArtboardSize());
                        br.setSourceMatchType(r.getSourceMatchType());
                        br.setActualPsdRenderMode(r.getActualPsdRenderMode());
                        br.setRenderSource(r.getRenderSource());
                        br.setFallbackUsed(r.getFallbackUsed());
                        br.setFallbackReason(r.getFallbackReason());
                        br.setFallbackErrors(r.getFallbackErrors());
                        br.setSourceWidth(r.getSourceWidth());
                        br.setSourceHeight(r.getSourceHeight());
                        br.setResizeStrategy(r.getResizeStrategy());
                        br.setCandidateType(r.getCandidateType());
                        br.setCandidateScore(r.getCandidateScore());
                        br.setBlurAreaRatio(r.getBlurAreaRatio());
                        br.setCropRatio(r.getCropRatio());
                        br.setSubjectScale(r.getSubjectScale());
                        br.setSafeZonePass(r.getSafeZonePass());
                        br.setRequiredLayerMissing(r.getRequiredLayerMissing());
                        br.setQualityGate(r.getQualityGate());
                        br.setQualityLabel(r.getQualityLabel());
                        br.setLayerReflowAttempted(r.getLayerReflowAttempted());
                        br.setLayerReflowSucceeded(r.getLayerReflowSucceeded());
                        br.setLayerReflowError(r.getLayerReflowError());
                        br.setLayerReflowExtractedLayerCount(r.getLayerReflowExtractedLayerCount());
                        br.setLayerReflowDetectedRoles(r.getLayerReflowDetectedRoles());
                        br.setLayerReflowTemplate(r.getLayerReflowTemplate());
                        br.setUsedLayerRoles(r.getUsedLayerRoles());
                        br.setObjectReflowAttempted(r.getObjectReflowAttempted());
                        br.setObjectReflowSucceeded(r.getObjectReflowSucceeded());
                        br.setObjectReflowMode(r.getObjectReflowMode());
                        br.setObjectReflowFallbackReason(r.getObjectReflowFallbackReason());
                        br.setUsedObjectRoles(r.getUsedObjectRoles());
                        br.setMissingObjectRoles(r.getMissingObjectRoles());
                        br.setCropFallbackRoles(r.getCropFallbackRoles());
                        br.setLowConfidenceRoles(r.getLowConfidenceRoles());
                        br.setObjectSafeZonePass(r.getObjectSafeZonePass());
                        br.setRenderMode(r.getRenderMode());
                        br.setObjectReflowUsed(r.getObjectReflowUsed());
                        br.setObjectReflowFallbackUsed(r.getObjectReflowFallbackUsed());
                        br.setLayoutScore(r.getLayoutScore());
                        br.setBackgroundMode(r.getBackgroundMode());
                        br.setCandidateCount(r.getCandidateCount());
                        br.setSelectedCandidateId(r.getSelectedCandidateId());
                        br.setSafeZonePassed(r.getSafeZonePassed());
                        br.setLayoutScoreStatus(r.getLayoutScoreStatus());
                        br.setSafeZoneViolations(r.getSafeZoneViolations());
                        // 9단계: Layout Repair & Quality Meta
                        br.setRepairAttempted(r.getRepairAttempted());
                        br.setRepairApplied(r.getRepairApplied());
                        br.setRepairReasons(r.getRepairReasons());
                        br.setRepairedObjects(r.getRepairedObjects());
                        br.setScoringBreakdown(r.getScoringBreakdown());
                        br.setDuplicateObjectsRemoved(r.getDuplicateObjectsRemoved());
                        br.setCtaGroupCreated(r.getCtaGroupCreated());
                        // 9단계: Debug Overlay Optional Fields
                        br.setCtaVisible(r.getCtaVisible());
                        br.setCtaOccluded(r.getCtaOccluded());
                        br.setCtaInsideSafeZone(r.getCtaInsideSafeZone());
                        br.setCtaGroupBbox(r.getCtaGroupBbox());
                        br.setHeadlineVisible(r.getHeadlineVisible());
                        br.setHeadlineClamped(r.getHeadlineClamped());
                        br.setHeadlineScaled(r.getHeadlineScaled());
                        br.setHeadlineOverflowFixed(r.getHeadlineOverflowFixed());
                        br.setBlurFallbackUsed(r.getBlurFallbackUsed());
                        br.setRenderProvenance(r.getRenderProvenance());
                        return br;
                    }).toList()
                    : List.of();

            // missingRatioTypes: 워커가 감지하지 못한 비율 타입 목록 → job에 저장
            if (response.getMissingRatioTypes() != null && !response.getMissingRatioTypes().isEmpty()) {
                BannerJob job = bannerMongoService.findById(jobId);
                if (job != null) {
                    job.setMissingRatioTypes(response.getMissingRatioTypes());
                    bannerMongoService.save(job);
                }
            }

            boolean hasInvalid = results.stream().anyMatch(r -> Boolean.FALSE.equals(r.getValid()));
            if (hasInvalid) {
                log.warn("Job validation failed={} — 규격 불일치 이미지 존재", jobId);
                bannerMongoService.updateFailWithResults(jobId, "생성 이미지 크기가 요청 규격과 다릅니다.", results);
                return;
            }

            bannerMongoService.updateDone(jobId, response.getZipPath(), results);
        } else {
            log.error("Job failed={} error={}", jobId, response.getError());
            bannerMongoService.updateFail(jobId, response.getError());
        }
    }

    public com.h3.creative.domain.PsdAnalysis analyzePsdLayers(MultipartFile psdFile) throws IOException {
        String filename = UUID.randomUUID() + "_" + psdFile.getOriginalFilename();
        File dest = new File(uploadDir, filename);
        dest.getParentFile().mkdirs();
        psdFile.transferTo(dest);
        try {
            com.h3.creative.worker.PsdAnalyzeResponse response = workerClient.analyzePsd(dest.getAbsolutePath());
            if (response.getError() != null) {
                com.h3.creative.domain.PsdAnalysis failed = new com.h3.creative.domain.PsdAnalysis();
                failed.setLayerReadable(false);
                failed.setLayerReadError(response.getError());
                failed.setLayerReflowAvailable(false);
                return failed;
            }
            return response.toPsdAnalysis();
        } finally {
            dest.delete();
        }
    }

    public BannerJob getJob(String id) {
        return bannerMongoService.findById(id);
    }

    public List<BannerJob> listJobs() {
        return bannerMongoService.findAll();
    }

    private String computeFileSha256(String path) throws Exception {
        MessageDigest md = MessageDigest.getInstance("SHA-256");
        byte[] data = Files.readAllBytes(Paths.get(path));
        byte[] digest = md.digest(data);
        StringBuilder sb = new StringBuilder(64);
        for (byte b : digest) sb.append(String.format("%02x", b));
        return sb.toString();
    }
}
