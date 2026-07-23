package com.h3.creative.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.h3.creative.domain.PsdObjectAnalysis;
import com.h3.creative.mongo.PsdObjectAnalysisMongoService;
import com.h3.creative.worker.WorkerClient;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.junit.jupiter.api.io.TempDir;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.http.HttpHeaders;
import org.springframework.test.util.ReflectionTestUtils;

import java.io.File;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

/**
 * PsdObjectAnalysisService — cache key / cache hit / cache miss unit tests.
 *
 * No Spring context: uses Mockito + ReflectionTestUtils to inject @Value fields.
 * MongoDB and OpenAI are mocked; real SHA-256 computation is exercised.
 */
@ExtendWith(MockitoExtension.class)
class PsdObjectAnalysisServiceCacheTest {

    @Mock private OpenAiChatClient openAiChatClient;
    @Mock private WorkerClient workerClient;
    @Mock private PsdObjectAnalysisMongoService mongoService;

    @TempDir Path tempDir;

    private PsdObjectAnalysisService service;

    /** Minimal GPT response returning one background object. */
    private static final String GPT_RESPONSE_JSON =
            "{\"objects\":[{\"id\":\"obj_1\",\"role\":\"background\",\"label\":\"배경\","
            + "\"importance\":\"required\",\"bbox\":{\"x\":0,\"y\":0,\"width\":100,\"height\":100},"
            + "\"confidence\":0.99,\"reflowBehavior\":\"fill_canvas\","
            + "\"safeZoneRequired\":false,\"canScale\":true,\"canCrop\":true}]}";

    @BeforeEach
    void setUp() {
        service = new PsdObjectAnalysisService(openAiChatClient, workerClient, mongoService, new ObjectMapper());
        ReflectionTestUtils.setField(service, "uploadDir", tempDir.toString());
        ReflectionTestUtils.setField(service, "openAiApiKey", "sk-test-key");
        ReflectionTestUtils.setField(service, "openAiObjectModel", "gpt-5-mini");
        ReflectionTestUtils.setField(service, "openAiFallbackModel", "gpt-4.1-mini");
        ReflectionTestUtils.setField(service, "openAiImageDetail", "high");
    }

    // ── Helpers ────────────────────────────────────────────────────────────────

    /** Create a temp PSD-like file with known bytes for SHA-256 verification. */
    private File createTempPsd(String filename, byte[] content) throws Exception {
        Path p = tempDir.resolve(filename);
        Files.write(p, content);
        return p.toFile();
    }

    private PsdObjectAnalysis readyDoc(String cacheKey, String sha256) {
        PsdObjectAnalysis doc = new PsdObjectAnalysis();
        doc.setId("mongo_id_cached");
        doc.setCacheKey(cacheKey);
        doc.setSourceFileSha256(sha256);
        doc.setStatus("READY");
        doc.setGptRequestCount(1);
        doc.setAnalysisCacheHit(false);
        doc.setObjects(Collections.emptyList());
        return doc;
    }

    /** Stub workerClient.extractArtboard to return previewBase64 + empty layers. */
    @SuppressWarnings("unchecked")
    private void stubWorkerExtract() throws Exception {
        Map<String, Object> extractResult = new LinkedHashMap<>();
        extractResult.put("previewBase64", "aGVsbG8="); // base64("hello")
        extractResult.put("layers", Collections.emptyList());
        extractResult.put("canvasWidth", 1200);
        extractResult.put("canvasHeight", 628);
        when(workerClient.extractArtboard(anyString(), any())).thenReturn(extractResult);
    }

    /** Stub openAiChatClient.call to return GPT JSON response. */
    @SuppressWarnings("unchecked")
    private void stubGpt() throws Exception {
        Map<String, Object> msgResp = Map.of("content", GPT_RESPONSE_JSON);
        Map<String, Object> choice = Map.of("message", msgResp);
        Map<String, Object> body = Map.of("choices", List.of(choice));
        OpenAiCallResult callResult = mock(OpenAiCallResult.class);
        when(callResult.getBody()).thenReturn(body);
        when(callResult.getUsedModel()).thenReturn("gpt-5-mini");
        when(callResult.getRequestedModel()).thenReturn("gpt-5-mini");
        when(openAiChatClient.call(anyString(), anyString(), any(HttpHeaders.class), any(), anyInt(), any()))
                .thenReturn(callResult);
    }

    /** Stub workerClient.matchLayers to return objects unchanged. */
    @SuppressWarnings("unchecked")
    private void stubWorkerMatch(List<Map<String, Object>> objects) throws Exception {
        when(workerClient.matchLayers(any(), any(), any(), anyInt(), anyInt()))
                .thenReturn(Map.of("matchedObjects", objects));
    }

    // ── Test: Cache miss → GPT is called once ─────────────────────────────────

    @Test
    void cacheMiss_gptCalledOnce_savedAsReady() throws Exception {
        byte[] psdBytes = "fake-psd-bytes".getBytes();

        when(mongoService.findByCacheKey(anyString())).thenReturn(null); // cache miss

        stubWorkerExtract();
        stubGpt();
        stubWorkerMatch(Collections.emptyList());

        PsdObjectAnalysis saved = new PsdObjectAnalysis();
        saved.setId("new_doc_id");
        saved.setStatus("READY");
        saved.setGptRequestCount(1);
        saved.setAnalysisCacheHit(false);
        when(mongoService.save(any())).thenReturn(saved);

        var psdFile = mockMultipartFile("test.psd", psdBytes);
        PsdObjectAnalysis result = service.analyze(psdFile, "ab1", 0, 0, 1200, 628);

        // GPT must be called exactly once
        verify(openAiChatClient, times(1)).call(anyString(), anyString(), any(), any(), anyInt(), any());

        // MongoDB save called with READY status and gptRequestCount=1
        ArgumentCaptor<PsdObjectAnalysis> captor = ArgumentCaptor.forClass(PsdObjectAnalysis.class);
        verify(mongoService, times(1)).save(captor.capture());
        PsdObjectAnalysis persisted = captor.getValue();
        assertThat(persisted.getStatus()).isEqualTo("READY");
        assertThat(persisted.getGptRequestCount()).isEqualTo(1);
        assertThat(persisted.getAnalysisCacheHit()).isFalse();
        assertThat(persisted.getSourceFileSha256()).isNotBlank();
        assertThat(persisted.getCacheKey()).isNotBlank();
    }

    // ── Test: Cache hit → GPT is NOT called ───────────────────────────────────

    @Test
    void cacheHit_gptNotCalled_cachedDocReturned() throws Exception {
        byte[] psdBytes = "fake-psd-bytes".getBytes();
        String sha = hexSha256(psdBytes);

        PsdObjectAnalysis cached = readyDoc("some-cache-key", sha);
        when(mongoService.findByCacheKey(anyString())).thenReturn(cached);

        // extractArtboard is still called for preview refresh on cache hit
        stubWorkerExtract();

        var psdFile = mockMultipartFile("test.psd", psdBytes);
        PsdObjectAnalysis result = service.analyze(psdFile, "ab1", 0, 0, 1200, 628);

        // GPT must NOT be called
        verify(openAiChatClient, never()).call(anyString(), anyString(), any(), any(), anyInt(), any());
        // mongoService.save must NOT be called (cached doc is returned directly)
        verify(mongoService, never()).save(any());
        // result reflects cache hit
        assertThat(result.getAnalysisCacheHit()).isTrue();
    }

    // ── Test: computeCacheKey is deterministic ────────────────────────────────

    @Test
    void computeCacheKey_sameInputs_sameKey() throws Exception {
        byte[] psdBytes = "deterministic-psd".getBytes();
        String sha = hexSha256(psdBytes);

        // Two separate analyses with identical inputs should hit the same cache key
        when(mongoService.findByCacheKey(anyString()))
                .thenReturn(null)   // first call: miss
                .thenReturn(null);  // second call: also miss (we test key generation, not actual caching)

        stubWorkerExtract();
        stubGpt();
        stubWorkerMatch(Collections.emptyList());

        PsdObjectAnalysis saved1 = new PsdObjectAnalysis();
        saved1.setId("id1");
        saved1.setStatus("READY");
        PsdObjectAnalysis saved2 = new PsdObjectAnalysis();
        saved2.setId("id2");
        saved2.setStatus("READY");
        when(mongoService.save(any())).thenReturn(saved1, saved2);

        ArgumentCaptor<PsdObjectAnalysis> captor = ArgumentCaptor.forClass(PsdObjectAnalysis.class);

        service.analyze(mockMultipartFile("test.psd", psdBytes), "ab1", 0, 0, 1200, 628);
        service.analyze(mockMultipartFile("test.psd", psdBytes), "ab1", 0, 0, 1200, 628);

        verify(mongoService, times(2)).save(captor.capture());
        List<PsdObjectAnalysis> all = captor.getAllValues();

        // Both analyses produce the same cacheKey (deterministic)
        assertThat(all.get(0).getCacheKey()).isEqualTo(all.get(1).getCacheKey());
        // Both have the same sourceFileSha256
        assertThat(all.get(0).getSourceFileSha256()).isEqualTo(sha);
        assertThat(all.get(0).getSourceFileSha256()).isEqualTo(all.get(1).getSourceFileSha256());
    }

    // ── Test: Different artboard dimensions → different cacheKey ─────────────

    @Test
    void computeCacheKey_differentArtboard_differentKey() throws Exception {
        byte[] psdBytes = "psd-bytes".getBytes();

        when(mongoService.findByCacheKey(anyString())).thenReturn(null);
        stubWorkerExtract();
        stubGpt();
        stubWorkerMatch(Collections.emptyList());

        PsdObjectAnalysis s1 = new PsdObjectAnalysis(); s1.setStatus("READY");
        PsdObjectAnalysis s2 = new PsdObjectAnalysis(); s2.setStatus("READY");
        when(mongoService.save(any())).thenReturn(s1, s2);

        ArgumentCaptor<PsdObjectAnalysis> cap = ArgumentCaptor.forClass(PsdObjectAnalysis.class);

        service.analyze(mockMultipartFile("t.psd", psdBytes), "ab1", 0, 0, 1200, 628);
        service.analyze(mockMultipartFile("t.psd", psdBytes), "ab2", 0, 0, 900, 900);

        verify(mongoService, times(2)).save(cap.capture());
        List<PsdObjectAnalysis> all = cap.getAllValues();
        assertThat(all.get(0).getCacheKey()).isNotEqualTo(all.get(1).getCacheKey());
    }

    // ── Test: saved document has analysisVersion set ──────────────────────────

    @Test
    void savedDoc_hasAnalysisVersion_andModelSet() throws Exception {
        when(mongoService.findByCacheKey(anyString())).thenReturn(null);
        stubWorkerExtract();
        stubGpt();
        stubWorkerMatch(Collections.emptyList());

        PsdObjectAnalysis saved = new PsdObjectAnalysis();
        saved.setId("id");
        saved.setStatus("READY");
        when(mongoService.save(any())).thenReturn(saved);

        service.analyze(mockMultipartFile("t.psd", "bytes".getBytes()), "ab1", 0, 0, 1200, 628);

        ArgumentCaptor<PsdObjectAnalysis> cap = ArgumentCaptor.forClass(PsdObjectAnalysis.class);
        verify(mongoService).save(cap.capture());
        PsdObjectAnalysis doc = cap.getValue();
        assertThat(doc.getAnalysisVersion()).isEqualTo("psd-object-map-v2");
        assertThat(doc.getModel()).isEqualTo("gpt-5-mini");
        assertThat(doc.getStatus()).isEqualTo("READY");
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    /** Compute SHA-256 hex of bytes (mirrors PsdObjectAnalysisService.computeFileSha256). */
    private static String hexSha256(byte[] bytes) throws Exception {
        MessageDigest md = MessageDigest.getInstance("SHA-256");
        byte[] digest = md.digest(bytes);
        StringBuilder sb = new StringBuilder(64);
        for (byte b : digest) sb.append(String.format("%02x", b));
        return sb.toString();
    }

    private org.springframework.web.multipart.MultipartFile mockMultipartFile(
            String originalFilename, byte[] bytes) throws Exception {
        var mf = mock(org.springframework.web.multipart.MultipartFile.class);
        when(mf.getOriginalFilename()).thenReturn(originalFilename);
        // transferTo writes the bytes to the destination file
        doAnswer(invocation -> {
            File dest = invocation.getArgument(0);
            Files.write(dest.toPath(), bytes);
            return null;
        }).when(mf).transferTo(any(File.class));
        return mf;
    }
}
