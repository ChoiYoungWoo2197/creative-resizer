package com.h3.creative.worker;

import com.h3.creative.config.AppConfig;
import org.junit.jupiter.api.Test;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.web.client.RestTemplate;

import java.lang.reflect.Field;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * /generate 전용 RestTemplate이 충분히 긴 read timeout을 갖는지 검증.
 *
 * 장애 원인: AppConfig의 단일 RestTemplate이 readTimeout=120s로 고정돼 있어
 * AI 이미지 생성(최대 9회 외부 호출)이 항상 120초 후 Read timed out으로 실패했다.
 *
 * 수정 후: generate 전용 RestTemplate = 900s, 분석용 = 120s.
 */
class WorkerClientTimeoutConfigTest {

    // ── helpers ──────────────────────────────────────────────────────────────

    private AppConfig appConfigWith(int connectSec, int analysisSec, int generateSec) throws Exception {
        AppConfig cfg = new AppConfig();
        setField(cfg, "connectTimeoutSeconds", connectSec);
        setField(cfg, "analysisReadTimeoutSeconds", analysisSec);
        setField(cfg, "generateReadTimeoutSeconds", generateSec);
        return cfg;
    }

    private static void setField(Object target, String name, int value) throws Exception {
        Field f = AppConfig.class.getDeclaredField(name);
        f.setAccessible(true);
        f.set(target, value);
    }

    private static Object getRequestFactory(RestTemplate rt) throws Exception {
        // requestFactory는 HttpAccessor(부모 클래스)에 선언됨
        Class<?> cls = rt.getClass();
        while (cls != null) {
            try {
                Field f = cls.getDeclaredField("requestFactory");
                f.setAccessible(true);
                return f.get(rt);
            } catch (NoSuchFieldException e) {
                cls = cls.getSuperclass();
            }
        }
        throw new NoSuchFieldException("requestFactory not found in RestTemplate hierarchy");
    }

    private static int readTimeoutMs(RestTemplate rt) throws Exception {
        Object factory = getRequestFactory(rt);
        Field fRead = SimpleClientHttpRequestFactory.class.getDeclaredField("readTimeout");
        fRead.setAccessible(true);
        return (int) fRead.get(factory);
    }

    private static int connectTimeoutMs(RestTemplate rt) throws Exception {
        Object factory = getRequestFactory(rt);
        Field fConn = SimpleClientHttpRequestFactory.class.getDeclaredField("connectTimeout");
        fConn.setAccessible(true);
        return (int) fConn.get(factory);
    }

    // ── T1: 기본값 — generate 900s, analysis 120s ─────────────────────────

    @Test
    void defaultConfig_generateTimeout_is900s() throws Exception {
        AppConfig cfg = appConfigWith(20, 120, 900);
        RestTemplate generateRt = cfg.generateRestTemplate();
        assertThat(readTimeoutMs(generateRt)).isEqualTo(900_000);
    }

    @Test
    void defaultConfig_analysisTimeout_is120s() throws Exception {
        AppConfig cfg = appConfigWith(20, 120, 900);
        RestTemplate analysisRt = cfg.restTemplate();
        assertThat(readTimeoutMs(analysisRt)).isEqualTo(120_000);
    }

    @Test
    void defaultConfig_connectTimeout_is20s() throws Exception {
        AppConfig cfg = appConfigWith(20, 120, 900);
        RestTemplate generateRt = cfg.generateRestTemplate();
        RestTemplate analysisRt = cfg.restTemplate();
        assertThat(connectTimeoutMs(generateRt)).isEqualTo(20_000);
        assertThat(connectTimeoutMs(analysisRt)).isEqualTo(20_000);
    }

    // ── T2: generate가 120초보다 훨씬 크다 ───────────────────────────────────

    @Test
    void generateTimeout_greaterThan_analysisTimeout() throws Exception {
        AppConfig cfg = appConfigWith(20, 120, 900);
        assertThat(readTimeoutMs(cfg.generateRestTemplate()))
                .isGreaterThan(readTimeoutMs(cfg.restTemplate()));
    }

    @Test
    void generateTimeout_greaterThan_120s() throws Exception {
        // 이전 장애: 120s 고정이어서 AI 생성이 항상 실패했음
        AppConfig cfg = appConfigWith(20, 120, 900);
        assertThat(readTimeoutMs(cfg.generateRestTemplate())).isGreaterThan(120_000);
    }

    // ── T3: 환경변수 커스터마이징 ─────────────────────────────────────────────

    @Test
    void customEnvVars_connectTimeout_applied() throws Exception {
        AppConfig cfg = appConfigWith(30, 180, 1200);
        assertThat(connectTimeoutMs(cfg.restTemplate())).isEqualTo(30_000);
        assertThat(connectTimeoutMs(cfg.generateRestTemplate())).isEqualTo(30_000);
    }

    @Test
    void customEnvVars_generateTimeout_applied() throws Exception {
        AppConfig cfg = appConfigWith(30, 180, 1200);
        assertThat(readTimeoutMs(cfg.generateRestTemplate())).isEqualTo(1_200_000);
    }

    @Test
    void customEnvVars_analysisTimeout_applied() throws Exception {
        AppConfig cfg = appConfigWith(30, 180, 1200);
        assertThat(readTimeoutMs(cfg.restTemplate())).isEqualTo(180_000);
    }

    // ── T4: generate와 analysis는 별도 인스턴스 ──────────────────────────────

    @Test
    void generateAndAnalysis_areDifferentInstances() throws Exception {
        AppConfig cfg = appConfigWith(20, 120, 900);
        RestTemplate gen = cfg.generateRestTemplate();
        RestTemplate ana = cfg.restTemplate();
        assertThat(gen).isNotSameAs(ana);
    }

    // ── T5: AI 생성 9회 × 최대 OpenAI 응답 시간(60s) = 540s → 900s 충분 ─────

    @Test
    void generateTimeout_covers_worst_case_9_openai_calls_at_60s_each() throws Exception {
        // 3 spec × 3 attempt × 60s/attempt = 540s worst case → 900s 충분
        int worstCaseSeconds = 3 * 3 * 60;
        AppConfig cfg = appConfigWith(20, 120, 900);
        int generateMs = readTimeoutMs(cfg.generateRestTemplate());
        assertThat(generateMs).isGreaterThan(worstCaseSeconds * 1_000);
    }
}
