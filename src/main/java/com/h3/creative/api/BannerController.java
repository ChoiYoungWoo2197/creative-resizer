package com.h3.creative.api;

import com.h3.creative.domain.BannerJob;
import com.h3.creative.domain.BannerSpec;
import com.h3.creative.mongo.SpecMongoService;
import com.h3.creative.service.BannerService;
import lombok.RequiredArgsConstructor;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;
import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/api/banner")
@RequiredArgsConstructor
public class BannerController {

    private final BannerService bannerService;
    private final SpecMongoService specMongoService;

    @PostMapping("/upload")
    public ResponseEntity<BannerJob> upload(
            @RequestParam MultipartFile psdFile,
            @RequestParam String advertiser,
            @RequestParam String campaignName,
            @RequestParam List<String> targetMedia,
            @RequestParam(defaultValue = "cover") String resizeMode,
            @RequestParam(defaultValue = "png") String outputFormat
    ) throws IOException {
        BannerJob job = bannerService.submit(psdFile, advertiser, campaignName, targetMedia, resizeMode, outputFormat);
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

    @GetMapping("/spec")
    public ResponseEntity<List<BannerSpec>> listSpecs(
            @RequestParam(required = false) String media
    ) {
        if (media != null) {
            return ResponseEntity.ok(specMongoService.findByMedia(media));
        }
        return ResponseEntity.ok(specMongoService.findAll());
    }

    @PostMapping("/spec")
    public ResponseEntity<BannerSpec> saveSpec(@RequestBody BannerSpec spec) {
        return ResponseEntity.ok(specMongoService.save(spec));
    }

    @DeleteMapping("/spec/{id}")
    public ResponseEntity<Map<String, String>> deleteSpec(@PathVariable String id) {
        specMongoService.deleteById(id);
        return ResponseEntity.ok(Map.of("result", "ok"));
    }
}
