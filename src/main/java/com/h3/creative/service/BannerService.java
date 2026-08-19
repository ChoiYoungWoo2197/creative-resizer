package com.h3.creative.service;

import com.h3.creative.domain.BannerJob;
import com.h3.creative.domain.BannerSpec;
import com.h3.creative.mongo.BannerMongoService;
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
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

@Slf4j
@Service
@RequiredArgsConstructor
public class BannerService {

    private final BannerMongoService bannerMongoService;
    private final SpecMongoService specMongoService;
    private final BannerProducer bannerProducer;
    private final WorkerClient workerClient;

    /** 요청에 pipelineVersion이 누락될 때 사용할 기본값. 모든 계층의 단일 기준. */
    public static final String DEFAULT_PIPELINE_VERSION = "clean_v1";
    private static final Set<String> VALID_PIPELINE_VERSIONS = Set.of("clean_v1");

    @Value("${creative.storage.upload-dir}")
    private String uploadDir;

    public BannerJob submit(MultipartFile psdFile, String advertiser, String campaignName,
                            List<String> specIds, String resizeMode, String smartFitStrength,
                            String focalPosition, String outputFormat,
                            String aiAnalysisId, Boolean aiApplied,
                            String aiRecommendedResizeMode, String aiRecommendedSmartFitStrength,
                            String aiRecommendedFocalPosition,
                            String psdMode, List<String> selectedArtboardIds,
                            String objectAnalysisId, Boolean objectReflowEnabled,
                            String pipelineVersion, String pipelineType) throws IOException {
        if (smartFitStrength == null || smartFitStrength.isBlank()) smartFitStrength = "balanced";
        if (focalPosition == null || focalPosition.isBlank()) focalPosition = "center";
        if (psdMode == null || psdMode.isBlank()) psdMode = "artboard-first";

        // ── pipelineVersion 단일 기준 정규화 (요청 진입 지점) ──────────────────
        String pipelineVersionSource;
        if (pipelineVersion == null || pipelineVersion.isBlank()) {
            pipelineVersion = DEFAULT_PIPELINE_VERSION;
            pipelineVersionSource = "default";
        } else if (!VALID_PIPELINE_VERSIONS.contains(pipelineVersion)) {
            throw new IllegalArgumentException(
                "Unsupported pipelineVersion: '" + pipelineVersion + "'. Allowed: " + VALID_PIPELINE_VERSIONS);
        } else {
            pipelineVersionSource = "explicit";
        }
        log.info("[PIPELINE_VERSION] resolved={} source={}", pipelineVersion, pipelineVersionSource);

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
        job.setPipelineVersion(pipelineVersion);

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
                .pipelineVersion(pipelineVersion)
                .pipelineType(pipelineType)
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

        // Queue 메시지에서 pipelineVersion이 누락된 경우(레거시 메시지) 방어적 정규화
        String resolvedPipelineVersion = message.getPipelineVersion();
        if (resolvedPipelineVersion == null || resolvedPipelineVersion.isBlank()) {
            resolvedPipelineVersion = DEFAULT_PIPELINE_VERSION;
            log.info("[PIPELINE_VERSION] defaulted in process() jobId={} — legacy queue message", jobId);
        }

        WorkerRequest request = WorkerRequest.builder()
                .jobId(jobId)
                .psdPath(message.getPsdPath())
                .specs(specItems)
                .outputFormat(message.getOutputFormat())
                .pipelineVersion(resolvedPipelineVersion)
                .pipelineType(message.getPipelineType())
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
                        br.setRenderSource(r.getRenderSource());
                        br.setFallbackUsed(r.getFallbackUsed());
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

}
