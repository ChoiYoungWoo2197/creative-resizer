package com.h3.creative.worker;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.*;

/**
 * Python Worker HTTP 응답 JSON ↔ Java WorkerResponse 계약 검증.
 *
 * 근본 버그 (commit fix):
 *   layout_compositor.py는 safeZoneViolations를 hardFailReasons에서 필터링한
 *   List<String>으로 반환한다. 이전 Java 선언(List<Map<String,Object>>)과의
 *   타입 불일치가 MismatchedInputException을 유발했다.
 */
class WorkerResponseDeserializationTest {

    private ObjectMapper mapper;

    @BeforeEach
    void setUp() {
        mapper = new ObjectMapper();
        mapper.registerModule(new JavaTimeModule());
        mapper.disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS);
    }

    // ── 1: 빈 응답 ──────────────────────────────────────────────────────────────

    @Test
    void emptyResults_deserializesOk() throws Exception {
        String json = """
                {"jobId":"j1","zipPath":"/z/j1.zip","count":0,
                 "results":[],"missingRatioTypes":[]}""";
        WorkerResponse r = mapper.readValue(json, WorkerResponse.class);
        assertThat(r.getJobId()).isEqualTo("j1");
        assertThat(r.getCount()).isEqualTo(0);
        assertThat(r.getResults()).isEmpty();
        assertThat(r.getMissingRatioTypes()).isEmpty();
    }

    // ── 2: 알 수 없는 필드 @JsonIgnoreProperties ──────────────────────────────

    @Test
    void unknownTopLevelField_ignored() throws Exception {
        // Python이 미래에 top-level 필드를 추가해도 무시
        String json = """
                {"jobId":"j2","count":1,"results":[],"newFieldFromPython":"ignored"}""";
        WorkerResponse r = mapper.readValue(json, WorkerResponse.class);
        assertThat(r.getJobId()).isEqualTo("j2");
    }

    @Test
    void unknownResultItemFields_hardFailReasons_droppedObjects_warnings_ignored() throws Exception {
        // object-reflow 경로에서 Python이 추가한 3개 필드: @JsonIgnoreProperties로 무시
        String json = """
                {"jobId":"j3","count":1,"results":[{
                  "media":"naver","name":"GFA","slug":"naver-gfa-1250x560",
                  "width":1250,"height":560,
                  "hardFailReasons":["safe zone: logo too small"],
                  "droppedObjects":["obj-logo-1"],
                  "warnings":["debugOverlayFailed: some err"],
                  "safeZoneViolations":[]
                }],"missingRatioTypes":[]}""";
        WorkerResponse r = mapper.readValue(json, WorkerResponse.class);
        WorkerResponse.ResultItem item = r.getResults().get(0);
        assertThat(item.getSlug()).isEqualTo("naver-gfa-1250x560");
        assertThat(item.getSafeZoneViolations()).isEmpty();
    }

    // ── 3: safeZoneViolations — 핵심 버그 수정 검증 ─────────────────────────────

    @Test
    void safeZoneViolations_emptyList_ok() throws Exception {
        // 빈 리스트: 모든 타입에서 통과 (버그 재현 조건 아님)
        String json = buildItemJson("\"safeZoneViolations\":[]");
        WorkerResponse r = mapper.readValue(json, WorkerResponse.class);
        assertThat(r.getResults().get(0).getSafeZoneViolations()).isEmpty();
    }

    @Test
    void safeZoneViolations_stringList_deserializesOk() throws Exception {
        // 핵심 버그: Python은 List<String>을 반환 → Java도 List<String>으로 선언해야 통과
        String json = buildItemJson("""
                "safeZoneViolations":["safe zone: logo too small","safe zone: cta outside boundary"]
                """);
        WorkerResponse r = mapper.readValue(json, WorkerResponse.class);
        List<String> violations = r.getResults().get(0).getSafeZoneViolations();
        assertThat(violations).hasSize(2);
        assertThat(violations).containsExactly(
                "safe zone: logo too small",
                "safe zone: cta outside boundary");
    }

    @Test
    void safeZoneViolations_singleString_ok() throws Exception {
        String json = buildItemJson("\"safeZoneViolations\":[\"safe zone: headline overflow\"]");
        WorkerResponse r = mapper.readValue(json, WorkerResponse.class);
        assertThat(r.getResults().get(0).getSafeZoneViolations())
                .containsExactly("safe zone: headline overflow");
    }

    // ── 4: nullable 필드 ──────────────────────────────────────────────────────

    @Test
    void layoutScore_null_ok() throws Exception {
        // emergency fallback 경로: layoutScore = None → null
        String json = buildItemJson("\"layoutScore\":null,\"candidateCount\":5");
        WorkerResponse.ResultItem item = mapper.readValue(json, WorkerResponse.class).getResults().get(0);
        assertThat(item.getLayoutScore()).isNull();
        assertThat(item.getCandidateCount()).isEqualTo(5);
    }

    @Test
    void candidateCount_null_layerReflowPath_ok() throws Exception {
        // layer-reflow / artboard-first 경로: candidateCount = null
        String json = buildItemJson("\"candidateCount\":null,\"layoutScore\":null");
        WorkerResponse.ResultItem item = mapper.readValue(json, WorkerResponse.class).getResults().get(0);
        assertThat(item.getCandidateCount()).isNull();
    }

    @Test
    void candidateCount_zero_ok() throws Exception {
        // object-reflow fallback 경로: candidateCount = 0
        String json = buildItemJson("\"candidateCount\":0");
        assertThat(mapper.readValue(json, WorkerResponse.class)
                .getResults().get(0).getCandidateCount()).isEqualTo(0);
    }

    // ── 5: 숫자 타입 ──────────────────────────────────────────────────────────

    @Test
    void primitiveWidthHeight_intValues() throws Exception {
        // width/height는 Java primitive int — Python도 항상 int 반환
        String json = buildItemJson("\"width\":1250,\"height\":560");
        WorkerResponse.ResultItem item = mapper.readValue(json, WorkerResponse.class).getResults().get(0);
        assertThat(item.getWidth()).isEqualTo(1250);
        assertThat(item.getHeight()).isEqualTo(560);
    }

    @Test
    void layoutScore_float_ok() throws Exception {
        // normal 경로: layoutScore = float
        String json = buildItemJson("\"layoutScore\":0.875,\"layoutScoreStatus\":\"normal\"");
        WorkerResponse.ResultItem item = mapper.readValue(json, WorkerResponse.class).getResults().get(0);
        assertThat(item.getLayoutScore()).isEqualTo(0.875);
        assertThat(item.getLayoutScoreStatus()).isEqualTo("normal");
    }

    @Test
    void fileSize_large_long_ok() throws Exception {
        // 큰 파일(10MB+): fileSize가 int 범위를 초과할 수 있어 Long 선언
        String json = buildItemJson("\"fileSize\":10485760");
        assertThat(mapper.readValue(json, WorkerResponse.class)
                .getResults().get(0).getFileSize()).isEqualTo(10_485_760L);
    }

    // ── 6: List<String> 역할 필드 ────────────────────────────────────────────

    @Test
    void usedObjectRoles_stringList_ok() throws Exception {
        String json = buildItemJson("\"usedObjectRoles\":[\"logo\",\"headline\",\"cta\"]");
        List<String> roles = mapper.readValue(json, WorkerResponse.class)
                .getResults().get(0).getUsedObjectRoles();
        assertThat(roles).containsExactly("logo", "headline", "cta");
    }

    @Test
    void missingObjectRoles_withNullElement_ok() throws Exception {
        // [o.get("role") for o in ai_objects] → role 없는 객체는 None → null 포함 가능
        String json = buildItemJson("\"missingObjectRoles\":[\"logo\",null]");
        List<String> roles = mapper.readValue(json, WorkerResponse.class)
                .getResults().get(0).getMissingObjectRoles();
        assertThat(roles).hasSize(2);
        assertThat(roles.get(0)).isEqualTo("logo");
        assertThat(roles.get(1)).isNull();
    }

    // ── 7: fallbackErrors — List<Map<String,Object>> 유지 확인 ────────────────

    @Test
    void fallbackErrors_dictList_ok() throws Exception {
        // psd fallback pipeline: [{"step":"psd_tools_composite","message":"..."}]
        String json = buildItemJson("""
                "fallbackErrors":[{"step":"psd_tools_composite","message":"composite() returned None"}]
                """);
        List<Map<String, Object>> errors = mapper.readValue(json, WorkerResponse.class)
                .getResults().get(0).getFallbackErrors();
        assertThat(errors).hasSize(1);
        assertThat(errors.get(0)).containsEntry("step", "psd_tools_composite");
    }

    @Test
    void fallbackErrors_emptyList_ok() throws Exception {
        String json = buildItemJson("\"fallbackErrors\":[]");
        assertThat(mapper.readValue(json, WorkerResponse.class)
                .getResults().get(0).getFallbackErrors()).isEmpty();
    }

    // ── helper ────────────────────────────────────────────────────────────────

    private String buildItemJson(String extraFields) {
        // object-reflow 경로의 최소 필드 + extraFields 주입
        return String.format("""
                {"jobId":"test","count":1,"missingRatioTypes":[],"results":[{
                  "media":"naver","name":"GFA 1250x560","slug":"naver-gfa-1250x560",
                  "width":1250,"height":560,
                  "fileName":"naver_naver-gfa-1250x560_1250x560.jpg",
                  "filePath":"/out/naver_1250x560.jpg",
                  "fileSize":245760,
                  "valid":true,
                  "validationMessage":"정상",
                  "safeZoneViolations":[],
                  "safeZonePassed":true,
                  "objectReflowSucceeded":true,
                  %s
                }]}""", extraFields);
    }
}
