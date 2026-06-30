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
                            String outputFormat) throws IOException {
        if (smartFitStrength == null || smartFitStrength.isBlank()) smartFitStrength = "balanced";

        String filename = UUID.randomUUID() + "_" + psdFile.getOriginalFilename();
        File dest = new File(uploadDir, filename);
        dest.getParentFile().mkdirs();
        psdFile.transferTo(dest);

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
        job.setOutputFormat(outputFormat);
        job.setPsdPath(dest.getAbsolutePath());
        job.setStatus("pending");

        BannerJob saved = bannerMongoService.save(job);

        BannerMessage message = BannerMessage.builder()
                .jobId(saved.getId())
                .psdPath(saved.getPsdPath())
                .specIds(specIds)
                .targetMedia(targetMedia)
                .resizeMode(resizeMode)
                .smartFitStrength(smartFitStrength)
                .outputFormat(outputFormat)
                .build();

        bannerProducer.publish(message);
        return saved;
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
                .outputFormat(message.getOutputFormat())
                .build();

        WorkerResponse response = workerClient.generate(request);

        if (response.isSuccess()) {
            log.info("Job done={} zip={} count={}", jobId, response.getZipPath(), response.getCount());
            List<BannerJob.BannerResult> results = response.getResults() != null
                    ? response.getResults().stream().map(r -> {
                        BannerJob.BannerResult br = new BannerJob.BannerResult();
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

    public BannerJob getJob(String id) {
        return bannerMongoService.findById(id);
    }

    public List<BannerJob> listJobs() {
        return bannerMongoService.findAll();
    }
}
