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
    }
}
