package com.h3.creative.service;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.h3.creative.domain.BannerSpec;
import com.h3.creative.mongo.SpecMongoService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.core.io.ClassPathResource;
import org.springframework.stereotype.Service;

import java.io.InputStream;
import java.util.List;
import java.util.Map;

@Slf4j
@Service
@RequiredArgsConstructor
public class BannerSpecSeedService {

    private final SpecMongoService specMongoService;
    private final ObjectMapper objectMapper;

    /**
     * banner-specs/{media}.json 을 읽어 MongoDB에 upsert (slug 기준).
     * safeZoneParseStatus == "parsed_text" 또는 "parsed_diagram" 이면 safeZone Map을 자동 구성한다.
     *
     * 반환 Map 키: media, loaded, inserted, updated, total
     */
    public Map<String, Object> seed(String media) {
        String path = "banner-specs/" + media + ".json";
        ClassPathResource resource = new ClassPathResource(path);
        if (!resource.exists()) {
            throw new IllegalArgumentException("seed file not found: " + path);
        }

        List<Map<String, Object>> rawList;
        try (InputStream is = resource.getInputStream()) {
            rawList = objectMapper.readValue(is, new TypeReference<>() {});
        } catch (Exception e) {
            throw new RuntimeException("Failed to parse " + path, e);
        }

        int inserted = 0, updated = 0, unchanged = 0, failed = 0;
        for (Map<String, Object> raw : rawList) {
            try {
                BannerSpec spec = mapToSpec(raw);
                SpecMongoService.UpsertStatus status = specMongoService.upsertBySlugStatus(spec);
                switch (status) {
                    case INSERTED  -> inserted++;
                    case UPDATED   -> updated++;
                    case UNCHANGED -> unchanged++;
                }
            } catch (Exception e) {
                log.warn("Failed to upsert spec slug={}: {}", raw.get("slug"), e.getMessage());
                failed++;
            }
        }
        int total = inserted + updated + unchanged;
        log.info("Seeded media={} total={} inserted={} updated={} unchanged={} failed={}",
                media, total, inserted, updated, unchanged, failed);
        return Map.of(
                "media",     media,
                "loaded",    rawList.size(),
                "inserted",  inserted,
                "updated",   updated,
                "unchanged", unchanged,
                "failed",    failed,
                "total",     total
        );
    }

    private BannerSpec mapToSpec(Map<String, Object> raw) {
        BannerSpec s = new BannerSpec();

        s.setMedia(str(raw, "media"));
        s.setPlacementName(str(raw, "placementName"));
        s.setSlug(str(raw, "slug"));
        s.setWidth(intVal(raw, "width", 0));
        s.setHeight(intVal(raw, "height", 0));
        s.setAspectRatio(str(raw, "ratio"));
        s.setActive(true);
        // sortOrder: 기존 init spec과 겹치지 않도록 큰 값 사용 (slug 기반 hash 대신 단순 순번)
        s.setSortOrder(intVal(raw, "id", 0) + 10000);

        // 8단계 확장 필드
        s.setCategory(str(raw, "category"));
        s.setPlacementType(str(raw, "placementType"));
        s.setSourceUrl(str(raw, "sourceUrl"));
        s.setSourceType(str(raw, "sourceType"));
        s.setSourceRef(str(raw, "sourceRef"));
        s.setRatio(str(raw, "ratio"));
        s.setRatioLabel(str(raw, "ratioLabel"));
        s.setMaxFileSizeKb(intOrNull(raw, "maxFileSizeKb"));
        s.setMinFileSizeKb(intOrNull(raw, "minFileSizeKb"));
        s.setColorSpace(str(raw, "colorSpace"));
        s.setSafeTop(intOrNull(raw, "safeTop"));
        s.setSafeRight(intOrNull(raw, "safeRight"));
        s.setSafeBottom(intOrNull(raw, "safeBottom"));
        s.setSafeLeft(intOrNull(raw, "safeLeft"));
        s.setSafeZoneWidth(intOrNull(raw, "safeZoneWidth"));
        s.setSafeZoneHeight(intOrNull(raw, "safeZoneHeight"));
        s.setSafeZoneParseStatus(str(raw, "safeZoneParseStatus"));
        s.setHeadlineMaxChars(intOrNull(raw, "headlineMaxChars"));
        s.setDescriptionMaxChars(intOrNull(raw, "descriptionMaxChars"));
        s.setTextMaxPct(intOrNull(raw, "textMaxPct"));
        s.setBgTransparent(boolVal(raw, "bgTransparent"));
        s.setIsVideo(boolVal(raw, "isVideo"));
        s.setNotes(str(raw, "notes"));
        s.setNeedsReview(boolVal(raw, "needsReview"));
        s.setLastVerified(str(raw, "lastVerified"));
        s.setLastUpdated(str(raw, "lastUpdated"));

        @SuppressWarnings("unchecked")
        List<String> formats = (List<String>) raw.get("fileFormats");
        s.setFileFormats(formats);

        // parsed_text이면 safeZone Map 자동 구성
        if ("parsed_text".equals(s.getSafeZoneParseStatus())
                && s.getSafeTop() != null && s.getSafeRight() != null
                && s.getSafeBottom() != null && s.getSafeLeft() != null) {
            s.setSafeZone(Map.of(
                    "top",    s.getSafeTop(),
                    "right",  s.getSafeRight(),
                    "bottom", s.getSafeBottom(),
                    "left",   s.getSafeLeft()
            ));
        }

        return s;
    }

    private String str(Map<String, Object> m, String key) {
        Object v = m.get(key);
        return v == null ? null : v.toString();
    }

    private int intVal(Map<String, Object> m, String key, int def) {
        Object v = m.get(key);
        if (v == null) return def;
        try { return ((Number) v).intValue(); } catch (Exception e) { return def; }
    }

    private Integer intOrNull(Map<String, Object> m, String key) {
        Object v = m.get(key);
        if (v == null) return null;
        try { return ((Number) v).intValue(); } catch (Exception e) { return null; }
    }

    private Boolean boolVal(Map<String, Object> m, String key) {
        Object v = m.get(key);
        if (v instanceof Boolean) return (Boolean) v;
        return null;
    }
}
