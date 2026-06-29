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
    private String outputFormat;

    @Data
    @Builder
    public static class SpecItem {
        private String media;
        private String placementName;
        private int width;
        private int height;
    }
}
