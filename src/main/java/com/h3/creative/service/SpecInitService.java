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
            // ── Google GDN ──────────────────────────────────
            spec("google", "GDN 스카이스크래퍼",           "gdn_skyscraper_sm",        120,  600,  "1:5",    1),
            spec("google", "GDN 와이드 스카이스크래퍼",    "gdn_wide_skyscraper",      160,  600,  "4:15",   2),
            spec("google", "GDN 소형 정사각형",            "gdn_small_square",         200,  200,  "1:1",    3),
            spec("google", "GDN 세로형",                   "gdn_vertical_rectangle",   240,  400,  "3:5",    4),
            spec("google", "GDN 정사각형",                 "gdn_square",               250,  250,  "1:1",    5),
            spec("google", "GDN 트리플 와이드스크린",      "gdn_triple_widescreen",    250,  360,  "25:36",  6),
            spec("google", "GDN 모바일 배너 소형",         "gdn_mobile_banner_sm",     300,  50,   "6:1",    7),
            spec("google", "GDN 중간 직사각형",            "gdn_medium_rectangle",     300,  250,  "6:5",    8),
            spec("google", "GDN 하프 페이지",              "gdn_half_page",            300,  600,  "1:2",    9),
            spec("google", "GDN 세로형 대형",              "gdn_portrait_large",       300,  1050, "2:7",    10),
            spec("google", "GDN 모바일 리더보드",          "gdn_mobile_leaderboard",   320,  50,   "32:5",   11),
            spec("google", "GDN 모바일 대형 배너",         "gdn_mobile_large_banner",  320,  100,  "16:5",   12),
            spec("google", "GDN 대형 직사각형",            "gdn_large_rectangle",      336,  280,  "6:5",    13),
            spec("google", "GDN 배너",                     "gdn_banner",               468,  60,   "39:5",   14),
            spec("google", "GDN 넷보드",                   "gdn_netboard",             580,  400,  "29:20",  15),
            spec("google", "GDN 리더보드",                 "gdn_leaderboard",          728,  90,   "728:90", 16),
            spec("google", "GDN 상단 배너",                "gdn_top_banner",           930,  180,  "31:6",   17),
            spec("google", "GDN 세로형 와이드",            "gdn_portrait_wide",        960,  1200, "4:5",    18),
            spec("google", "GDN 빌보드",                   "gdn_billboard",            970,  250,  "97:25",  19),
            spec("google", "GDN 파노라마",                 "gdn_panorama",             980,  120,  "49:6",   20),
            spec("google", "GDN 세로형 XL",                "gdn_portrait_xl",          900,  1600, "9:16",   21),
            spec("google", "GDN 반응형 가로형",            "gdn_responsive_landscape", 1200, 628,  "1.91:1", 22),
            spec("google", "GDN 반응형 정사각형",          "gdn_responsive_square",    1200, 1200, "1:1",    23),

            // ── Naver ───────────────────────────────────────
            spec("naver", "스마트채널 가로형",             "smartchannel_horizontal",  1200, 628,  "1.91:1", 101),
            spec("naver", "PC 디스플레이",                 "pc_display",               300,  250,  "6:5",    102),
            spec("naver", "PC 리더보드",                   "pc_leaderboard",           728,  90,   "728:90", 103),
            spec("naver", "PC 스카이스크래퍼",             "pc_skyscraper",            160,  600,  "4:15",   104),
            spec("naver", "모바일 배너",                   "mobile_banner",            320,  50,   "32:5",   105),
            spec("naver", "GFA 피드 1대1",                 "gfa_feed_square",          1200, 1200, "1:1",    106),
            spec("naver", "GFA 피드 16대9",                "gfa_feed_landscape",       1200, 628,  "1.91:1", 107),
            spec("naver", "GFA 모바일DA",                  "gfa_mobile_da",            1250, 560,  "25:11",  108),

            // ── Meta ────────────────────────────────────────
            spec("meta", "피드 1:1",                       "feed_square",              1080, 1080, "1:1",    201),
            spec("meta", "피드 4:5",                       "feed_portrait",            1080, 1350, "4:5",    202),
            spec("meta", "스토리/릴스 9:16",               "story_reels",              1080, 1920, "9:16",   203),
            spec("meta", "카탈로그 정사각형",              "catalog_square",           1200, 1200, "1:1",    204),
            spec("meta", "인스타그램 릴스",                "instagram_reels",          1440, 2560, "9:16",   205),

            // ── Criteo ───────────────────────────────────────
            spec("criteo", "스카이스크래퍼",               "skyscraper_sm",            120,  600,  "1:5",    301),
            spec("criteo", "와이드 스카이스크래퍼",        "wide_skyscraper",          160,  600,  "4:15",   302),
            spec("criteo", "소형 정사각형",                "small_square",             200,  200,  "1:1",    303),
            spec("criteo", "소형 정사각형 2",              "small_square_md",          240,  240,  "1:1",    304),
            spec("criteo", "정사각형",                     "square",                   250,  250,  "1:1",    305),
            spec("criteo", "직사각형 소형",                "rectangle_sm",             280,  230,  "28:23",  306),
            spec("criteo", "중간 직사각형",                "medium_rectangle",         300,  250,  "6:5",    307),
            spec("criteo", "하프 페이지",                  "half_page",                300,  600,  "1:2",    308),
            spec("criteo", "모바일 대형 배너",             "mobile_large_banner",      320,  100,  "16:5",   309),
            spec("criteo", "모바일 리더보드",              "mobile_leaderboard",       320,  50,   "32:5",   310),
            spec("criteo", "대형 직사각형",                "large_rectangle",          336,  280,  "6:5",    311),
            spec("criteo", "배너",                         "banner",                   468,  60,   "39:5",   312),
            spec("criteo", "리더보드",                     "leaderboard",              728,  90,   "728:90", 313),
            spec("criteo", "빌보드",                       "billboard",                970,  250,  "97:25",  314),
            spec("criteo", "대형 가로형",                  "large_landscape",          1200, 600,  "2:1",    315),

            // ── Mobion ───────────────────────────────────────
            spec("mobion", "일반형 스카이스크래퍼",        "general_skyscraper_sm",    120,  600,  "1:5",    401),
            spec("mobion", "일반형 와이드 스카이스크래퍼", "general_wide_skyscraper",  160,  600,  "4:15",   402),
            spec("mobion", "일반형 정사각형",              "general_square",           250,  250,  "1:1",    403),
            spec("mobion", "일반형 배너 소형",             "general_banner_sm",        300,  150,  "2:1",    404),
            spec("mobion", "일반형 직사각형",              "general_rectangle",        336,  280,  "6:5",    405),
            spec("mobion", "일반형 모바일",                "general_mobile",           720,  1230, "12:21",  406),
            spec("mobion", "일반형 모바일 대형",           "general_mobile_lg",        800,  1500, "8:15",   407),
            spec("mobion", "일반형 빌보드",                "general_billboard",        970,  250,  "97:25",  408),
            spec("mobion", "단색배경 직사각형",            "solid_rectangle_sm",       300,  180,  "5:3",    409),
            spec("mobion", "단색배경 배너",                "solid_banner",             320,  100,  "16:5",   410),
            spec("mobion", "단색배경 가로형",              "solid_landscape",          720,  120,  "6:1",    411),
            spec("mobion", "단색배경 대형 가로",           "solid_landscape_lg",       1456, 180,  "364:45", 412),

            // ── Kakao ───────────────────────────────────────
            spec("kakao", "비즈보드 가로형",               "bizboard_horizontal",      1200, 628,  "1.91:1", 501),
            spec("kakao", "모먼트 정사각형",               "moment_square",            1080, 1080, "1:1",    502),
            spec("kakao", "모먼트 세로형",                 "moment_portrait",          1080, 1350, "4:5",    503),
            spec("kakao", "모먼트 스토리",                 "moment_story",             1080, 1920, "9:16",   504),
            spec("kakao", "모바일 배너",                   "mobile_banner",            320,  50,   "32:5",   505),

            // ── LinkedIn ─────────────────────────────────────
            spec("linkedin", "가로형",                     "landscape",                1200, 628,  "1.91:1", 601),
            spec("linkedin", "정사각형",                   "square",                   1080, 1080, "1:1",    602),
            spec("linkedin", "세로형",                     "portrait",                 1080, 1350, "4:5",    603),

            // ── TikTok ───────────────────────────────────────
            spec("tiktok", "인피드 광고",                  "infeed",                   1080, 1920, "9:16",   701)
        );
    }

    private BannerSpec spec(String media, String placementName, String slug,
                            int width, int height, String aspectRatio, int sortOrder) {
        BannerSpec s = new BannerSpec();
        s.setMedia(media);
        s.setPlacementName(placementName);
        s.setSlug(slug);
        s.setWidth(width);
        s.setHeight(height);
        s.setAspectRatio(aspectRatio);
        s.setActive(true);
        s.setSortOrder(sortOrder);
        return s;
    }
}
