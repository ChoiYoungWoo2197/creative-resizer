package com.h3.creative.worker;

import lombok.Builder;
import lombok.Data;

import java.util.List;

@Data
@Builder
public class CompareWorkerRequest {

    private String compareId;
    private String psdPath;
    private SpecItem spec;
    private String resizeMode;
    private String focalPosition;
    private List<String> strengths;

    @Data
    @Builder
    public static class SpecItem {
        private String media;
        private String slug;
        private int width;
        private int height;
    }
}
