package com.h3.creative.domain;

import lombok.Data;
import org.springframework.data.annotation.Id;
import org.springframework.data.annotation.Transient;
import org.springframework.data.mongodb.core.mapping.Document;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;

@Data
@Document(collection = "psd_object_analysis")
public class PsdObjectAnalysis {

    @Id
    private String id;

    private String psdPath;
    private String selectedArtboardId;
    private Map<String, Integer> artboardBox;
    private Integer canvasWidth;
    private Integer canvasHeight;

    private List<ObjectResult> objects;
    private boolean reflowReady;
    private List<String> missingRequiredRoles;

    private LocalDateTime createdAt;

    /** 프리뷰 이미지(base64). MongoDB에 저장되지 않으며 HTTP 응답에만 포함된다. */
    @Transient
    private String previewBase64;

    @Data
    public static class ObjectResult {
        private String id;
        private String role;            // background/title/body_text/main_image/cta/logo/badge/decoration/unknown
        private String label;
        private String importance;      // required/priority/optional
        private Map<String, Integer> bbox;  // artboard-relative {x,y,width,height}
        private Double confidence;
        private String reflowBehavior;  // fill_canvas/keep_aspect/move_only/optional_drop
        private Boolean safeZoneRequired;
        private Boolean canScale;
        private Boolean canCrop;
        private String recommendedLayerName;
        // 레이어 매칭 결과
        private String matchedLayerId;
        private String matchedLayerName;
        private Double matchScore;
        private String matchStatus;     // ready/matched_low_confidence/missing_layer
    }
}
