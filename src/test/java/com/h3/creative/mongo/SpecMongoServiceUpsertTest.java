package com.h3.creative.mongo;

import com.h3.creative.domain.BannerSpec;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.data.mongodb.core.MongoTemplate;
import org.springframework.data.mongodb.core.query.Query;

import static com.h3.creative.mongo.SpecMongoService.UpsertStatus.*;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.*;

/**
 * SpecMongoService.upsertBySlugStatus / upsertBySlug 로직 단위 테스트.
 * MongoTemplate을 mock해서 INSERTED / UPDATED / UNCHANGED 반환 조건 검증.
 */
@ExtendWith(MockitoExtension.class)
class SpecMongoServiceUpsertTest {

    @Mock
    private MongoTemplate mongoTemplate;

    @InjectMocks
    private SpecMongoService service;

    // ─── upsertBySlugStatus ──────────────────────────────────────────────────

    @Test
    void status_newSpec_returnsInserted() {
        when(mongoTemplate.findOne(any(Query.class), eq(BannerSpec.class))).thenReturn(null);

        BannerSpec spec = makeSpec("slug-new", null, 1200, 628);
        SpecMongoService.UpsertStatus status = service.upsertBySlugStatus(spec);

        assertEquals(INSERTED, status);
        verify(mongoTemplate).save(spec);
        assertNull(spec.getId(), "new spec has no id");
    }

    @Test
    void status_existingSpec_sameContent_returnsUnchanged_noSave() {
        BannerSpec existing = makeSpec("slug-x", "id-123", 1200, 628);
        when(mongoTemplate.findOne(any(Query.class), eq(BannerSpec.class))).thenReturn(existing);

        BannerSpec incoming = makeSpec("slug-x", null, 1200, 628); // same content
        SpecMongoService.UpsertStatus status = service.upsertBySlugStatus(incoming);

        assertEquals(UNCHANGED, status, "same content → UNCHANGED");
        assertEquals("id-123", incoming.getId(), "id inherited from existing");
        verify(mongoTemplate, never()).save(any()); // no write for unchanged
    }

    @Test
    void status_existingSpec_changedContent_returnsUpdated_withSave() {
        BannerSpec existing = makeSpec("slug-x", "id-123", 1200, 628);
        when(mongoTemplate.findOne(any(Query.class), eq(BannerSpec.class))).thenReturn(existing);

        BannerSpec incoming = makeSpec("slug-x", null, 800, 600); // different size
        SpecMongoService.UpsertStatus status = service.upsertBySlugStatus(incoming);

        assertEquals(UPDATED, status, "different content → UPDATED");
        assertEquals("id-123", incoming.getId());
        verify(mongoTemplate).save(incoming);
    }

    @Test
    void status_idempotent_secondCallIsUnchanged() {
        BannerSpec existing = makeSpec("slug-x", "id-x", 300, 250);
        when(mongoTemplate.findOne(any(Query.class), eq(BannerSpec.class)))
                .thenReturn(null)      // 1st call: new
                .thenReturn(existing); // 2nd call: already exists

        BannerSpec spec1 = makeSpec("slug-x", null, 300, 250);
        BannerSpec spec2 = makeSpec("slug-x", null, 300, 250);

        assertEquals(INSERTED,  service.upsertBySlugStatus(spec1), "1st: insert");
        assertEquals(UNCHANGED, service.upsertBySlugStatus(spec2), "2nd: unchanged");
        verify(mongoTemplate, times(1)).save(any()); // only 1 save total
    }

    // ─── upsertBySlug (backward-compat) ─────────────────────────────────────

    @Test
    void upsertBySlug_newSpec_returnsTrue() {
        when(mongoTemplate.findOne(any(Query.class), eq(BannerSpec.class))).thenReturn(null);

        BannerSpec spec = makeSpec("slug-new", null, 1200, 628);
        assertTrue(service.upsertBySlug(spec), "new spec → true");
        verify(mongoTemplate).save(spec);
    }

    @Test
    void upsertBySlug_existingSpec_unchanged_returnsFalse_noSave() {
        BannerSpec existing = makeSpec("slug-existing", "mongo-id-123", 1200, 628);
        when(mongoTemplate.findOne(any(Query.class), eq(BannerSpec.class))).thenReturn(existing);

        BannerSpec incoming = makeSpec("slug-existing", null, 1200, 628);
        boolean isNew = service.upsertBySlug(incoming);

        assertFalse(isNew, "unchanged spec → false");
        assertEquals("mongo-id-123", incoming.getId(), "id inherited");
        verify(mongoTemplate, never()).save(any()); // UNCHANGED: no save
    }

    @Test
    void upsertBySlug_changedContent_returnsFalse_withSave() {
        BannerSpec existing = makeSpec("slug-existing", "mongo-id-123", 1200, 628);
        when(mongoTemplate.findOne(any(Query.class), eq(BannerSpec.class))).thenReturn(existing);

        BannerSpec incoming = makeSpec("slug-existing", null, 800, 600); // different
        boolean isNew = service.upsertBySlug(incoming);

        assertFalse(isNew, "updated spec → false");
        verify(mongoTemplate).save(incoming);
    }

    // ─── helper ──────────────────────────────────────────────────────────────

    private BannerSpec makeSpec(String slug, String id, int width, int height) {
        BannerSpec s = new BannerSpec();
        s.setSlug(slug);
        s.setMedia("naver");
        s.setPlacementName("Test Spec");
        s.setWidth(width);
        s.setHeight(height);
        s.setActive(true);
        if (id != null) s.setId(id);
        return s;
    }
}
