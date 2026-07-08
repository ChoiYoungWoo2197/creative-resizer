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

    private String status;           // pending / processing / done / fail
    private String psdPath;
    private String zipPath;
    private List<BannerResult> results;

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

        // AI 후보 적용 이력
        private String selectedCompareId;
        private String selectedCandidate;       // safe / balanced / fill
        private String selectedCandidateFilePath;
        private Boolean aiCompareApplied;
    }
}
