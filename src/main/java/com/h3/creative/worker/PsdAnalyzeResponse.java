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

    // PSD 파서 호환성 진단 (4차-2 보완)
    private Boolean layerReadable;
    private Integer layerCount;
    private String layerReadError;
    private String layerReadErrorCode;
    private Boolean layerReflowAvailable;
    private String psdParserEngine;
    private Boolean psdCompatPatched;

    public PsdAnalysis toPsdAnalysis() {
        PsdAnalysis analysis = new PsdAnalysis();
        analysis.setWidth(width);
        analysis.setHeight(height);
        analysis.setHasArtboards(hasArtboards);
        analysis.setArtboards(artboards);
        analysis.setLayers(layers);
        analysis.setLayerReadable(layerReadable);
        analysis.setLayerCount(layerCount);
        analysis.setLayerReadError(layerReadError);
        analysis.setLayerReadErrorCode(layerReadErrorCode);
        analysis.setLayerReflowAvailable(layerReflowAvailable);
        analysis.setPsdParserEngine(layerReadable != null && layerReadable && Boolean.TRUE.equals(psdCompatPatched)
                ? "psd-tools-patched" : psdParserEngine);
        analysis.setPsdCompatPatched(psdCompatPatched);
        return analysis;
    }
}
