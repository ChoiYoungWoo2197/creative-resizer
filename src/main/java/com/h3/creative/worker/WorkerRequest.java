package com.h3.creative.worker;

import lombok.Builder;
import lombok.Data;

import java.util.List;

@Data
@Builder
public class WorkerRequest {

    private String jobId;
    private String psdPath;
    private List<SpecItem> specs;
    private String resizeMode;
    private String smartFitStrength;
    private String focalPosition;
    private String outputFormat;
    private String sourceType;   // image / psd
    private String psdMode;      // artboard-first / flatten
    private List<String> selectedArtboardIds;  // 사용자가 선택한 아트보드 ID 목록 (null = 전체)

    // 4차-9: Object Reflow
    private Boolean objectReflowEnabled;
    private java.util.Map<String, Object> objectAnalysis;  // PsdObjectAnalysis 스냅샷

    @Data
    @Builder
    public static class SpecItem {
        private String media;
        private String name;   // 한글 지면명
        private String slug;   // 영문 파일명용
        private int width;
        private int height;

        // 4단계: safe zone (픽셀 inset, optional — null이면 worker가 비율 기반 기본값 사용)
        private java.util.Map<String, Integer> safeZone;
        private java.util.Map<String, Integer> textSafeZone;
        private java.util.Map<String, Integer> ctaSafeZone;

        // 8단계: safe zone 적용 방식 힌트
        // "parsed_text" → hard constraint, "diagram_unreadable" / null → fallback
        private String safeZoneParseStatus;

        // 8단계: 파일 규칙 (optional)
        private java.util.Map<String, Object> fileRules;
    }
}
