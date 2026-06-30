package com.h3.creative.api;

import com.h3.creative.domain.BannerJob;
import com.h3.creative.service.BannerService;
import lombok.RequiredArgsConstructor;
import org.springframework.core.io.FileSystemResource;
import org.springframework.core.io.Resource;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;

import java.io.File;
import java.io.IOException;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.List;

@RestController
@RequestMapping("/api/banner")
@RequiredArgsConstructor
public class BannerController {

    private final BannerService bannerService;

    @PostMapping("/upload")
    public ResponseEntity<BannerJob> upload(
            @RequestParam MultipartFile psdFile,
            @RequestParam String advertiser,
            @RequestParam String campaignName,
            @RequestParam List<String> specIds,
            @RequestParam(defaultValue = "cover") String resizeMode,
            @RequestParam(defaultValue = "png") String outputFormat
    ) throws IOException {
        BannerJob job = bannerService.submit(psdFile, advertiser, campaignName, specIds, resizeMode, outputFormat);
        return ResponseEntity.ok(job);
    }

    @GetMapping("/job/{id}")
    public ResponseEntity<BannerJob> getJob(@PathVariable String id) {
        BannerJob job = bannerService.getJob(id);
        if (job == null) return ResponseEntity.notFound().build();
        return ResponseEntity.ok(job);
    }

    @GetMapping("/jobs")
    public ResponseEntity<List<BannerJob>> listJobs() {
        return ResponseEntity.ok(bannerService.listJobs());
    }

    @GetMapping("/job/{id}/download")
    public ResponseEntity<Resource> download(@PathVariable String id) {
        BannerJob job = bannerService.getJob(id);
        if (job == null || !"done".equals(job.getStatus())) {
            return ResponseEntity.notFound().build();
        }

        File zip = new File(job.getZipPath());
        if (!zip.exists()) {
            return ResponseEntity.notFound().build();
        }

        String rawName = job.getAdvertiser() + "_" + job.getCampaignName() + ".zip";
        String encoded = URLEncoder.encode(rawName, StandardCharsets.UTF_8).replace("+", "%20");
        Resource resource = new FileSystemResource(zip);

        return ResponseEntity.ok()
                .contentType(MediaType.APPLICATION_OCTET_STREAM)
                .header(HttpHeaders.CONTENT_DISPOSITION, "attachment; filename*=UTF-8''" + encoded)
                .body(resource);
    }
}
