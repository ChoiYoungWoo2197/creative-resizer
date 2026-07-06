package com.h3.creative.worker;

import com.h3.creative.domain.PsdAnalysis;
import com.h3.creative.domain.PsdArtboard;
import com.h3.creative.domain.PsdLayer;
import lombok.Data;

import java.util.List;

@Data
public class PsdAnalyzeResponse {
    private Integer width;
    private Integer height;
    private Boolean hasArtboards;
    private List<PsdArtboard> artboards;
    private List<PsdLayer> layers;
    private String error;

    public PsdAnalysis toPsdAnalysis() {
        PsdAnalysis analysis = new PsdAnalysis();
        analysis.setWidth(width);
        analysis.setHeight(height);
        analysis.setHasArtboards(hasArtboards);
        analysis.setArtboards(artboards);
        analysis.setLayers(layers);
        return analysis;
    }
}
