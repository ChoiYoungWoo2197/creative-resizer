package com.h3.creative.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.h3.creative.domain.BannerSpec;
import com.h3.creative.mongo.SpecMongoService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

/**
 * BannerSpecSeedService 단위 테스트.
 * 실제 JSON 파일(src/main/resources/banner-specs/naver.json)을 읽어
 * 매핑·카운트 로직을 검증한다.
 * MongoDB 없이 SpecMongoService를 mock으로 대체.
 */
@ExtendWith(MockitoExtension.class)
class BannerSpecSeedServiceTest {

    @Mock
    private SpecMongoService specMongoService;

    private BannerSpecSeedService service;

    @BeforeEach
    void setUp() {
        service = new BannerSpecSeedService(specMongoService, new ObjectMapper());
    }

    // ─── 기본 카운트 검증 ────────────────────────────────────────────────────

    @Test
    void seed_naver_loads68Specs_allNew() {
        when(specMongoService.upsertBySlug(any())).thenReturn(true);

        Map<String, Object> result = service.seed("naver");

        assertEquals("naver", result.get("media"));
        assertEquals(68, result.get("loaded"),  "loaded should be 68");
        assertEquals(68, result.get("inserted"), "all new → inserted=68");
        assertEquals(0,  result.get("updated"),  "all new → updated=0");
        assertEquals(68, result.get("total"),    "total=68");
        verify(specMongoService, times(68)).upsertBySlug(any());
    }

    @Test
    void seed_naver_idempotent_allUpdated() {
        // 2회차 seed: 모두 이미 존재 → updated
        when(specMongoService.upsertBySlug(any())).thenReturn(false);

        Map<String, Object> result = service.seed("naver");

        assertEquals(0,  result.get("inserted"), "2nd seed → inserted=0");
        assertEquals(68, result.get("updated"),  "2nd seed → updated=68");
        assertEquals(68, result.get("total"));
    }

    // ─── safeZone 매핑 검증 ──────────────────────────────────────────────────

    @Test
    void seed_parsedTextSpecs_haveSafeZone() {
        // parsed_text 3건은 safeZone Map이 자동으로 채워져야 한다
        when(specMongoService.upsertBySlug(any())).thenAnswer(inv -> {
            BannerSpec spec = inv.getArgument(0);
            if ("parsed_text".equals(spec.getSafeZoneParseStatus())) {
                assertNotNull(spec.getSafeZone(),
                        "parsed_text spec must have safeZone: slug=" + spec.getSlug());
                assertEquals(50,  spec.getSafeZone().get("top"),    "top=50");
                assertEquals(240, spec.getSafeZone().get("right"),  "right=240");
                assertEquals(35,  spec.getSafeZone().get("bottom"), "bottom=35");
                assertEquals(240, spec.getSafeZone().get("left"),   "left=240");
            }
            return true;
        });
        service.seed("naver");
    }

    @Test
    void seed_diagramUnreadableSpecs_haveNoSafeZone() {
        // diagram_unreadable 65건은 safeZone이 null이어야 한다
        when(specMongoService.upsertBySlug(any())).thenAnswer(inv -> {
            BannerSpec spec = inv.getArgument(0);
            if ("diagram_unreadable".equals(spec.getSafeZoneParseStatus())) {
                assertNull(spec.getSafeZone(),
                        "diagram_unreadable spec must NOT have safeZone: slug=" + spec.getSlug());
            }
            return true;
        });
        service.seed("naver");
    }

    // ─── parseStatus 건수 검증 ───────────────────────────────────────────────

    @Test
    void seed_naver_parsedText_count3() {
        int[] parsedCount = {0};
        when(specMongoService.upsertBySlug(any())).thenAnswer(inv -> {
            BannerSpec spec = inv.getArgument(0);
            if ("parsed_text".equals(spec.getSafeZoneParseStatus())) parsedCount[0]++;
            return true;
        });
        service.seed("naver");
        assertEquals(3, parsedCount[0], "parsed_text should be 3");
    }

    @Test
    void seed_naver_diagramUnreadable_count65() {
        int[] count = {0};
        when(specMongoService.upsertBySlug(any())).thenAnswer(inv -> {
            BannerSpec spec = inv.getArgument(0);
            if ("diagram_unreadable".equals(spec.getSafeZoneParseStatus())) count[0]++;
            return true;
        });
        service.seed("naver");
        assertEquals(65, count[0], "diagram_unreadable should be 65");
    }

    @Test
    void seed_naver_needsReview_count1() {
        int[] count = {0};
        when(specMongoService.upsertBySlug(any())).thenAnswer(inv -> {
            BannerSpec spec = inv.getArgument(0);
            if (Boolean.TRUE.equals(spec.getNeedsReview())) count[0]++;
            return true;
        });
        service.seed("naver");
        assertEquals(1, count[0], "needsReview=true should be 1");
    }

    // ─── 개별 필드 검증 ──────────────────────────────────────────────────────

    @Test
    void seed_naver_gfaSpec_hasCorrectDimensions() {
        // slug=naver-gfa-mobile-da-image-banner-1250x560 → width=1250, height=560
        BannerSpec[] found = {null};
        when(specMongoService.upsertBySlug(any())).thenAnswer(inv -> {
            BannerSpec spec = inv.getArgument(0);
            if ("naver-gfa-mobile-da-image-banner-1250x560".equals(spec.getSlug())) {
                found[0] = spec;
            }
            return true;
        });
        service.seed("naver");

        assertNotNull(found[0], "GFA spec must be found");
        assertEquals(1250, found[0].getWidth());
        assertEquals(560,  found[0].getHeight());
        assertEquals("naver", found[0].getMedia());
        assertEquals("parsed_text", found[0].getSafeZoneParseStatus());
        assertNotNull(found[0].getSafeZone());
    }

    @Test
    void seed_naver_allSlugsUnique() {
        java.util.Set<String> slugs = new java.util.HashSet<>();
        when(specMongoService.upsertBySlug(any())).thenAnswer(inv -> {
            BannerSpec spec = inv.getArgument(0);
            assertTrue(slugs.add(spec.getSlug()),
                    "duplicate slug detected: " + spec.getSlug());
            return true;
        });
        service.seed("naver");
        assertEquals(68, slugs.size());
    }

    // ─── 오류 케이스 ─────────────────────────────────────────────────────────

    @Test
    void seed_unknownMedia_throwsIllegalArgument() {
        assertThrows(IllegalArgumentException.class,
                () -> service.seed("unknown_media_xyz"),
                "unknown media should throw IllegalArgumentException");
    }
}
