package com.h3.creative.worker;

import lombok.Data;

import java.util.List;

@Data
public class WorkerResponse {

    private String jobId;
    private String zipPath;
    private int count;
    private String error;
    private List<ResultItem> results;
    private List<String> missingRatioTypes;   // 감지되지 않은 비율 타입 목록

    public boolean isSuccess() {
        return error == null || error.isBlank();
    }

    @Data
    public static class ResultItem {
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
        private java.util.List<java.util.Map<String, Object>> fallbackErrors;
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
        private java.util.List<String> layerReflowDetectedRoles;
        private String layerReflowTemplate;
        private java.util.List<String> usedLayerRoles;

        // 4차-9: Object Reflow 결과
        private Boolean objectReflowAttempted;
        private Boolean objectReflowSucceeded;
        private String objectReflowMode;
        private String objectReflowFallbackReason;
        private java.util.List<String> usedObjectRoles;
        private java.util.List<String> missingObjectRoles;
        private java.util.List<String> cropFallbackRoles;
        private java.util.List<String> lowConfidenceRoles;
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
        private String layoutScoreStatus; // "normal" | "fallback" (emergency_fallback 선택 시)
        private java.util.List<java.util.Map<String, Object>> safeZoneViolations;
    }
}
