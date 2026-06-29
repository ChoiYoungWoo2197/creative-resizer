package com.h3.creative.mongo;

import com.h3.creative.domain.BannerSpec;
import lombok.RequiredArgsConstructor;
import org.springframework.data.mongodb.core.MongoTemplate;
import org.springframework.data.mongodb.core.query.Criteria;
import org.springframework.data.mongodb.core.query.Query;
import org.springframework.stereotype.Service;

import java.util.List;

@Service
@RequiredArgsConstructor
public class SpecMongoService {

    private final MongoTemplate mongoTemplate;

    public List<BannerSpec> findAll() {
        return mongoTemplate.findAll(BannerSpec.class);
    }

    public List<BannerSpec> findByMedia(String media) {
        Query query = Query.query(Criteria.where("media").is(media).and("active").is(true));
        return mongoTemplate.find(query, BannerSpec.class);
    }

    public List<BannerSpec> findByMediaIn(List<String> mediaList) {
        Query query = Query.query(Criteria.where("media").in(mediaList).and("active").is(true));
        return mongoTemplate.find(query, BannerSpec.class);
    }

    public List<BannerSpec> findByIds(List<String> ids) {
        Query query = Query.query(Criteria.where("id").in(ids));
        return mongoTemplate.find(query, BannerSpec.class);
    }

    public BannerSpec save(BannerSpec spec) {
        return mongoTemplate.save(spec);
    }

    public void deleteById(String id) {
        Query query = Query.query(Criteria.where("id").is(id));
        mongoTemplate.remove(query, BannerSpec.class);
    }
}
