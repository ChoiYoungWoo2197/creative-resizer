package com.h3.creative.mongo;

import com.h3.creative.domain.BannerAiCompare;
import lombok.RequiredArgsConstructor;
import org.springframework.data.mongodb.core.MongoTemplate;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;

@Service
@RequiredArgsConstructor
public class BannerCompareMongoService {

    private final MongoTemplate mongoTemplate;

    public BannerAiCompare save(BannerAiCompare compare) {
        compare.setCreatedAt(LocalDateTime.now());
        return mongoTemplate.save(compare);
    }

    public BannerAiCompare findById(String id) {
        return mongoTemplate.findById(id, BannerAiCompare.class);
    }
}
