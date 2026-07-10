package com.h3.creative.mongo;

import com.h3.creative.domain.BannerSpec;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.data.mongodb.core.MongoTemplate;
import org.springframework.data.mongodb.core.query.Query;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.*;

/**
 * SpecMongoService.upsertBySlug 로직 단위 테스트.
 * MongoTemplate을 mock해서 insert=true, update=false 반환 조건 검증.
 */
@ExtendWith(MockitoExtension.class)
class SpecMongoServiceUpsertTest {

    @Mock
    private MongoTemplate mongoTemplate;

    @InjectMocks
    private SpecMongoService service;

    @Test
    void upsertBySlug_newSpec_returnsTrue() {
        // existing spec 없음 → findOne returns null → insert
        when(mongoTemplate.findOne(any(Query.class), eq(BannerSpec.class))).thenReturn(null);

        BannerSpec spec = makeSpec("slug-new", null);
        boolean isNew = service.upsertBySlug(spec);

        assertTrue(isNew, "new spec should return true");
        verify(mongoTemplate).save(spec);
        assertNull(spec.getId(), "new spec should have no id set");
    }

    @Test
    void upsertBySlug_existingSpec_returnsFalse() {
        // 기존 spec이 있음 → findOne returns existing → update (id 이어받음)
        BannerSpec existing = makeSpec("slug-existing", "mongo-id-123");
        when(mongoTemplate.findOne(any(Query.class), eq(BannerSpec.class))).thenReturn(existing);

        BannerSpec incoming = makeSpec("slug-existing", null);
        boolean isNew = service.upsertBySlug(incoming);

        assertFalse(isNew, "existing spec should return false");
        assertEquals("mongo-id-123", incoming.getId(), "id should be inherited from existing");

        ArgumentCaptor<BannerSpec> captor = ArgumentCaptor.forClass(BannerSpec.class);
        verify(mongoTemplate).save(captor.capture());
        assertEquals("mongo-id-123", captor.getValue().getId());
    }

    @Test
    void upsertBySlug_idempotent_secondCallUpdates() {
        // 1차: null (insert) → 2차: existing (update)
        BannerSpec existing = makeSpec("slug-x", "id-x");
        when(mongoTemplate.findOne(any(Query.class), eq(BannerSpec.class)))
                .thenReturn(null)   // 1st call
                .thenReturn(existing);  // 2nd call

        BannerSpec spec1 = makeSpec("slug-x", null);
        BannerSpec spec2 = makeSpec("slug-x", null);

        assertTrue(service.upsertBySlug(spec1),  "1st call: insert");
        assertFalse(service.upsertBySlug(spec2), "2nd call: update");
        verify(mongoTemplate, times(2)).save(any(BannerSpec.class));
    }

    // ─── helper ──────────────────────────────────────────────────────────────

    private BannerSpec makeSpec(String slug, String id) {
        BannerSpec s = new BannerSpec();
        s.setSlug(slug);
        s.setMedia("naver");
        s.setPlacementName("Test Spec");
        s.setWidth(1200);
        s.setHeight(628);
        s.setActive(true);
        if (id != null) s.setId(id);
        return s;
    }
}
