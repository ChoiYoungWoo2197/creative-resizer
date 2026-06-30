package com.h3.creative.mongo;

import com.h3.creative.domain.BannerJob;
import lombok.RequiredArgsConstructor;
import org.springframework.data.mongodb.core.MongoTemplate;
import org.springframework.data.mongodb.core.query.Criteria;
import org.springframework.data.mongodb.core.query.Query;
import org.springframework.data.mongodb.core.query.Update;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;
import java.util.List;

@Service
@RequiredArgsConstructor
public class BannerMongoService {

    private final MongoTemplate mongoTemplate;

    public BannerJob save(BannerJob job) {
        job.setCreatedAt(LocalDateTime.now());
        job.setUpdatedAt(LocalDateTime.now());
        return mongoTemplate.save(job);
    }

    public BannerJob findById(String id) {
        return mongoTemplate.findById(id, BannerJob.class);
    }

    public List<BannerJob> findAll() {
        return mongoTemplate.findAll(BannerJob.class);
    }

    public void updateStatus(String id, String status) {
        Query query = Query.query(Criteria.where("id").is(id));
        Update update = new Update()
                .set("status", status)
                .set("updatedAt", LocalDateTime.now());
        mongoTemplate.updateFirst(query, update, BannerJob.class);
    }

    public void updateDone(String id, String zipPath, List<BannerJob.BannerResult> results) {
        Query query = Query.query(Criteria.where("id").is(id));
        Update update = new Update()
                .set("status", "done")
                .set("zipPath", zipPath)
                .set("results", results)
                .set("updatedAt", LocalDateTime.now());
        mongoTemplate.updateFirst(query, update, BannerJob.class);
    }

    public void updateFail(String id, String errorMessage) {
        Query query = Query.query(Criteria.where("id").is(id));
        Update update = new Update()
                .set("status", "fail")
                .set("errorMessage", errorMessage)
                .set("updatedAt", LocalDateTime.now());
        mongoTemplate.updateFirst(query, update, BannerJob.class);
    }

    public void updateFailWithResults(String id, String errorMessage, List<BannerJob.BannerResult> results) {
        Query query = Query.query(Criteria.where("id").is(id));
        Update update = new Update()
                .set("status", "fail")
                .set("errorMessage", errorMessage)
                .set("results", results)
                .set("updatedAt", LocalDateTime.now());
        mongoTemplate.updateFirst(query, update, BannerJob.class);
    }
}
