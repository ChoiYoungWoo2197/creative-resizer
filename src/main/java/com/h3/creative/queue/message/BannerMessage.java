package com.h3.creative.queue.message;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.util.List;

@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class BannerMessage {

    private String jobId;
    private String psdPath;
    private List<String> targetMedia;
    private List<String> specIds;
    private String resizeMode;
    private String smartFitStrength;
    private String focalPosition;
    private String outputFormat;
    private String sourceType;
    private String psdMode;
    private List<String> selectedArtboardIds;
}
