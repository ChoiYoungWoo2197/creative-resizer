package com.h3.creative.domain;

import lombok.Data;
import org.springframework.data.annotation.Id;
import org.springframework.data.mongodb.core.mapping.Document;

@Data
@Document(collection = "banner_spec")
public class BannerSpec {

    @Id
    private String id;

    private String media;           // google / meta / naver / kakao / linkedin / tiktok
    private String placementName;   // 디스플레이, 피드, 스토리 등
    private int width;
    private int height;
    private String aspectRatio;
    private boolean active;
}
