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
        private String actualPsdRenderMode;  // artboard / full-canvas / imagemagick-flatten / layer-reflow / failed

        // PSD 레이어 재배치 메타 (4차-2 보완)
        private Boolean layerReflowAttempted;
        private Boolean layerReflowSucceeded;
        private String layerReflowError;
        private Integer layerReflowExtractedLayerCount;
        private java.util.List<String> layerReflowDetectedRoles;
        private String layerReflowTemplate;
        private java.util.List<String> usedLayerRoles;
    }
}
