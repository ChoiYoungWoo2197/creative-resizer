package com.h3.creative.mongo;

import com.h3.creative.domain.BannerSpec;
import lombok.RequiredArgsConstructor;
import org.springframework.data.domain.Sort;
import org.springframework.data.mongodb.core.MongoTemplate;
import org.springframework.data.mongodb.core.query.Criteria;
import org.springframework.data.mongodb.core.query.Query;
import org.springframework.stereotype.Service;

import java.util.List;
import java.util.Objects;

@Service
@RequiredArgsConstructor
public class SpecMongoService {

    /** slug 기준 upsert 결과 */
    public enum UpsertStatus {
        INSERTED,   // 신규 문서 삽입
        UPDATED,    // 기존 문서 내용 변경 후 저장
        UNCHANGED   // 기존 문서와 내용 동일 — DB 쓰기 생략
    }

    private final MongoTemplate mongoTemplate;

    public List<BannerSpec> findAll() {
        Query query = new Query().with(Sort.by(Sort.Direction.ASC, "sortOrder"));
        return mongoTemplate.find(query, BannerSpec.class);
    }

    public List<BannerSpec> findByMedia(String media) {
        Query query = Query.query(Criteria.where("media").is(media).and("active").is(true));
        return mongoTemplate.find(query, BannerSpec.class);
    }

    public List<BannerSpec> findByMediaIn(List<String> mediaList) {
        Query query = Query.query(Criteria.where("media").in(mediaList).and("active").is(true));
        return mongoTemplate.find(query, BannerSpec.class);
    }

    public List<BannerSpec> findByIds(List<String> ids) {
        Query query = Query.query(Criteria.where("id").in(ids));
        return mongoTemplate.find(query, BannerSpec.class);
    }

    public BannerSpec save(BannerSpec spec) {
        return mongoTemplate.save(spec);
    }

    public void deleteById(String id) {
        Query query = Query.query(Criteria.where("id").is(id));
        mongoTemplate.remove(query, BannerSpec.class);
    }

    // 8단계: 신규 조회 메서드
    public BannerSpec findByMediaAndSlug(String media, String slug) {
        Query query = Query.query(Criteria.where("media").is(media).and("slug").is(slug));
        return mongoTemplate.findOne(query, BannerSpec.class);
    }

    public List<BannerSpec> findBySlug(String slug) {
        Query query = Query.query(Criteria.where("slug").is(slug));
        return mongoTemplate.find(query, BannerSpec.class);
    }

    public List<BannerSpec> findByPlacementType(String placementType) {
        Query query = Query.query(Criteria.where("placementType").is(placementType));
        return mongoTemplate.find(query, BannerSpec.class);
    }

    /**
     * slug 기준 upsert — 세 가지 상태를 구분해서 반환.
     * INSERTED : 새 문서
     * UPDATED  : 기존 문서, 내용이 달라서 덮어씀
     * UNCHANGED: 기존 문서, 내용 동일 — DB 쓰기 생략 (멱등성 보장)
     */
    public UpsertStatus upsertBySlugStatus(BannerSpec spec) {
        Query query = Query.query(Criteria.where("slug").is(spec.getSlug()));
        BannerSpec existing = mongoTemplate.findOne(query, BannerSpec.class);
        if (existing == null) {
            mongoTemplate.save(spec);
            return UpsertStatus.INSERTED;
        }
        spec.setId(existing.getId());
        if (sameContent(existing, spec)) {
            return UpsertStatus.UNCHANGED;
        }
        mongoTemplate.save(spec);
        return UpsertStatus.UPDATED;
    }

    /** backward-compat: true=INSERTED, false=UPDATED|UNCHANGED */
    public boolean upsertBySlug(BannerSpec spec) {
        return upsertBySlugStatus(spec) == UpsertStatus.INSERTED;
    }

    private boolean sameContent(BannerSpec a, BannerSpec b) {
        return Objects.equals(a.getMedia(),               b.getMedia())
            && a.getWidth()  == b.getWidth()
            && a.getHeight() == b.getHeight()
            && Objects.equals(a.getPlacementName(),       b.getPlacementName())
            && Objects.equals(a.getPlacementType(),       b.getPlacementType())
            && Objects.equals(a.getCategory(),            b.getCategory())
            && Objects.equals(a.getSafeZoneParseStatus(), b.getSafeZoneParseStatus())
            && Objects.equals(a.getSafeTop(),             b.getSafeTop())
            && Objects.equals(a.getSafeRight(),           b.getSafeRight())
            && Objects.equals(a.getSafeBottom(),          b.getSafeBottom())
            && Objects.equals(a.getSafeLeft(),            b.getSafeLeft())
            && Objects.equals(a.getMaxFileSizeKb(),       b.getMaxFileSizeKb())
            && Objects.equals(a.getMinFileSizeKb(),       b.getMinFileSizeKb())
            && Objects.equals(a.getNeedsReview(),         b.getNeedsReview())
            && Objects.equals(a.getIsVideo(),             b.getIsVideo())
            && Objects.equals(a.getNotes(),               b.getNotes())
            && Objects.equals(a.getLastVerified(),        b.getLastVerified())
            && Objects.equals(a.getLastUpdated(),         b.getLastUpdated());
    }
}
