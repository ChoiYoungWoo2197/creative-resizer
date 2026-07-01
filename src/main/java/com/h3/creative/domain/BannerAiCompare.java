package com.h3.creative.domain;

import lombok.Data;
import org.springframework.data.annotation.Id;
import org.springframework.data.mongodb.core.mapping.Document;

import java.time.LocalDateTime;
import java.util.List;

@Data
@Document(collection = "banner_ai_compare")
public class BannerAiCompare {

    @Id
    private String id;

    private String jobId;
    private String specId;
    private String media;
    private int width;
    private int height;
    private String resizeMode;
    private String focalPosition;

    private String bestCandidate;
    private int bestScore;
    private String summary;

    private List<CandidateResult> candidates;
    private LocalDateTime createdAt;

    @Data
    public static class CandidateResult {
        private String strength;
        private int score;
        private String fileName;
        private String filePath;
        private String previewUrl;
        private List<String> pros;
        private List<String> cons;
    }
}
