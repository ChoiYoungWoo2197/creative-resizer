package com.h3.creative.api;

import com.h3.creative.domain.BannerAiAnalysis;
import com.h3.creative.domain.BannerJob;
import com.h3.creative.service.BannerAnalysisService;
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
    private final BannerAnalysisService bannerAnalysisService;

    @PostMapping("/analyze")
    public ResponseEntity<BannerAiAnalysis> analyze(@RequestParam MultipartFile file) throws IOException {
        return ResponseEntity.ok(bannerAnalysisService.analyze(file));
    }

    @PostMapping("/upload")
    public ResponseEntity<BannerJob> upload(
            @RequestParam MultipartFile psdFile,
            @RequestParam String advertiser,
            @RequestParam String campaignName,
            @RequestParam List<String> specIds,
            @RequestParam(defaultValue = "smart-fit") String resizeMode,
            @RequestParam(defaultValue = "balanced") String smartFitStrength,
            @RequestParam(defaultValue = "center") String focalPosition,
            @RequestParam(defaultValue = "png") String outputFormat
    ) throws IOException {
        BannerJob job = bannerService.submit(psdFile, advertiser, campaignName, specIds, resizeMode, smartFitStrength, focalPosition, outputFormat);
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

    @GetMapping("/job/{id}/preview/{filename:.+}")
    public ResponseEntity<Resource> preview(@PathVariable String id, @PathVariable String filename) {
        BannerJob job = bannerService.getJob(id);
        if (job == null || job.getResults() == null) return ResponseEntity.notFound().build();

        BannerJob.BannerResult result = job.getResults().stream()
                .filter(r -> filename.equals(r.getFileName()))
                .findFirst().orElse(null);

        if (result == null) return ResponseEntity.notFound().build();

        File file = new File(result.getFilePath());
        if (!file.exists()) return ResponseEntity.notFound().build();

        MediaType mediaType = filename.endsWith(".jpg") || filename.endsWith(".jpeg")
                ? MediaType.IMAGE_JPEG
                : filename.endsWith(".webp")
                        ? MediaType.parseMediaType("image/webp")
                        : MediaType.IMAGE_PNG;

        return ResponseEntity.ok()
                .contentType(mediaType)
                .body(new FileSystemResource(file));
    }

    @GetMapping("/job/{id}/image/{filename:.+}")
    public ResponseEntity<Resource> downloadImage(@PathVariable String id, @PathVariable String filename) {
        BannerJob job = bannerService.getJob(id);
        if (job == null || job.getResults() == null) return ResponseEntity.notFound().build();

        BannerJob.BannerResult result = job.getResults().stream()
                .filter(r -> filename.equals(r.getFileName()))
                .findFirst().orElse(null);

        if (result == null) return ResponseEntity.notFound().build();

        File file = new File(result.getFilePath());
        if (!file.exists()) return ResponseEntity.notFound().build();

        String encoded = URLEncoder.encode(filename, StandardCharsets.UTF_8).replace("+", "%20");
        return ResponseEntity.ok()
                .contentType(MediaType.APPLICATION_OCTET_STREAM)
                .header(HttpHeaders.CONTENT_DISPOSITION, "attachment; filename*=UTF-8''" + encoded)
                .body(new FileSystemResource(file));
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
