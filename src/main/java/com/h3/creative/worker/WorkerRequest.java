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
    private String outputFormat;

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
