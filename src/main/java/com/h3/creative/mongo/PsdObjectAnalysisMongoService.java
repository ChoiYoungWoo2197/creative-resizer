package com.h3.creative.mongo;

import com.h3.creative.domain.PsdObjectAnalysis;
import lombok.RequiredArgsConstructor;
import org.springframework.data.mongodb.core.MongoTemplate;
import org.springframework.stereotype.Service;

@Service
@RequiredArgsConstructor
public class PsdObjectAnalysisMongoService {

    private final MongoTemplate mongoTemplate;

    public PsdObjectAnalysis save(PsdObjectAnalysis doc) {
        return mongoTemplate.save(doc);
    }
}
