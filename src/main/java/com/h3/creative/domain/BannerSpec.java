package com.h3.creative.domain;

import lombok.Data;
import org.springframework.data.annotation.Id;
import org.springframework.data.mongodb.core.mapping.Document;

import java.util.List;
import java.util.Map;

@Data
@Document(collection = "banner_spec")
public class BannerSpec {

    @Id
    private String id;

    private String media;           // google / meta / naver / kakao / linkedin / tiktok
    private String placementName;   // 한글 지면명
    private String slug;            // 영문 식별자 (파일명용)
    private int width;
    private int height;
    private String aspectRatio;
    private boolean active;
    private int sortOrder;

    // 4단계: safe zone (픽셀 inset, optional — null이면 worker가 비율 기반 기본값 사용)
    private Map<String, Integer> safeZone;
    private Map<String, Integer> textSafeZone;
    private Map<String, Integer> ctaSafeZone;

    // 8단계: BannerSpec DB 확장 필드 (optional, imagefactory 수집 데이터)
    private String category;           // 지면 카테고리 (검색/디스플레이, 피드, 쇼핑 등)
    private String placementType;      // display / feed / search / shopping / native / video
    private String sourceUrl;          // 출처 URL
    private String sourceType;         // competitor / official
    private String sourceRef;          // 출처 레퍼런스 텍스트
    private String ratio;              // GCD 비율 (예: "300:157")
    private String ratioLabel;         // 표시용 비율 (예: "1.91:1", max>20 일 때만)
    private List<String> fileFormats;  // 허용 파일 형식 (jpg, png, gif ...)
    private Integer maxFileSizeKb;
    private Integer minFileSizeKb;
    private String colorSpace;
    // safe zone 개별 픽셀 값 (source 원본 보존용 — safeZone Map은 이 값으로 채워짐)
    private Integer safeTop;
    private Integer safeRight;
    private Integer safeBottom;
    private Integer safeLeft;
    private Integer safeZoneWidth;
    private Integer safeZoneHeight;
    // "parsed_text" → hard constraint, "diagram_unreadable" / "no_safezone" → fallback
    private String safeZoneParseStatus;
    private Integer headlineMaxChars;
    private Integer descriptionMaxChars;
    private Integer textMaxPct;
    private Boolean bgTransparent;
    private Boolean isVideo;
    private String notes;
    private Boolean needsReview;
    private String lastVerified;
    private String lastUpdated;
}
