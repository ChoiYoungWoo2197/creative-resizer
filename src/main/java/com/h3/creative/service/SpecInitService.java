package com.h3.creative.service;

import com.h3.creative.domain.BannerSpec;
import com.h3.creative.mongo.SpecMongoService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.data.mongodb.core.MongoTemplate;
import org.springframework.data.mongodb.core.query.Query;
import org.springframework.stereotype.Service;

import java.util.List;

@Slf4j
@Service
@RequiredArgsConstructor
public class SpecInitService {

    private final SpecMongoService specMongoService;
    private final MongoTemplate mongoTemplate;

    public int init(boolean reset) {
        if (reset) {
            mongoTemplate.remove(new Query(), BannerSpec.class);
            log.info("기존 규격 전체 삭제");
        }

        List<BannerSpec> specs = buildDefaultSpecs();
        specs.forEach(specMongoService::save);
        log.info("규격 {}개 삽입 완료", specs.size());
        return specs.size();
    }

    private List<BannerSpec> buildDefaultSpecs() {
        return List.of(
            // ── Google ──────────────────────────────────────
            spec("google", "반응형 디스플레이 가로형",   "responsive_landscape",  1200, 628,  "1.91:1"),
            spec("google", "반응형 디스플레이 정사각형", "responsive_square",      1200, 1200, "1:1"),
            spec("google", "반응형 디스플레이 세로형",   "responsive_portrait",   900,  1600, "9:16"),
            spec("google", "중간 직사각형",               "medium_rectangle",      300,  250,  "6:5"),
            spec("google", "리더보드",                    "leaderboard",           728,  90,   "728:90"),
            spec("google", "와이드 스카이스크래퍼",       "wide_skyscraper",       160,  600,  "4:15"),
            spec("google", "모바일 배너",                 "mobile_banner",         320,  100,  "32:10"),
            spec("google", "모바일 리더보드",             "mobile_leaderboard",    320,  50,   "32:5"),

            // ── Meta ────────────────────────────────────────
            spec("meta", "피드 정사각형 1:1",     "feed_square",    1080, 1080, "1:1"),
            spec("meta", "피드 세로형 4:5",       "feed_portrait",  1080, 1350, "4:5"),
            spec("meta", "스토리/릴스 9:16",      "story_reels",    1080, 1920, "9:16"),

            // ── Naver ───────────────────────────────────────
            spec("naver", "스마트채널 가로형",    "smartchannel_horizontal", 1200, 628,  "1.91:1"),
            spec("naver", "PC 디스플레이",        "pc_display",              300,  250,  "6:5"),
            spec("naver", "PC 리더보드",          "pc_leaderboard",          728,  90,   "728:90"),
            spec("naver", "PC 스카이스크래퍼",    "pc_skyscraper",           160,  600,  "4:15"),
            spec("naver", "모바일 배너",           "mobile_banner",           320,  50,   "32:5"),
            spec("naver", "GFA 정사각형",          "gfa_square",              1080, 1080, "1:1"),
            spec("naver", "GFA 피드 세로형",       "gfa_feed_portrait",       1080, 1350, "4:5"),
            spec("naver", "GFA 스토리",            "gfa_story",               1080, 1920, "9:16"),

            // ── Kakao ───────────────────────────────────────
            spec("kakao", "비즈보드 가로형",       "bizboard_horizontal", 1200, 628,  "1.91:1"),
            spec("kakao", "모먼트 정사각형",        "moment_square",       1080, 1080, "1:1"),
            spec("kakao", "모먼트 세로형",          "moment_portrait",     1080, 1350, "4:5"),
            spec("kakao", "모먼트 스토리",          "moment_story",        1080, 1920, "9:16"),
            spec("kakao", "모바일 배너",            "mobile_banner",       320,  50,   "32:5"),

            // ── LinkedIn ─────────────────────────────────────
            spec("linkedin", "가로형",             "landscape", 1200, 628,  "1.91:1"),
            spec("linkedin", "정사각형",           "square",    1080, 1080, "1:1"),
            spec("linkedin", "세로형",             "portrait",  1080, 1350, "4:5"),

            // ── TikTok ───────────────────────────────────────
            spec("tiktok", "인피드 광고",          "infeed", 1080, 1920, "9:16")
        );
    }

    private BannerSpec spec(String media, String placementName, String slug,
                            int width, int height, String aspectRatio) {
        BannerSpec s = new BannerSpec();
        s.setMedia(media);
        s.setPlacementName(placementName);
        s.setSlug(slug);
        s.setWidth(width);
        s.setHeight(height);
        s.setAspectRatio(aspectRatio);
        s.setActive(true);
        return s;
    }
}
