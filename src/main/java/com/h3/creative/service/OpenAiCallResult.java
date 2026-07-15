package com.h3.creative.service;

import lombok.Builder;
import lombok.Getter;

import java.util.Map;

@Getter
@Builder
public class OpenAiCallResult {
    private final Map<String, Object> body;
    private final String requestedModel;
    private final String usedModel;
    private final boolean fallbackUsed;
    private final String fallbackReason;
    private final String tokenParameter;
    private final int tokenLimit;
}
