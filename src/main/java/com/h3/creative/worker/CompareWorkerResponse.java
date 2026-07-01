package com.h3.creative.worker;

import lombok.Data;

import java.util.List;

@Data
public class CompareWorkerResponse {

    private String compareId;
    private String originalFilePath;
    private String error;
    private List<CandidateItem> candidates;

    public boolean isSuccess() {
        return error == null || error.isBlank();
    }

    @Data
    public static class CandidateItem {
        private String strength;
        private String fileName;
        private String filePath;
        private int width;
        private int height;
    }
}
