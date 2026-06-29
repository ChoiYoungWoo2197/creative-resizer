package com.h3.creative.worker;

import lombok.Data;

@Data
public class WorkerResponse {

    private String jobId;
    private String zipPath;
    private int count;
    private String error;

    public boolean isSuccess() {
        return error == null || error.isBlank();
    }
}
