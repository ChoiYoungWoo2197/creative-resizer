package com.h3.creative.domain;

import lombok.Data;
import org.springframework.data.annotation.Id;
import org.springframework.data.mongodb.core.mapping.Document;

import java.time.LocalDateTime;
import java.util.List;

@Data
@Document(collection = "banner_job")
public class BannerJob {

    @Id
    private String id;

    private String advertiser;
    private String campaignName;
    private List<String> targetMedia;
    private List<String> specIds;
    private String resizeMode;        // cover / contain / blur-bg / smart-fit
    private String smartFitStrength; // safe / balanced / fill (smart-fit 전용)
    private String outputFormat;     // png / jpg / webp

    private String status;           // pending / processing / done / fail
    private String psdPath;
    private String zipPath;
    private List<BannerResult> results;

    private String errorMessage;
    private LocalDateTime createdAt;
    private LocalDateTime updatedAt;

    @Data
    public static class BannerResult {
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
    }
}
