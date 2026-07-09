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
    private String placementName;   // 한글 지면명
    private String slug;            // 영문 식별자 (파일명용)
    private int width;
    private int height;
    private String aspectRatio;
    private boolean active;
    private int sortOrder;

    // 4단계: safe zone (픽셀 inset, optional — null이면 worker가 비율 기반 기본값 사용)
    private java.util.Map<String, Integer> safeZone;
    private java.util.Map<String, Integer> textSafeZone;
    private java.util.Map<String, Integer> ctaSafeZone;
}
