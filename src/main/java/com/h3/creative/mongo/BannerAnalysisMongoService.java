package com.h3.creative.mongo;

import com.h3.creative.domain.BannerAiAnalysis;
import lombok.RequiredArgsConstructor;
import org.springframework.data.mongodb.core.MongoTemplate;
import org.springframework.data.mongodb.core.query.Criteria;
import org.springframework.data.mongodb.core.query.Query;
import org.springframework.stereotype.Service;

@Service
@RequiredArgsConstructor
public class BannerAnalysisMongoService {

    private final MongoTemplate mongoTemplate;

    public BannerAiAnalysis save(BannerAiAnalysis analysis) {
        return mongoTemplate.save(analysis);
    }

    public BannerAiAnalysis findById(String id) {
        if (id == null || id.isBlank()) return null;
        return mongoTemplate.findOne(Query.query(Criteria.where("_id").is(id)), BannerAiAnalysis.class);
    }
}
