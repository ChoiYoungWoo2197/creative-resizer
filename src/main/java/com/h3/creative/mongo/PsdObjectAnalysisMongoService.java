package com.h3.creative.mongo;

import com.h3.creative.domain.PsdObjectAnalysis;
import lombok.RequiredArgsConstructor;
import org.springframework.data.mongodb.core.MongoTemplate;
import org.springframework.data.mongodb.core.query.Criteria;
import org.springframework.data.mongodb.core.query.Query;
import org.springframework.stereotype.Service;

@Service
@RequiredArgsConstructor
public class PsdObjectAnalysisMongoService {

    private final MongoTemplate mongoTemplate;

    public PsdObjectAnalysis save(PsdObjectAnalysis doc) {
        return mongoTemplate.save(doc);
    }

    public PsdObjectAnalysis findById(String id) {
        return mongoTemplate.findOne(Query.query(Criteria.where("_id").is(id)), PsdObjectAnalysis.class);
    }
}
