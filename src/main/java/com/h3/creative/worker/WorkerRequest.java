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
    }
}
