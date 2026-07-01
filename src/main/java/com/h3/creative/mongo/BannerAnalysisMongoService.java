package com.h3.creative.mongo;

import com.h3.creative.domain.BannerAiAnalysis;
import lombok.RequiredArgsConstructor;
import org.springframework.data.mongodb.core.MongoTemplate;
import org.springframework.stereotype.Service;

@Service
@RequiredArgsConstructor
public class BannerAnalysisMongoService {

    private final MongoTemplate mongoTemplate;

    public BannerAiAnalysis save(BannerAiAnalysis analysis) {
        return mongoTemplate.save(analysis);
    }
}
