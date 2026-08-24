package com.h3.creative.queue.message;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import com.h3.creative.domain.BannerSpec;
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

    // 4차-9: Object Reflow
    private String objectAnalysisId;
    private Boolean objectReflowEnabled;

    // 신규 파이프라인 라우팅 (예: "clean_v1")
    private String pipelineVersion;

    // 파이프라인 타입 (예: "G") — Worker pipeline_type_selector에 전달
    private String pipelineType;

    // 커스텀 사이즈 (DB에 없는 사용자 지정 규격)
    private List<BannerSpec> customSpecs;
}
