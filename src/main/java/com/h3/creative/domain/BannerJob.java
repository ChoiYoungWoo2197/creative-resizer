package com.h3.creative.domain;

import lombok.Data;
import org.springframework.data.annotation.Id;
import org.springframework.data.mongodb.core.mapping.Document;

import java.time.LocalDateTime;
import java.util.List;



@Data
@Document(collection = "banner_job")
public class BannerJob {

    @Id
    private String id;

    private String advertiser;
    private String campaignName;
    private List<String> targetMedia;
    private List<String> specIds;
    private String resizeMode;        // cover / contain / blur-bg / smart-fit
    private String smartFitStrength; // safe / balanced / fill (smart-fit 전용)
    private String focalPosition;    // center / top / bottom / left / right / left-top / right-top / left-bottom / right-bottom
    private String outputFormat;     // png / jpg / webp

    private String sourceType;        // image / psd
    private String psdMode;          // artboard-first / flatten / layer-reflow
    private PsdAnalysis psdAnalysis;
    private List<String> selectedArtboardIds;   // 사용자 선택 아트보드 ID
    private List<String> missingRatioTypes;     // 감지되지 않은 비율 타입 (square/vertical/horizontal)

    private String status;           // pending / processing / done / fail
    private String psdPath;
    private String zipPath;
    private List<BannerResult> results;

    // 4차-9: Object Reflow
    private String objectAnalysisId;
    private Boolean objectReflowEnabled;

    // AI 추천 적용 이력
    private String aiAnalysisId;
    private Boolean aiApplied;
    private String aiRecommendedResizeMode;
    private String aiRecommendedSmartFitStrength;
    private String aiRecommendedFocalPosition;

    private String errorMessage;
    private LocalDateTime createdAt;
    private LocalDateTime updatedAt;

    @Data
    public static class BannerResult {
        private String specId;
        private String media;
        private String name;
        private String slug;
        private int width;
        private int height;
        private String fileName;
        private String filePath;
        private Long fileSize;
        private Boolean valid;
        private String validationMessage;

        // PSD 아트보드 선택 정보
        private String selectedArtboardId;
        private String selectedArtboardName;
        private String selectedArtboardType;         // square / vertical / horizontal / custom / full-canvas
        private java.util.Map<String, Object> selectedArtboardBox;  // {x, y, width, height}
        private Double artboardMatchScore;           // 0.0 ~ 1.0 (1.0 = 비율 완전 일치)
        private String selectedSourceArtboardSize;   // e.g. "1200x1200"
        private String sourceMatchType;              // exact / inferred / fallback
        private String actualPsdRenderMode;  // artboard / full-canvas / imagemagick-flatten / layer-reflow / failed

        // PSD fallback pipeline 메타 (4차-3)
        private String renderSource;   // psd_tools_composite / imagemagick_* / psd_layer_reflow / pillow_image / unknown
        private Boolean fallbackUsed;
        private String fallbackReason;
        private List<java.util.Map<String, Object>> fallbackErrors;
        private Integer sourceWidth;
        private Integer sourceHeight;

        // 4차-4: Wide-Banner Smart-Fit 메타
        private String resizeStrategy;    // wide-banner-smart-fit / smart-fit / psd-layer-reflow 등
        private String candidateType;     // safe / balanced / fill / focus-crop
        private Double candidateScore;
        private Double blurAreaRatio;
        private Double cropRatio;
        private Double subjectScale;

        // 4차-5: Layer Reflow 품질 메타
        private Boolean safeZonePass;
        private Boolean requiredLayerMissing;

        // wide-banner 품질 게이트 결과
        private Boolean qualityGate;   // true = 모든 후보가 50점 미만이었음
        private String  qualityLabel;  // 정상 / 주의 / 품질 낮음

        // PSD 레이어 재배치 메타 (4차-2 보완)
        private Boolean layerReflowAttempted;
        private Boolean layerReflowSucceeded;
        private String layerReflowError;
        private Integer layerReflowExtractedLayerCount;
        private List<String> layerReflowDetectedRoles;
        private String layerReflowTemplate;  // horizontal-1250x560-product / horizontal-1250x560-poster
        private List<String> usedLayerRoles; // headline, product, cta, logo ...

        // 4차-9: Object Reflow 결과
        private Boolean objectReflowAttempted;
        private Boolean objectReflowSucceeded;
        private String objectReflowMode;
        private String objectReflowFallbackReason;
        private List<String> usedObjectRoles;
        private List<String> missingObjectRoles;
        private List<String> cropFallbackRoles;
        private List<String> lowConfidenceRoles;
        private Boolean objectSafeZonePass;

        // 1단계: 고품질 경로 메타
        private String renderMode;
        private Boolean objectReflowUsed;
        private Boolean objectReflowFallbackUsed;
        private Double layoutScore;
        private String backgroundMode;
        private Integer candidateCount;
        private String selectedCandidateId;

        // 4단계: safe zone 체크 결과
        private Boolean safeZonePassed;   // canonical (safeZonePass는 alias)
        private List<java.util.Map<String, Object>> safeZoneViolations;

        // AI 후보 적용 이력
        private String selectedCompareId;
        private String selectedCandidate;       // safe / balanced / fill
        private String selectedCandidateFilePath;
        private Boolean aiCompareApplied;
    }
}
