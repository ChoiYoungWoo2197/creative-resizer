package com.h3.creative.api;

import com.h3.creative.domain.ApplyRequest;
import com.h3.creative.domain.BannerAiAnalysis;
import com.h3.creative.domain.BannerAiCompare;
import com.h3.creative.domain.BannerJob;
import com.h3.creative.domain.CompareRequest;
import com.h3.creative.domain.PsdObjectAnalysis;
import com.h3.creative.service.BannerAnalysisService;
import com.h3.creative.service.BannerCompareService;
import com.h3.creative.service.BannerService;
import com.h3.creative.service.PsdObjectAnalysisService;
import lombok.RequiredArgsConstructor;
import org.springframework.core.io.ByteArrayResource;
import org.springframework.core.io.FileSystemResource;
import org.springframework.core.io.Resource;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.IOException;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.List;
import java.util.zip.ZipEntry;
import java.util.zip.ZipOutputStream;

@RestController
@RequestMapping("/api/banner")
@RequiredArgsConstructor
public class BannerController {

    private final BannerService bannerService;
    private final BannerAnalysisService bannerAnalysisService;
    private final BannerCompareService bannerCompareService;
    private final PsdObjectAnalysisService psdObjectAnalysisService;
    private final com.h3.creative.worker.WorkerClient workerClient;

    @PostMapping("/analyze")
    public ResponseEntity<BannerAiAnalysis> analyze(@RequestParam MultipartFile file) throws IOException {
        return ResponseEntity.ok(bannerAnalysisService.analyze(file));
    }

    @PostMapping("/analyze-psd")
    public ResponseEntity<com.h3.creative.domain.PsdAnalysis> analyzePsd(
            @RequestParam MultipartFile psdFile) throws IOException {
        return ResponseEntity.ok(bannerService.analyzePsdLayers(psdFile));
    }

    @PostMapping("/psd/object-analysis")
    public ResponseEntity<PsdObjectAnalysis> psdObjectAnalysis(
            @RequestParam MultipartFile psdFile,
            @RequestParam(required = false) String selectedArtboardId,
            @RequestParam(defaultValue = "0") int artboardX,
            @RequestParam(defaultValue = "0") int artboardY,
            @RequestParam int artboardWidth,
            @RequestParam int artboardHeight
    ) throws IOException {
        PsdObjectAnalysis result = psdObjectAnalysisService.analyze(
                psdFile, selectedArtboardId, artboardX, artboardY, artboardWidth, artboardHeight);
        return ResponseEntity.ok(result);
    }

    @PostMapping("/upload")
    public ResponseEntity<BannerJob> upload(
            @RequestParam MultipartFile psdFile,
            @RequestParam String advertiser,
            @RequestParam String campaignName,
            @RequestParam List<String> specIds,
            @RequestParam(defaultValue = "smart-fit") String resizeMode,
            @RequestParam(defaultValue = "balanced") String smartFitStrength,
            @RequestParam(defaultValue = "center") String focalPosition,
            @RequestParam(defaultValue = "png") String outputFormat,
            @RequestParam(required = false) String aiAnalysisId,
            @RequestParam(required = false) Boolean aiApplied,
            @RequestParam(required = false) String aiRecommendedResizeMode,
            @RequestParam(required = false) String aiRecommendedSmartFitStrength,
            @RequestParam(required = false) String aiRecommendedFocalPosition,
            @RequestParam(defaultValue = "artboard-first") String psdMode,
            @RequestParam(required = false) List<String> selectedArtboardIds,
            @RequestParam(required = false) String objectAnalysisId,
            @RequestParam(required = false) Boolean objectReflowEnabled,
            @RequestParam(required = false) String pipelineVersion,
            @RequestParam(required = false) String pipelineType
    ) throws IOException {
        BannerJob job = bannerService.submit(psdFile, advertiser, campaignName, specIds, resizeMode,
                smartFitStrength, focalPosition, outputFormat,
                aiAnalysisId, aiApplied, aiRecommendedResizeMode, aiRecommendedSmartFitStrength, aiRecommendedFocalPosition,
                psdMode, selectedArtboardIds, objectAnalysisId, objectReflowEnabled, pipelineVersion, pipelineType);
        return ResponseEntity.ok(job);
    }

    @GetMapping("/job/{id}")
    public ResponseEntity<BannerJob> getJob(@PathVariable String id) {
        BannerJob job = bannerService.getJob(id);
        if (job == null) return ResponseEntity.notFound().build();
        return ResponseEntity.ok(job);
    }

    @GetMapping("/jobs")
    public ResponseEntity<List<BannerJob>> listJobs() {
        return ResponseEntity.ok(bannerService.listJobs());
    }

    @PostMapping("/jobs/{jobId}/compare")
    public ResponseEntity<BannerAiCompare> compare(
            @PathVariable String jobId,
            @RequestBody CompareRequest body
    ) throws IOException {
        return ResponseEntity.ok(bannerCompareService.compare(jobId, body.getSpecId()));
    }

    @PostMapping("/jobs/{jobId}/compare/{compareId}/apply")
    public ResponseEntity<BannerJob> applyCompare(
            @PathVariable String jobId,
            @PathVariable String compareId,
            @RequestBody ApplyRequest body
    ) {
        BannerJob job = bannerCompareService.apply(jobId, compareId, body.getSpecId(), body.getCandidate());
        if (job == null) return ResponseEntity.notFound().build();
        return ResponseEntity.ok(job);
    }

    @GetMapping("/compare/{compareId}/files/{filename:.+}")
    public ResponseEntity<Resource> compareFile(
            @PathVariable String compareId,
            @PathVariable String filename
    ) {
        BannerAiCompare compare = bannerCompareService.getCompare(compareId);
        if (compare == null || compare.getCandidates() == null) return ResponseEntity.notFound().build();

        BannerAiCompare.CandidateResult result = compare.getCandidates().stream()
                .filter(c -> filename.equals(c.getFileName()))
                .findFirst().orElse(null);
        if (result == null) return ResponseEntity.notFound().build();

        File file = new File(result.getFilePath());
        if (!file.exists()) return ResponseEntity.notFound().build();

        return ResponseEntity.ok()
                .contentType(MediaType.IMAGE_PNG)
                .body(new FileSystemResource(file));
    }

    @GetMapping("/job/{id}/preview/{filename:.+}")
    public ResponseEntity<Resource> preview(@PathVariable String id, @PathVariable String filename) {
        BannerJob job = bannerService.getJob(id);
        if (job == null || job.getResults() == null) return ResponseEntity.notFound().build();

        BannerJob.BannerResult result = job.getResults().stream()
                .filter(r -> filename.equals(r.getFileName()))
                .findFirst().orElse(null);

        if (result == null) return ResponseEntity.notFound().build();

        // AI 후보 적용됐으면 선택 후보 파일 우선
        String filePath = (result.getSelectedCandidateFilePath() != null && Boolean.TRUE.equals(result.getAiCompareApplied()))
                ? result.getSelectedCandidateFilePath()
                : result.getFilePath();

        File file = new File(filePath);
        if (!file.exists()) return ResponseEntity.notFound().build();

        MediaType mediaType = filename.endsWith(".jpg") || filename.endsWith(".jpeg")
                ? MediaType.IMAGE_JPEG
                : filename.endsWith(".webp")
                        ? MediaType.parseMediaType("image/webp")
                        : MediaType.IMAGE_PNG;

        return ResponseEntity.ok()
                .contentType(mediaType)
                .body(new FileSystemResource(file));
    }

    @GetMapping("/job/{id}/image/{filename:.+}")
    public ResponseEntity<Resource> downloadImage(@PathVariable String id, @PathVariable String filename) {
        BannerJob job = bannerService.getJob(id);
        if (job == null || job.getResults() == null) return ResponseEntity.notFound().build();

        BannerJob.BannerResult result = job.getResults().stream()
                .filter(r -> filename.equals(r.getFileName()))
                .findFirst().orElse(null);

        if (result == null) return ResponseEntity.notFound().build();

        // AI 후보 적용됐으면 선택 후보 파일 우선
        String filePath = (result.getSelectedCandidateFilePath() != null && Boolean.TRUE.equals(result.getAiCompareApplied()))
                ? result.getSelectedCandidateFilePath()
                : result.getFilePath();

        File file = new File(filePath);
        if (!file.exists()) return ResponseEntity.notFound().build();

        String encoded = URLEncoder.encode(filename, StandardCharsets.UTF_8).replace("+", "%20");
        return ResponseEntity.ok()
                .contentType(MediaType.APPLICATION_OCTET_STREAM)
                .header(HttpHeaders.CONTENT_DISPOSITION, "attachment; filename*=UTF-8''" + encoded)
                .body(new FileSystemResource(file));
    }

    @GetMapping("/job/{id}/download")
    public ResponseEntity<Resource> download(@PathVariable String id) throws IOException {
        BannerJob job = bannerService.getJob(id);
        if (job == null || !"done".equals(job.getStatus())) {
            return ResponseEntity.notFound().build();
        }

        String rawName = job.getAdvertiser() + "_" + job.getCampaignName() + ".zip";
        String encoded = URLEncoder.encode(rawName, StandardCharsets.UTF_8).replace("+", "%20");

        boolean hasApplied = job.getResults() != null &&
                job.getResults().stream().anyMatch(r -> Boolean.TRUE.equals(r.getAiCompareApplied()));

        if (!hasApplied) {
            File zip = new File(job.getZipPath());
            if (!zip.exists()) return ResponseEntity.notFound().build();
            return ResponseEntity.ok()
                    .contentType(MediaType.APPLICATION_OCTET_STREAM)
                    .header(HttpHeaders.CONTENT_DISPOSITION, "attachment; filename*=UTF-8''" + encoded)
                    .body(new FileSystemResource(zip));
        }

        // AI 후보 적용된 결과가 있으면 ZIP 동적 재생성 (파일명은 원본 규격명 유지)
        ByteArrayOutputStream baos = new ByteArrayOutputStream();
        try (ZipOutputStream zos = new ZipOutputStream(baos)) {
            for (BannerJob.BannerResult r : job.getResults()) {
                String filePath = (Boolean.TRUE.equals(r.getAiCompareApplied()) && r.getSelectedCandidateFilePath() != null)
                        ? r.getSelectedCandidateFilePath()
                        : r.getFilePath();
                File file = new File(filePath);
                if (!file.exists()) continue;
                zos.putNextEntry(new ZipEntry(r.getFileName()));
                zos.write(Files.readAllBytes(file.toPath()));
                zos.closeEntry();
            }
        }

        return ResponseEntity.ok()
                .contentType(MediaType.APPLICATION_OCTET_STREAM)
                .header(HttpHeaders.CONTENT_DISPOSITION, "attachment; filename*=UTF-8''" + encoded)
                .body(new ByteArrayResource(baos.toByteArray()));
    }

    /** P5 에디터용: 특정 결과의 layout_result.json 반환 */
    @GetMapping("/job/{id}/layout-result")
    public ResponseEntity<Object> layoutResult(
            @PathVariable String id,
            @RequestParam String fileName) throws IOException {
        BannerJob job = bannerService.getJob(id);
        if (job == null || job.getResults() == null) return ResponseEntity.notFound().build();

        BannerJob.BannerResult result = job.getResults().stream()
                .filter(r -> fileName.equals(r.getFileName()))
                .findFirst().orElse(null);
        if (result == null || result.getFilePath() == null) return ResponseEntity.notFound().build();

        // layout_result.json 은 result.png 와 같은 디렉토리에 위치
        File layoutFile = new File(new File(result.getFilePath()).getParentFile(), "layout_result.json");
        if (!layoutFile.exists()) return ResponseEntity.notFound().build();

        byte[] bytes = Files.readAllBytes(layoutFile.toPath());
        return ResponseEntity.ok()
                .contentType(MediaType.APPLICATION_JSON)
                .body(bytes);
    }

    /** P5 에디터용: 레이어 개별 PNG 서빙 (경로 순회 방지) */
    @GetMapping("/job/{id}/layer-file")
    public ResponseEntity<Resource> layerFile(
            @PathVariable String id,
            @RequestParam String rel) throws IOException {
        BannerJob job = bannerService.getJob(id);
        if (job == null || job.getResults() == null || job.getResults().isEmpty())
            return ResponseEntity.notFound().build();

        // filePath 예: /app/storage/outputs/{jobId}/{jobId}/clean_v1/04_composite/01_spec.png
        // job 루트: 그 3단계 위 (04_composite → clean_v1 → {jobId})
        File resultFile = new File(job.getResults().get(0).getFilePath());
        File jobRoot = resultFile.getParentFile().getParentFile().getParentFile();

        File target = new File(jobRoot, rel).getCanonicalFile();
        // 경로 순회 방지: jobRoot 밖으로 벗어나지 않았는지 확인
        if (!target.getPath().startsWith(jobRoot.getCanonicalPath())) {
            return ResponseEntity.badRequest().build();
        }
        if (!target.exists()) return ResponseEntity.notFound().build();

        return ResponseEntity.ok()
                .contentType(MediaType.IMAGE_PNG)
                .body(new FileSystemResource(target));
    }

    /** P5 에디터용: 수정된 레이어 위치로 P4 재합성 */
    @PostMapping("/job/{id}/recomposite")
    @SuppressWarnings("unchecked")
    public ResponseEntity<Object> recomposite(
            @PathVariable String id,
            @RequestBody java.util.Map<String, Object> body) throws IOException {
        BannerJob job = bannerService.getJob(id);
        if (job == null || job.getResults() == null) return ResponseEntity.notFound().build();

        String fileName = (String) body.get("fileName");
        BannerJob.BannerResult result = job.getResults().stream()
                .filter(r -> fileName.equals(r.getFileName()))
                .findFirst().orElse(null);
        if (result == null || result.getFilePath() == null) return ResponseEntity.notFound().build();

        File resultFile   = new File(result.getFilePath());
        File compositeDir = resultFile.getParentFile();           // .../clean_v1/04_composite/
        File jobRoot      = compositeDir.getParentFile().getParentFile(); // .../clean_v1/ → job root

        // layout_result.json 에서 bg_file 경로를 읽어 절대경로로 변환
        File layoutFile = new File(compositeDir, "layout_result.json");
        File bgFile;
        if (layoutFile.exists()) {
            @SuppressWarnings("unchecked")
            java.util.Map<String, Object> layoutData =
                    new com.fasterxml.jackson.databind.ObjectMapper()
                            .readValue(layoutFile, java.util.Map.class);
            String bgRelative = (String) layoutData.get("bg_file");
            bgFile = bgRelative != null ? new File(jobRoot, bgRelative) : null;
        } else {
            bgFile = null;
        }
        if (bgFile == null || !bgFile.exists()) {
            return ResponseEntity.internalServerError()
                    .body(java.util.Map.of("error", "bg 이미지를 찾을 수 없습니다: layout_result.json 확인 필요"));
        }

        java.util.Map<String, Object> payload = new java.util.LinkedHashMap<>();
        payload.put("jobId", id);
        payload.put("bgPath", bgFile.getAbsolutePath());
        payload.put("psdPath", job.getPsdPath());
        payload.put("targetW", result.getWidth());
        payload.put("targetH", result.getHeight());
        payload.put("sourceW", result.getSourceWidth() != null ? result.getSourceWidth() : result.getWidth());
        payload.put("sourceH", result.getSourceHeight() != null ? result.getSourceHeight() : result.getHeight());
        payload.put("layers", body.get("layers"));
        payload.put("resultPath", result.getFilePath());

        java.util.Map<String, Object> workerResp = workerClient.recomposite(payload);
        if (workerResp.containsKey("error")) {
            return ResponseEntity.internalServerError().body(workerResp);
        }
        return ResponseEntity.ok(workerResp);
    }
}
