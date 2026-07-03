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
    private List<DetectedElementPayload> detectedElements;
    private List<String> requiredGroups;
    private List<String> priorityGroups;

    @Data
    @Builder
    public static class SpecItem {
        private String media;
        private String slug;
        private int width;
        private int height;
    }

    @Data
    @Builder
    public static class DetectedElementPayload {
        private String id;
        private String type;
        private String label;
        private String group;
        private String importance;
        private BboxPayload bbox;
    }

    @Data
    @Builder
    public static class BboxPayload {
        private Integer x;
        private Integer y;
        private Integer width;
        private Integer height;
    }
}
