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
                            List<String> targetMedia, String resizeMode, String outputFormat) throws IOException {

        String filename = UUID.randomUUID() + "_" + psdFile.getOriginalFilename();
        File dest = new File(uploadDir, filename);
        dest.getParentFile().mkdirs();
        psdFile.transferTo(dest);

        BannerJob job = new BannerJob();
        job.setAdvertiser(advertiser);
        job.setCampaignName(campaignName);
        job.setTargetMedia(targetMedia);
        job.setResizeMode(resizeMode);
        job.setOutputFormat(outputFormat);
        job.setPsdPath(dest.getAbsolutePath());
        job.setStatus("pending");

        BannerJob saved = bannerMongoService.save(job);

        BannerMessage message = BannerMessage.builder()
                .jobId(saved.getId())
                .psdPath(saved.getPsdPath())
                .targetMedia(targetMedia)
                .resizeMode(resizeMode)
                .outputFormat(outputFormat)
                .build();

        bannerProducer.publish(message);
        return saved;
    }

    public void process(BannerMessage message) {
        String jobId = message.getJobId();
        bannerMongoService.updateStatus(jobId, "processing");
        log.info("Processing job={} media={}", jobId, message.getTargetMedia());

        List<BannerSpec> specs = specMongoService.findByMediaIn(message.getTargetMedia());
        if (specs.isEmpty()) {
            log.warn("No active specs found for job={} media={}", jobId, message.getTargetMedia());
            bannerMongoService.updateFail(jobId, "등록된 규격이 없습니다: " + message.getTargetMedia());
            return;
        }

        List<WorkerRequest.SpecItem> specItems = specs.stream()
                .map(s -> WorkerRequest.SpecItem.builder()
                        .media(s.getMedia())
                        .placementName(s.getPlacementName())
                        .width(s.getWidth())
                        .height(s.getHeight())
                        .build())
                .toList();

        WorkerRequest request = WorkerRequest.builder()
                .jobId(jobId)
                .psdPath(message.getPsdPath())
                .specs(specItems)
                .resizeMode(message.getResizeMode())
                .outputFormat(message.getOutputFormat())
                .build();

        WorkerResponse response = workerClient.generate(request);

        if (response.isSuccess()) {
            log.info("Job done={} zip={} count={}", jobId, response.getZipPath(), response.getCount());
            bannerMongoService.updateDone(jobId, response.getZipPath());
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
