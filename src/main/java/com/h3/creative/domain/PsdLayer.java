package com.h3.creative.domain;

import lombok.Data;

import java.util.List;

@Data
public class PsdLayer {
    private String id;
    private String name;
    private String type;
    private Boolean visible;
    private List<Integer> bbox;
    private String role;
}
