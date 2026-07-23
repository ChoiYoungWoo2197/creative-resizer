package com.h3.creative.mongo;

import com.h3.creative.domain.BannerSpec;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.data.domain.Sort;
import org.springframework.data.mongodb.core.MongoTemplate;
import org.springframework.data.mongodb.core.query.Criteria;
import org.springframework.data.mongodb.core.query.Query;
import org.springframework.data.mongodb.core.query.Update;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;

@Slf4j
@Service
@RequiredArgsConstructor
public class SpecMongoService {

    private static final List<String> LEGACY_NAVER_SLUGS = List.of(
        "smartchannel_horizontal",
        "pc_display",
        "pc_leaderboard",
        "pc_skyscraper",
        "mobile_banner",
        "gfa_feed_square",
        "gfa_feed_landscape",
        "gfa_mobile_da"
    );

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

    public List<BannerSpec> findAllActive() {
        Query query = Query.query(Criteria.where("active").is(true))
                .with(Sort.by(Sort.Direction.ASC, "sortOrder"));
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

    /**
     * 레거시 Naver 기본 규격 8개를 active=false 처리한다.
     * - media=naver AND slug IN (8개 slug) 대상만 변경
     * - 물리 삭제 없음, 다른 매체 무영향
     * - 재실행해도 안전한 idempotent 작업
     * - 8개 미만이어도 found 개수만큼만 처리
     *
     * @throws IllegalStateException 대상이 8개 초과일 경우 (의도치 않은 데이터 변형 방지)
     */
    public Map<String, Object> deactivateLegacyNaverSpecs() {
        Query findQuery = Query.query(
            Criteria.where("media").is("naver")
                    .and("slug").in(LEGACY_NAVER_SLUGS)
        );
        List<BannerSpec> targets = mongoTemplate.find(findQuery, BannerSpec.class);

        List<String> foundSlugs = new ArrayList<>();
        for (BannerSpec s : targets) foundSlugs.add(s.getSlug());
        log.info("[legacy-naver] 대상 발견: {}개 slugs={}", targets.size(), foundSlugs);

        if (targets.size() > LEGACY_NAVER_SLUGS.size()) {
            throw new IllegalStateException(
                "예상 최대 " + LEGACY_NAVER_SLUGS.size() + "개인데 " + targets.size() + "개 발견 — 중단");
        }

        long activeCount = targets.stream().filter(BannerSpec::isActive).count();

        Query updateQuery = Query.query(
            Criteria.where("media").is("naver")
                    .and("slug").in(LEGACY_NAVER_SLUGS)
                    .and("active").is(true)
        );
        com.mongodb.client.result.UpdateResult result =
            mongoTemplate.updateMulti(updateQuery, Update.update("active", false), BannerSpec.class);

        log.info("[legacy-naver] matchedCount={} modifiedCount={}",
                result.getMatchedCount(), result.getModifiedCount());

        Map<String, Object> out = new HashMap<>();
        out.put("foundSlugs",    foundSlugs);
        out.put("foundCount",    targets.size());
        out.put("activeAtStart", activeCount);
        out.put("matchedCount",  result.getMatchedCount());
        out.put("modifiedCount", result.getModifiedCount());
        return out;
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
