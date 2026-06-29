package com.h3.creative.service;

import com.h3.creative.domain.BannerJob;
import com.h3.creative.mongo.BannerMongoService;
import com.h3.creative.queue.message.BannerMessage;
import com.h3.creative.queue.producer.BannerProducer;
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
    private final BannerProducer bannerProducer;

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
        bannerMongoService.updateStatus(message.getJobId(), "processing");
        // Worker 호출은 2단계에서 구현
        log.info("Job {} queued for worker processing", message.getJobId());
    }

    public BannerJob getJob(String id) {
        return bannerMongoService.findById(id);
    }

    public List<BannerJob> listJobs() {
        return bannerMongoService.findAll();
    }
}
