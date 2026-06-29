package com.h3.creative.api;

import com.h3.creative.domain.BannerSpec;
import com.h3.creative.mongo.SpecMongoService;
import com.h3.creative.service.SpecInitService;
import lombok.RequiredArgsConstructor;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/api/spec")
@RequiredArgsConstructor
public class SpecController {

    private final SpecMongoService specMongoService;
    private final SpecInitService specInitService;

    @GetMapping
    public ResponseEntity<List<BannerSpec>> list(
            @RequestParam(required = false) String media
    ) {
        if (media != null) {
            return ResponseEntity.ok(specMongoService.findByMedia(media));
        }
        return ResponseEntity.ok(specMongoService.findAll());
    }

    @PostMapping
    public ResponseEntity<BannerSpec> save(@RequestBody BannerSpec spec) {
        return ResponseEntity.ok(specMongoService.save(spec));
    }

    @DeleteMapping("/{id}")
    public ResponseEntity<Map<String, String>> delete(@PathVariable String id) {
        specMongoService.deleteById(id);
        return ResponseEntity.ok(Map.of("result", "ok"));
    }

    /**
     * 기본 규격 일괄 삽입.
     * reset=true 이면 기존 데이터 전체 삭제 후 재삽입.
     */
    @PostMapping("/init")
    public ResponseEntity<Map<String, Object>> init(
            @RequestParam(defaultValue = "false") boolean reset
    ) {
        int count = specInitService.init(reset);
        return ResponseEntity.ok(Map.of("inserted", count, "reset", reset));
    }
}
