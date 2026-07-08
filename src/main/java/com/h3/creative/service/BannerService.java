package com.h3.creative.service;

import com.h3.creative.domain.BannerJob;
import com.h3.creative.domain.BannerSpec;
import java.util.ArrayList;
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
import java.util.UUID;

@Slf4j
@Service
@RequiredArgsConstructor
public class BannerService {

    private final BannerMongoService bannerMongoService;
    private final SpecMongoService specMongoService;
    private final BannerProducer bannerProducer;
    private final WorkerClient workerClient;

    @Value("${creative.storage.upload-dir}")
    private String uploadDir;

    public BannerJob submit(MultipartFile psdFile, String advertiser, String campaignName,
                            List<String> specIds, String resizeMode, String smartFitStrength,
                            String focalPosition, String outputFormat,
                            String aiAnalysisId, Boolean aiApplied,
                            String aiRecommendedResizeMode, String aiRecommendedSmartFitStrength,
                            String aiRecommendedFocalPosition,
                            String psdMode) throws IOException {
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
                .map(s -> WorkerRequest.SpecItem.builder()
                        .media(s.getMedia())
                        .name(s.getPlacementName())
                        .slug(s.getSlug() != null ? s.getSlug() : "")
                        .width(s.getWidth())
                        .height(s.getHeight())
                        .build())
                .toList();

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
                        br.setLayerReflowAttempted(r.getLayerReflowAttempted());
                        br.setLayerReflowSucceeded(r.getLayerReflowSucceeded());
                        br.setLayerReflowError(r.getLayerReflowError());
                        br.setLayerReflowExtractedLayerCount(r.getLayerReflowExtractedLayerCount());
                        br.setLayerReflowDetectedRoles(r.getLayerReflowDetectedRoles());
                        br.setLayerReflowTemplate(r.getLayerReflowTemplate());
                        br.setUsedLayerRoles(r.getUsedLayerRoles());
                        return br;
                    }).toList()
                    : List.of();

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
