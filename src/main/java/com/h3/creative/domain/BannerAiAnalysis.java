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

    private LocalDateTime createdAt;
}
