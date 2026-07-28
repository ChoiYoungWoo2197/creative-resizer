package com.h3.creative.worker;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import lombok.Data;

import java.util.List;

@Data
@JsonIgnoreProperties(ignoreUnknown = true)
public class WorkerResponse {

    private String jobId;
    private String zipPath;
    private int count;
    private String error;
    private List<ResultItem> results;
    private List<String> missingRatioTypes;

    public boolean isSuccess() {
        return error == null || error.isBlank();
    }

    @Data
    @JsonIgnoreProperties(ignoreUnknown = true)
    public static class ResultItem {

        // ── clean_v1 공통 필드 (PASS / FAIL 모두 반환) ────────────────────────
        private String pipelineVersion;   // "clean_v1"
        private String renderSource;      // "clean_pipeline"
        private Boolean fallbackUsed;     // 항상 false
        private Boolean valid;            // PASS=true / FAIL=false
        private String media;
        private String name;
        private String slug;
        private int width;
        private int height;

        // ── clean_v1 PASS 전용 ────────────────────────────────────────────────
        private String fileName;
        private String filePath;
        private Long fileSize;

        // ── clean_v1 FAIL 전용 ───────────────────────────────────────────────
        private String failedStage;    // P1~P8 단계명 (예: SOURCE_PREPARATION)
        private String failureCode;    // 오류 코드 (예: SOURCE_NOT_FOUND)
        private String error;          // 오류 메시지

        // ── 선택적 메타 (미래 확장용, 현재 clean_v1이 반환하지 않을 수 있음) ──
        private String validationMessage;
    }
}
