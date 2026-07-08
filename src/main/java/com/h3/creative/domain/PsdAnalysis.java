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

    // PSD 파서 호환성 진단 (4차-2 보완)
    private Boolean layerReadable;
    private Integer layerCount;
    private String layerReadError;
    private String layerReadErrorCode;   // PSD_VERSION_8_UNSUPPORTED | PSD_OPEN_FAILED
    private Boolean layerReflowAvailable;
    private List<String> reflowDetectedRoles;  // 감지된 role 목록
    private List<String> reflowMissingRoles;   // 누락된 required role 목록
    private String psdParserEngine;      // psd-tools | psd-tools-patched
    private Boolean psdCompatPatched;
}
