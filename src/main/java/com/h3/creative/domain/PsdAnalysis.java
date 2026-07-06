package com.h3.creative.domain;

import lombok.Data;

import java.util.List;

@Data
public class PsdAnalysis {
    private Integer width;
    private Integer height;
    private Boolean hasArtboards;
    private List<PsdArtboard> artboards;
    private List<PsdLayer> layers;
}
