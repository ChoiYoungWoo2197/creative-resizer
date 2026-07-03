package com.h3.creative.domain;

import lombok.Data;
import org.springframework.data.annotation.Id;
import org.springframework.data.mongodb.core.mapping.Document;

import java.time.LocalDateTime;
import java.util.List;

@Data
@Document(collection = "banner_ai_analysis")
public class BannerAiAnalysis {

    @Id
    private String id;

    private String sourceFileName;

    // 이미지 분석
    private String creativeType;           // text_heavy / product_focused / balanced_mix
    private String textDensity;            // high / medium / low
    private String edgeRisk;              // high / medium / low
    private String mainSubjectPosition;   // center / top / bottom / left / right + 대각선 4종
    private String mainSubjectDescription;

    // 추천 설정
    private String resizeMode;
    private String smartFitStrength;
    private String focalPosition;
    private String reason;
    private List<String> warnings;
    private Double confidence;

    // 품질 체크
    private List<String> cropRiskAreas;      // 잘림 위험 영역
    private List<String> recommendedBecause; // 추천 근거 bullet
    private List<String> avoidOptions;       // 피해야 할 설정

    // AI 요소 분석 (3.5차)
    private List<DetectedElement> detectedElements;
    private List<ElementGroup> elementGroups;
    private List<String> requiredGroups;
    private List<String> priorityGroups;
    private List<String> optionalGroups;

    // Poster Reflow (4차)
    private String layoutType;          // single_subject / product_visual / poster_info / horizontal_bands / vertical_bands / mixed_layout
    private Boolean reflowRecommended;  // true when poster-type layout detected
    private List<ContentBand> contentBands;

    private LocalDateTime createdAt;

    @Data
    public static class DetectedElement {
        private String id;
        private String type;        // product / person / text / logo / cta / price / discount / badge / decoration / background
        private String label;
        private String group;
        private String importance;  // required / priority / optional
        private Bbox bbox;
    }

    @Data
    public static class Bbox {
        private Integer x;
        private Integer y;
        private Integer width;
        private Integer height;
    }

    @Data
    public static class ElementGroup {
        private String id;
        private String name;
        private String importance;  // required / priority / optional
        private List<String> elementIds;
    }

    @Data
    public static class ContentBand {
        private String id;          // top_main / middle_date / bottom_desc 등
        private String name;        // 한국어 설명
        private String role;        // main_title / date_info / description / product_visual / sub_copy / cta / logo
        private Integer y1;         // 원본 이미지 기준 시작 y좌표
        private Integer y2;         // 원본 이미지 기준 끝 y좌표
        private String importance;  // required / priority / optional
    }
}
