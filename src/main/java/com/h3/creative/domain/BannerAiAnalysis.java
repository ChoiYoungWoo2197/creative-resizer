package com.h3.creative.domain;

import lombok.Data;
import org.springframework.data.annotation.Id;
import org.springframework.data.mongodb.core.mapping.Document;

import java.time.LocalDateTime;
import java.util.List;

@Data
@Document(collection = "banner_ai_analysis")
public class BannerAiAnalysis {

    @Id
    private String id;

    private String sourceFileName;
    private String resizeMode;
    private String smartFitStrength;
    private String focalPosition;
    private String reason;
    private List<String> warnings;
    private Double confidence;
    private LocalDateTime createdAt;
}
