package com.h3.creative.api;

import com.h3.creative.domain.BannerSpec;
import com.h3.creative.mongo.SpecMongoService;
import com.h3.creative.service.BannerSpecSeedService;
import lombok.RequiredArgsConstructor;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Map;

/**
 * 8단계: BannerSpec DB API.
 * 기존 /api/spec 과 별도 경로로 분리 — 기존 flow 무영향.
 */
@RestController
@RequestMapping("/api/banner-specs")
@RequiredArgsConstructor
public class BannerSpecController {

    private final SpecMongoService specMongoService;
    private final BannerSpecSeedService bannerSpecSeedService;

    /** GET /api/banner-specs[?media=naver][&placementType=display] */
    @GetMapping
    public ResponseEntity<List<BannerSpec>> list(
            @RequestParam(required = false) String media,
            @RequestParam(required = false) String placementType
    ) {
        if (media != null && placementType != null) {
            List<BannerSpec> all = specMongoService.findByMedia(media);
            List<BannerSpec> filtered = all.stream()
                    .filter(s -> placementType.equals(s.getPlacementType()))
                    .toList();
            return ResponseEntity.ok(filtered);
        }
        if (media != null) {
            return ResponseEntity.ok(specMongoService.findByMedia(media));
        }
        if (placementType != null) {
            return ResponseEntity.ok(specMongoService.findByPlacementType(placementType));
        }
        return ResponseEntity.ok(specMongoService.findAllActive());
    }

    /** GET /api/banner-specs/{media}/{slug} */
    @GetMapping("/{media}/{slug}")
    public ResponseEntity<?> getOne(
            @PathVariable String media,
            @PathVariable String slug
    ) {
        BannerSpec spec = specMongoService.findByMediaAndSlug(media, slug);
        if (spec == null) {
            return ResponseEntity.notFound().build();
        }
        return ResponseEntity.ok(spec);
    }

    /**
     * POST /api/banner-specs/seed?media=naver
     * banner-specs/{media}.json → MongoDB upsert (slug 기준, idempotent).
     * 반복 호출해도 중복 insert 없음 — slug 기준 upsert이므로 항상 안전.
     * 응답: {media, loaded, inserted, updated, total}
     */
    @PostMapping("/seed")
    public ResponseEntity<Map<String, Object>> seed(
            @RequestParam String media
    ) {
        Map<String, Object> result = bannerSpecSeedService.seed(media);
        return ResponseEntity.ok(result);
    }

    /**
     * POST /api/banner-specs/deactivate-legacy-naver
     * 레거시 Naver 기본 규격 8개(smartchannel_horizontal, pc_display, …)를
     * active=false 처리한다. 물리 삭제 없음. 재실행 idempotent.
     * 응답: {foundSlugs, foundCount, activeAtStart, matchedCount, modifiedCount}
     */
    @PostMapping("/deactivate-legacy-naver")
    public ResponseEntity<Map<String, Object>> deactivateLegacyNaver() {
        Map<String, Object> result = specMongoService.deactivateLegacyNaverSpecs();
        return ResponseEntity.ok(result);
    }
}
