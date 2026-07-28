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
    private String outputFormat;
    private String pipelineVersion;   // "clean_v1" (기본값, 생략 시 Worker가 동일하게 처리)

    @Data
    @Builder
    public static class SpecItem {
        private String media;
        private String name;   // 한글 지면명
        private String slug;   // 영문 파일명용
        private int width;
        private int height;

        // safe zone (픽셀 inset) — null이면 clean_v1이 비율 기반 기본값 사용
        private java.util.Map<String, Integer> safeZone;
        private java.util.Map<String, Integer> textSafeZone;
        private java.util.Map<String, Integer> ctaSafeZone;

        // safe zone 적용 방식 힌트: "parsed_text" → hard constraint, null → fallback
        private String safeZoneParseStatus;

        // 파일 규칙 (optional)
        private java.util.Map<String, Object> fileRules;
    }
}
