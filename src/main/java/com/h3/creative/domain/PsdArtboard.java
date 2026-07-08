package com.h3.creative.domain;

import lombok.Data;

@Data
public class PsdArtboard {
    private String id;
    private String name;
    private Integer x;
    private Integer y;
    private Integer width;
    private Integer height;
    private Double ratio;
    private String artboardType;   // square / vertical / horizontal / custom / full-canvas
    private String source;         // artboard_tag / group_name / layer_bbox / fallback
}
