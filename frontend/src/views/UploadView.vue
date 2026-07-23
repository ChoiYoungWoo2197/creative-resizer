<template>
  <div class="two-panel">

    <!-- ======== LEFT SIDEBAR ======== -->
    <aside class="sidebar">
      <div class="sidebar-scroll">

        <!-- Upload -->
        <div class="sec">
          <div class="sec-head" @click="uploadOpen = !uploadOpen">
            <span class="sec-title">배너 업로드 <span class="sec-hint">(이미지)</span></span>
            <span class="chevron" :class="{ up: uploadOpen }">›</span>
          </div>
          <div v-show="uploadOpen" class="sec-body">
            <div v-if="!form.psdFile"
              class="drop-zone" :class="{ dragover }"
              @click="$refs.fileInput.click()"
              @dragover.prevent="dragover = true"
              @dragleave.prevent="dragover = false"
              @drop.prevent="onDrop"
            >
              <input ref="fileInput" type="file" accept=".psd,.png,.jpg,.jpeg,.webp,.gif,.tiff,.bmp" style="display:none" @change="onInputChange" />
              <div class="drop-ico-wrap">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" class="drop-svg">
                  <path d="M12 15V5M12 5L8.5 8.5M12 5L15.5 8.5" stroke="#7C3AED" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                  <path d="M3 18C3 19.657 4.343 21 6 21H18C19.657 21 21 19.657 21 18" stroke="#7C3AED" stroke-width="2" stroke-linecap="round"/>
                </svg>
              </div>
              <div class="drop-label">클릭 또는 드래그</div>
              <div class="drop-hint">PSD · PNG · JPG · WebP · GIF</div>
            </div>
            <div v-else class="preview-block">
              <div class="preview-img-wrap">
                <div v-if="previewLoading" class="preview-skeleton">
                  <span class="preview-spin" /><span>미리보기 생성 중...</span>
                </div>
                <div v-else-if="previewError" class="preview-fallback">
                  <div class="fallback-badge">{{ fileExt }}</div><div class="fallback-text">미리보기 불가</div>
                </div>
                <img v-else-if="previewUrl" :src="previewUrl" class="preview-img" alt="PSD 미리보기" />
              </div>
              <div class="file-info-row">
                <div class="file-meta">
                  <div class="file-name">{{ form.psdFile.name }}</div>
                  <div class="file-size">{{ (form.psdFile.size/1024/1024).toFixed(1) }} MB<span v-if="previewSize"> · {{ previewSize }}</span></div>
                </div>
                <button class="file-change" @click="clearFile">변경</button>
              </div>
              <!-- PSD 처리 방식: 자동 선택 배지만 표시 (선택 UI 숨김) -->
              <div v-if="isPsdFile" class="psd-auto-badge">
                <template v-if="psdLayerAnalyzing">
                  <span class="spinner" style="width:10px;height:10px;border-width:1.5px;display:inline-block;" />
                  레이어 분석 중...
                </template>
                <template v-else-if="psdLayerAnalysis?.layerReadable === false">
                  ⚠ 레이어 분석이 제한되어 이미지 기반으로 처리됩니다.
                </template>
                <template v-else-if="psdMode === 'object-reflow'">
                  ✦ 객체 기반 재배치 자동 선택됨
                </template>
                <template v-else>
                  ✦ 자동 최적화 적용 중
                </template>
              </div>
              <button class="ai-analyze-btn" :disabled="aiAnalyzing || !previewUrl" @click="runAiAnalyze">
                <span v-if="aiAnalyzing" class="spinner" style="width:12px;height:12px;border-width:1.5px;" />
                <span v-else>✦</span>
                {{ aiAnalyzing ? 'AI 분석 중...' : 'AI 추천 분석' }}
              </button>
              <div v-if="aiAnalysis" class="ai-result-card">
                <div class="ai-result-head">
                  <span class="ai-result-star">✦</span> AI 분석 결과
                  <span class="ai-conf">신뢰도 {{ Math.round((aiAnalysis.confidence ?? 0) * 100) }}%</span>
                </div>
                <!-- 이미지 분석 -->
                <div class="ai-analysis-section">
                  <div class="ai-analysis-row">
                    <span class="ai-analysis-label">소재 유형</span>
                    <span class="ai-badge" :class="aiAnalysis.creativeType">{{ creativeTypeLabel(aiAnalysis.creativeType) }}</span>
                  </div>
                  <div class="ai-analysis-row">
                    <span class="ai-analysis-label">텍스트 밀도</span>
                    <span class="ai-badge" :class="'density-' + aiAnalysis.textDensity">{{ densityLabel(aiAnalysis.textDensity) }}</span>
                  </div>
                  <div class="ai-analysis-row">
                    <span class="ai-analysis-label">잘림 위험</span>
                    <span class="ai-badge" :class="'risk-' + aiAnalysis.edgeRisk">{{ riskLabel(aiAnalysis.edgeRisk) }}</span>
                  </div>
                  <div v-if="aiAnalysis.mainSubjectDescription" class="ai-analysis-row">
                    <span class="ai-analysis-label">주요 피사체</span>
                    <span class="ai-subject-desc">{{ aiAnalysis.mainSubjectDescription }}</span>
                  </div>
                </div>
                <!-- 분석 이유 -->
                <div class="ai-result-reason">{{ aiAnalysis.reason }}</div>
                <!-- 추천 설정 -->
                <div class="ai-result-settings">
                  <span class="ai-tag">{{ resizeLabel(aiAnalysis.resizeMode) }}</span>
                  <span class="ai-tag">강도: {{ strengthLabel(aiAnalysis.smartFitStrength) }}</span>
                  <span class="ai-tag">위치: {{ posLabel(aiAnalysis.focalPosition) }}</span>
                </div>
                <div v-if="aiAnalysis.warnings?.length" class="ai-result-warnings">
                  <div v-for="w in aiAnalysis.warnings" :key="w" class="ai-warn-item">⚠ {{ w }}</div>
                </div>
                <!-- 추천 근거 -->
                <div v-if="aiAnalysis.recommendedBecause?.length" class="ai-quality-section">
                  <div class="ai-quality-label">추천 근거</div>
                  <div v-for="r in aiAnalysis.recommendedBecause" :key="r" class="ai-quality-item ai-quality-good">✓ {{ r }}</div>
                </div>
                <!-- 잘림 위험 영역 -->
                <div v-if="aiAnalysis.cropRiskAreas?.length" class="ai-quality-section">
                  <div class="ai-quality-label">잘림 위험 영역</div>
                  <div v-for="a in aiAnalysis.cropRiskAreas" :key="a" class="ai-quality-item ai-quality-warn">⚠ {{ a }}</div>
                </div>
                <!-- 피해야 할 옵션 -->
                <div v-if="aiAnalysis.avoidOptions?.length" class="ai-quality-section">
                  <div class="ai-quality-label">피해야 할 옵션</div>
                  <div v-for="o in aiAnalysis.avoidOptions" :key="o" class="ai-quality-item ai-quality-danger">✕ {{ o }}</div>
                </div>
                <!-- AI 요소 분석 (3.5차) -->
                <div v-if="aiAnalysis.requiredGroups?.length || aiAnalysis.priorityGroups?.length || aiAnalysis.optionalGroups?.length" class="ai-element-section">
                  <div class="ai-quality-label">AI 요소 분석</div>
                  <div v-if="aiAnalysis.requiredGroups?.length" class="ai-element-row">
                    <span class="ai-el-label ai-el-required">필수</span>
                    <div class="ai-el-tags">
                      <span v-for="gid in aiAnalysis.requiredGroups" :key="gid" class="ai-el-tag ai-el-tag-required">
                        {{ getGroupName(gid, aiAnalysis) }}
                      </span>
                    </div>
                  </div>
                  <div v-if="aiAnalysis.priorityGroups?.length" class="ai-element-row">
                    <span class="ai-el-label ai-el-priority">우선순위</span>
                    <div class="ai-el-tags">
                      <span v-for="gid in aiAnalysis.priorityGroups" :key="gid" class="ai-el-tag ai-el-tag-priority">
                        {{ getGroupName(gid, aiAnalysis) }}
                      </span>
                    </div>
                  </div>
                  <div v-if="aiAnalysis.optionalGroups?.length" class="ai-element-row">
                    <span class="ai-el-label ai-el-optional">선택</span>
                    <div class="ai-el-tags">
                      <span v-for="gid in aiAnalysis.optionalGroups" :key="gid" class="ai-el-tag ai-el-tag-optional">
                        {{ getGroupName(gid, aiAnalysis) }}
                      </span>
                    </div>
                  </div>
                </div>
                <button class="ai-apply-btn" :class="{ applied: aiApplied }" @click="applyAiAnalysis">
                  {{ aiApplied ? '✓ 적용됨' : '추천 적용' }}
                </button>
              </div>
            </div>
            <div class="field-stack">
              <div class="input-wrap">
                <svg class="input-ico" width="14" height="14" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="8" r="4" stroke="#B0B8C1" stroke-width="2"/><path d="M4 20c0-4 3.582-7 8-7s8 3 8 7" stroke="#B0B8C1" stroke-width="2" stroke-linecap="round"/></svg>
                <input v-model="form.advertiser" class="side-input" placeholder="광고주명" />
              </div>
              <div class="input-wrap">
                <svg class="input-ico" width="14" height="14" viewBox="0 0 24 24" fill="none"><rect x="3" y="6" width="18" height="14" rx="2" stroke="#B0B8C1" stroke-width="2"/><path d="M3 10h18M8 3v3M16 3v3" stroke="#B0B8C1" stroke-width="2" stroke-linecap="round"/></svg>
                <input v-model="form.campaignName" class="side-input" placeholder="캠페인명" />
              </div>
            </div>
          </div>
        </div>

        <!-- Media guide -->
        <div class="sec">
          <div class="sec-head" @click="mediaOpen = !mediaOpen">
            <span class="sec-title">매체 가이드 <span class="info-badge">ⓘ</span></span>
            <span class="chevron" :class="{ up: mediaOpen }">›</span>
          </div>
          <div v-show="mediaOpen">
            <div v-if="specsLoading" class="side-loading">불러오는 중...</div>
            <div v-else-if="naverLoadError" class="spec-load-error">
              ⚠ Naver 매체 가이드 로드 실패
              <button class="spec-retry-btn" @click="loadSpecs">재시도</button>
            </div>
            <template v-else>
              <div v-for="platform in platformOrder.filter(p => groupedSpecs[p])" :key="platform">
                <div class="pf-row" @click="toggleExpand(platform)">
                  <input type="checkbox" class="check"
                    :checked="isPlatformAllSelected(platform)"
                    :indeterminate.prop="isPlatformPartial(platform)"
                    @change.stop="togglePlatform(platform)" @click.stop />
                  <span class="pf-dot" :style="{ background: platformCfg[platform]?.color }" />
                  <span class="pf-name">{{ platformCfg[platform]?.label ?? platform }}</span>
                  <span class="pf-region">{{ platformCfg[platform]?.region }}</span>
                  <span class="pf-cnt">({{ platformSelectedCount(platform) }}/{{ groupedSpecs[platform].length }})</span>
                  <span class="pf-chv" :class="{ open: expandedPlatforms[platform] }">›</span>
                </div>
                <div v-show="expandedPlatforms[platform]" class="spec-items">
                  <div v-for="spec in groupedSpecs[platform]" :key="spec.id"
                    class="spec-item" :class="{ on: selectedSpecIds.includes(spec.id) }"
                    @click="toggleSpec(spec.id)">
                    <input type="checkbox" class="check sm" :checked="selectedSpecIds.includes(spec.id)" @click.stop @change="toggleSpec(spec.id)" />
                    <span class="sp-name">{{ spec.placementName }}</span>
                    <span class="sp-dim">{{ spec.width }}×{{ spec.height }}</span>
                    <span v-if="spec.safeZone" class="sp-sz-badge">SZ</span>
                  </div>
                </div>
              </div>
            </template>
          </div>
        </div>

        <!-- AI 자동 재구성 -->
        <div class="sec">
          <div class="sec-head" @click="advOpen = !advOpen">
            <span class="sec-title">렌더링 옵션</span>
            <span class="chevron" :class="{ up: advOpen }">›</span>
          </div>
          <div v-show="advOpen" class="sec-body adv-body">
            <div class="ai-only-notice">
              <span class="ai-only-icon">✦</span>
              <div class="ai-only-content">
                <strong class="ai-only-title">AI 자동 재구성</strong>
                <span class="ai-only-desc">Stage 20.3 Source-Faithful Repair — 소재의 핵심 요소를 보존하면서 AI가 각 규격에 맞는 배경을 자동 생성합니다.</span>
              </div>
            </div>
            <div class="adv-row">
              <span class="adv-label">포맷</span>
              <div class="adv-chips">
                <button v-for="f in formatOptions" :key="f.value" type="button"
                  class="adv-chip" :class="{ on: form.outputFormat === f.value }"
                  @click="form.outputFormat = f.value">{{ f.label }}</button>
              </div>
            </div>
          </div>
        </div>

      </div>

      <div class="sidebar-foot">
        <div class="foot-info">선택된 사이즈 <b>{{ selectedSpecIds.length }}</b>개</div>
        <button class="gen-btn"
          :disabled="loading || !selectedSpecIds.length || !form.psdFile || !form.advertiser || !form.campaignName"
          @click="submit">
          <span v-if="loading" class="spinner" />
          <span v-else class="gen-star">✦</span>
          {{ loading ? '생성 중...' : '배너 생성' }}
        </button>
      </div>
    </aside>

    <!-- ======== RIGHT PANEL ======== -->
    <div class="right-panel">
      <div class="right-scroll">

        <!-- Hero row -->
        <div class="hero-row">
          <div class="hero-left">
            <div class="sparkles">
              <span class="sp sp1">✦</span>
              <span class="sp sp2">✧</span>
              <span class="sp sp3">✦</span>
            </div>
            <h1 class="hero-title">
              선택된 사이즈 <span class="accent-num">{{ selectedSpecIds.length }}개</span>
            </h1>
            <p class="hero-sub">AI가 최적화된 크리에이티브를 빠르게 생성합니다.</p>
          </div>
          <div class="ai-insight-card">
            <div class="ai-insight-head">
              <span class="ai-star">✦</span> AI 추천 인사이트
            </div>
            <div class="ai-features">
              <div v-for="f in aiFeatures" :key="f.id" class="ai-feat">
                <span class="ai-feat-ico">{{ f.icon }}</span>
                <div class="ai-feat-title">{{ f.title }}</div>
                <div class="ai-feat-desc">{{ f.desc }}</div>
              </div>
            </div>
          </div>
        </div>

        <!-- AI 객체 분석 섹션 (PSD 전용) -->
        <div v-if="isPsdFile && psdLayerAnalysis" class="oa-section">
          <!-- 툴바 -->
          <div class="oa-toolbar">
            <div class="oa-toolbar-left">
              <span class="oa-title">AI 객체 분석</span>
              <span class="oa-beta">Beta</span>
              <!-- 자동 선택 분석 영역 정보 -->
              <span class="oa-artboard-info">
                <template v-if="detectedArtboards.length > 0">
                  분석 영역: {{ detectedArtboards.find(a => a.id === objAnalysisArtboardId) ? detectedArtboards.find(a => a.id === objAnalysisArtboardId).name : detectedArtboards[0]?.name }} 자동 선택
                </template>
                <template v-else>
                  분석 영역: 전체 캔버스 자동 선택
                </template>
              </span>
            </div>
            <div class="oa-toolbar-actions">
              <button v-if="objAnalysisResult && selectedObjId"
                class="oa-view-all-btn" @click="selectedObjId = null">
                전체 보기
              </button>
              <button class="oa-btn" :disabled="objAnalyzing" @click="runObjectAnalysis">
                <span v-if="objAnalyzing" class="oa-btn-spin" />
                {{ objAnalyzing ? '분석 중…' : objAnalysisResult ? '재분석' : 'AI 객체 분석' }}
              </button>
            </div>
          </div>

          <!-- IDLE 상태: PSD 분석 완료 후 객체 분석 전 -->
          <div v-if="!objAnalysisResult && !objAnalyzing && !objAnalysisError" class="oa-idle-state">
            <div class="oa-idle-icon">✦</div>
            <div class="oa-idle-title">AI 객체분석 시작하기</div>
            <div class="oa-idle-desc">PSD의 텍스트, 제품, 로고, 인물, 배경을 분석하고<br>각 레이어에 역할을 자동으로 할당합니다.</div>
          </div>

          <!-- 로딩 -->
          <div v-if="objAnalyzing" class="oa-loading">
            <div class="oa-spinner" />
            <span>PSD 레이어와 AI 객체를 매칭하는 중입니다…</span>
          </div>

          <!-- 에러 -->
          <div v-if="objAnalysisError" class="oa-error">{{ objAnalysisError }}</div>

          <!-- 결과 -->
          <template v-if="objAnalysisResult && !objAnalyzing">
            <!-- 분석 메타 요약 바 -->
            <div class="oa-meta-bar">
              <span class="oa-meta-badge-cached" v-if="objAnalysisMeta?.cacheHit">↩ 저장된 분석 재사용</span>
              <span class="oa-meta-badge-new" v-else>✦ 새 AI 분석</span>
              <span class="oa-meta-sep">·</span>
              <span class="oa-meta-count">객체 {{ objAnalysisResult.objects?.length ?? 0 }}개</span>
              <span v-if="objAnalysisMeta?.model" class="oa-meta-model">{{ objAnalysisMeta.model }}</span>
              <span v-if="objAnalysisResult.id" class="oa-meta-saved" title="Object Map MongoDB에 저장됨">Object Map 저장됨</span>
            </div>
            <!-- compact 상태 바 -->
            <div class="oa-status-bar">
              <span class="oa-rf-badge" :class="objAnalysisResult.reflowReady ? 'rf-ok' : 'rf-ng'">
                {{ objAnalysisResult.reflowReady ? '✓ 재배치 준비 완료' : '✗ 재배치 준비 미완료' }}
              </span>
              <span v-if="!objAnalysisResult.reflowReady && objAnalysisResult.missingRequiredRoles?.length" class="oa-missing">
                누락: {{ objAnalysisResult.missingRequiredRoles.map(objRoleLabel).join(' · ') }}
              </span>
              <span v-if="selectedObjId" class="oa-selected-info">
                · {{ objRoleLabel(visibleObjects.find(o => o.id === selectedObjId)?.role) }} 선택됨
              </span>
            </div>

            <!-- 본문: 프리뷰 (좌) + 인스펙터 (우) -->
            <div class="oa-body">
              <!-- 프리뷰 패널 -->
              <div class="oa-preview-panel">
                <div class="oa-preview-wrap" ref="previewWrapRef">
                  <img
                    v-if="objAnalysisResult.previewBase64"
                    :src="'data:image/jpeg;base64,' + objAnalysisResult.previewBase64"
                    class="oa-preview-img"
                    alt="아트보드 프리뷰"
                    @load="onPreviewImgLoad"
                  />
                  <div
                    v-for="obj in visibleObjects"
                    :key="'bbox-' + obj.id"
                    class="oa-bbox"
                    :class="[
                      'oa-role-' + obj.role,
                      'oa-ms-' + obj.matchStatus,
                      {
                        'oa-bbox-hl': hoveredObjId === obj.id,
                        'oa-bbox-selected': selectedObjId === obj.id,
                        'oa-bbox-dim': selectedObjId && selectedObjId !== obj.id,
                      }
                    ]"
                    :style="bboxOverlayStyle(obj.bbox)"
                    @mouseenter="hoveredObjId = obj.id"
                    @mouseleave="hoveredObjId = null"
                    @click="toggleSelectObj(obj.id)"
                  >
                    <span class="oa-bbox-label">{{ objRoleLabel(obj.role) }}</span>
                  </div>
                </div>

                <!-- 객체 crop 미리보기 (선택된 객체) -->
                <div v-if="selectedObjId && selectedObjCropStyle" class="oa-crop-preview">
                  <div class="oa-crop-header">
                    <span class="oa-crop-title">{{ objRoleLabel(visibleObjects.find(o => o.id === selectedObjId)?.role) }} — 확대 보기</span>
                    <button class="oa-crop-close" @click="selectedObjId = null">×</button>
                  </div>
                  <div class="oa-crop-canvas" :style="selectedObjCropStyle" />
                  <div class="oa-crop-meta">
                    <template v-if="visibleObjects.find(o => o.id === selectedObjId)">
                      {{ visibleObjects.find(o => o.id === selectedObjId)?.bbox?.width ?? 0 }}×{{ visibleObjects.find(o => o.id === selectedObjId)?.bbox?.height ?? 0 }}px
                      · {{ visibleObjects.find(o => o.id === selectedObjId)?.label }}
                    </template>
                  </div>
                </div>
                <div class="oa-preview-hint" v-if="!objAnalysisResult.previewBase64 || !previewImgMeta">
                  객체를 클릭하면 해당 영역을 확대해서 볼 수 있습니다.
                </div>
              </div>

              <!-- 인스펙터 패널 -->
              <div class="oa-inspector">
                <div class="oa-inspector-hint">객체를 클릭하면 이미지에서 해당 영역을 강조해서 볼 수 있습니다.</div>
                <!-- 필터 칩 -->
                <div class="oa-filter-row">
                  <button
                    v-for="f in OBJ_FILTERS"
                    :key="f.value"
                    class="oa-filter-chip"
                    :class="{ active: objFilter === f.value }"
                    @click="objFilter = f.value"
                  >{{ f.label }}<span class="oa-filter-cnt">{{ getFilterCount(f.value) }}</span></button>
                </div>
                <!-- 필터 결과 없음 -->
                <div v-if="filteredVisibleObjects.length === 0 && objFilter !== 'all'" class="oa-filter-empty">
                  해당 카테고리에 객체가 없습니다.
                </div>
                <div
                  v-for="obj in filteredVisibleObjects"
                  :key="'ins-' + obj.id"
                  class="oa-ins-card"
                  :class="{
                    'oa-ins-hl': hoveredObjId === obj.id,
                    'oa-ins-selected': selectedObjId === obj.id,
                  }"
                  @mouseenter="hoveredObjId = obj.id"
                  @mouseleave="hoveredObjId = null"
                  @click="toggleSelectObj(obj.id)"
                >
                  <div class="oa-ins-row">
                    <span class="oa-role-dot" :class="'oa-dot-' + obj.role" />
                    <span class="oa-ins-role">{{ objRoleLabel(obj.role) }}</span>
                    <span class="oa-ins-label">{{ obj.label }}</span>
                    <span class="oa-imp-chip" :class="'imp-' + obj.importance">{{ objImportanceLabel(obj.importance) }}</span>
                  </div>
                  <div class="oa-ins-detail" v-if="obj.bbox">
                    <span class="oa-ins-bbox">{{ obj.bbox.width }}×{{ obj.bbox.height }}</span>
                    <span class="oa-ins-conf" v-if="obj.confidence">{{ Math.round(obj.confidence * 100) }}%</span>
                  </div>
                  <div class="oa-ins-pipeline">
                    <span class="oa-stage oa-stage-ok">AI 감지됨</span>
                    <span class="oa-pipe-arrow">›</span>
                    <template v-if="obj.matchStatus === 'ready'">
                      <span class="oa-stage oa-stage-ok">레이어 매칭 ✓</span>
                    </template>
                    <template v-else-if="obj.matchStatus === 'matched_low_confidence'">
                      <span class="oa-stage oa-stage-warn">레이어 낮은신뢰도</span>
                    </template>
                    <template v-else>
                      <span class="oa-stage oa-stage-fail">레이어 매칭 ✗</span>
                      <span class="oa-pipe-arrow">›</span>
                      <span v-if="obj.bbox" class="oa-stage oa-stage-info">crop 가능</span>
                      <span v-else class="oa-stage oa-stage-muted">crop 불가</span>
                    </template>
                    <span v-if="obj.matchedLayerName" class="oa-ins-layer" :title="obj.matchedLayerName">{{ obj.matchedLayerName }}</span>
                    <span v-if="obj.matchScore" class="oa-ins-score">{{ Math.round(obj.matchScore * 100) }}%</span>
                  </div>
                </div>

                <details v-if="hiddenObjects.length" class="oa-ins-more">
                  <summary class="oa-ins-more-lbl">기타 {{ hiddenObjects.length }}개 ▾</summary>
                  <div v-for="obj in hiddenObjects" :key="'sec-' + obj.id" class="oa-ins-card oa-ins-secondary">
                    <div class="oa-ins-row">
                      <span class="oa-role-dot" :class="'oa-dot-' + obj.role" />
                      <span class="oa-ins-role">{{ objRoleLabel(obj.role) }}</span>
                      <span class="oa-ins-label">{{ obj.label }}</span>
                    </div>
                  </div>
                </details>

                <!-- 기술 정보 접기 -->
                <details v-if="objAnalysisMeta || objAnalysisResult?.id" class="oa-tech-details">
                  <summary class="oa-tech-summary">기술 정보</summary>
                  <div class="oa-tech-body">
                    <div v-if="objAnalysisResult?.id" class="oa-tech-row">
                      <span class="oa-tech-key">분석 ID</span>
                      <span class="oa-tech-val oa-tech-mono oa-tech-truncate" :title="objAnalysisResult.id">{{ objAnalysisResult.id }}</span>
                    </div>
                    <div v-if="objAnalysisMeta?.sourceFileSha256" class="oa-tech-row">
                      <span class="oa-tech-key">소스 SHA-256</span>
                      <span class="oa-tech-val oa-tech-mono oa-tech-truncate" :title="objAnalysisMeta.sourceFileSha256">{{ objAnalysisMeta.sourceFileSha256.slice(0, 16) }}…</span>
                    </div>
                    <div v-if="objAnalysisMeta?.model" class="oa-tech-row">
                      <span class="oa-tech-key">모델</span>
                      <span class="oa-tech-val oa-tech-mono">{{ objAnalysisMeta.model }}</span>
                    </div>
                    <div v-if="objAnalysisMeta?.analysisVersion" class="oa-tech-row">
                      <span class="oa-tech-key">버전</span>
                      <span class="oa-tech-val oa-tech-mono">{{ objAnalysisMeta.analysisVersion }}</span>
                    </div>
                    <div class="oa-tech-row">
                      <span class="oa-tech-key">캐시 히트</span>
                      <span class="oa-tech-val">{{ objAnalysisMeta?.cacheHit ? '예 (저장된 분석 재사용)' : '아니오 (새 AI 분석)' }}</span>
                    </div>
                    <div v-if="objAnalysisMeta?.gptRequestCount !== null" class="oa-tech-row">
                      <span class="oa-tech-key">GPT 호출 횟수</span>
                      <span class="oa-tech-val oa-tech-mono">{{ objAnalysisMeta.gptRequestCount }}</span>
                    </div>
                    <div v-if="objAnalysisMeta?.analyzedAt" class="oa-tech-row">
                      <span class="oa-tech-key">분석 일시</span>
                      <span class="oa-tech-val">{{ new Date(objAnalysisMeta.analyzedAt).toLocaleString('ko-KR') }}</span>
                    </div>
                  </div>
                </details>
              </div>
            </div>
          </template>
        </div>

        <!-- Empty -->
        <div v-if="selectedSpecIds.length === 0" class="empty-hint">
          <div class="empty-icon">☰</div>
          <div class="empty-text">왼쪽 매체 가이드에서 사이즈를 선택하세요</div>
        </div>

        <!-- Platform groups -->
        <template v-else>
          <div v-for="platform in activePlatforms" :key="platform" class="pf-group">
            <div class="pf-group-head">
              <span class="pf-badge" :style="{ background: platformCfg[platform]?.color }">
                {{ (platformCfg[platform]?.label ?? platform)[0] }}
              </span>
              <span class="pf-group-name">{{ platformCfg[platform]?.label ?? platform }}</span>
              <span class="pf-group-cnt">{{ selectedByPlatform(platform).length }}</span>
              <button class="desel-btn" @click="deselectPlatform(platform)">전체 해제</button>
              <div class="view-toggle">
                <button class="vt-btn" :class="{ on: getViewMode(platform) === 'grid' }" @click="setViewMode(platform, 'grid')">⊞</button>
                <button class="vt-btn" :class="{ on: getViewMode(platform) === 'list' }" @click="setViewMode(platform, 'list')">☰</button>
              </div>
            </div>
            <div class="hcards-grid" :class="{ 'hcards-list': getViewMode(platform) === 'list' }">
              <div v-for="spec in selectedByPlatform(platform)" :key="spec.id" class="hcard">
                <div class="hcard-preview">
                  <div class="spec-preview-canvas">
                    <div
                      class="spec-preview-frame"
                      :class="getPreviewType(spec)"
                      :style="{ aspectRatio: `${spec.width} / ${spec.height}` }"
                    >
                      <img v-if="previewUrl" :src="previewUrl" class="spec-preview-img" :alt="spec.placementName" />
                      <div v-else class="spec-preview-ph" :style="{ background: platformCfg[platform]?.tagBg }">
                        <span>{{ spec.width }}×{{ spec.height }}</span>
                      </div>
                      <!-- 세이프존 시각화 오버레이: 클릭 toggle, 이 프레임 내부에만 표시 -->
                      <div
                        v-if="hasParsedSafeZone(spec) && safeZoneVisibleIds.has(spec.id)"
                        class="sz-frame-overlay"
                        :style="szOverlayStyle(spec)"
                      />
                    </div>
                  </div>
                </div>
                <div class="hcard-info">
                  <button class="hcard-x" @click="removeSpec(spec.id)">×</button>
                  <div class="hcard-name">{{ formatSpecLabel(spec) }}</div>
                  <div class="hcard-dim">{{ spec.width }}×{{ spec.height }}px</div>
                  <span class="hcard-tag" :style="tagStyle(platform)">{{ (platformCfg[platform]?.label ?? platform).toUpperCase() }}</span>
                  <div class="hcard-meta">
                    <span class="hcard-ratio">{{ getSimpleRatio(spec.width, spec.height) }} 비율</span>
                    <span class="hcard-orient">{{ getOrientation(spec.width, spec.height) }}</span>
                  </div>
                  <!-- 제작 가이드 요약 -->
                  <div class="hcard-guide">
                    <div v-if="spec.fileFormats?.length || spec.maxFileSizeKb" class="hcard-guide-row">
                      <span v-for="f in (spec.fileFormats ?? [])" :key="f" class="hcard-fmt-chip">{{ f.toUpperCase() }}</span>
                      <span v-if="spec.maxFileSizeKb" class="hcard-guide-size">{{ formatFileSize(spec.maxFileSizeKb) }} 이하</span>
                    </div>
                    <div v-if="hasParsedSafeZone(spec)" class="hcard-guide-row hcard-sz-row">
                      <button
                        class="hcard-sz-label"
                        :class="{ 'hcard-sz-active': safeZoneVisibleIds.has(spec.id) }"
                        @click.stop="toggleSafeZone(spec.id)"
                        title="클릭하여 세이프존 미리보기"
                      >세이프존 {{ safeZoneVisibleIds.has(spec.id) ? '▲' : '▼' }}</button>
                      <span v-if="spec.safeTop != null"    class="hcard-sz-chip">상 {{ spec.safeTop }}px</span>
                      <span v-if="spec.safeRight != null"  class="hcard-sz-chip">우 {{ spec.safeRight }}px</span>
                      <span v-if="spec.safeBottom != null" class="hcard-sz-chip">하 {{ spec.safeBottom }}px</span>
                      <span v-if="spec.safeLeft != null"   class="hcard-sz-chip">좌 {{ spec.safeLeft }}px</span>
                    </div>
                    <div v-if="spec.notes" class="hcard-guide-row">
                      <span class="hcard-notes-text">{{ spec.notes }}</span>
                    </div>
                  </div>
                  <!-- 상세 가이드 (접기) -->
                  <details class="hcard-details">
                    <summary class="hcard-details-lbl">상세 가이드</summary>
                    <div class="hcard-details-body">
                      <template v-if="hasDetailContent(spec)">
                        <div v-if="spec.headlineMaxChars" class="hcard-dk-row">
                          <span class="hcard-dk">헤드라인</span><span class="hcard-dv">최대 {{ spec.headlineMaxChars }}자</span>
                        </div>
                        <div v-if="spec.descriptionMaxChars" class="hcard-dk-row">
                          <span class="hcard-dk">설명문</span><span class="hcard-dv">최대 {{ spec.descriptionMaxChars }}자</span>
                        </div>
                        <div v-if="spec.safeZoneParseStatus === 'diagram_unreadable'" class="hcard-dk-row">
                          <span class="hcard-dk">세이프존</span><span class="hcard-dv hcard-dv-muted">공식 가이드 도식 확인 필요</span>
                        </div>
                        <div v-if="spec.bgTransparent === false" class="hcard-dk-row">
                          <span class="hcard-dk">배경</span><span class="hcard-dv">투명 배경 불가</span>
                        </div>
                        <div v-if="spec.lastVerified" class="hcard-dk-row">
                          <span class="hcard-dk">확인일</span><span class="hcard-dv">{{ spec.lastVerified }}</span>
                        </div>
                        <a v-if="spec.sourceUrl" :href="spec.sourceUrl" target="_blank" rel="noopener noreferrer" class="hcard-source-link">공식 가이드 보기 →</a>
                      </template>
                      <div v-else class="hcard-no-detail">등록된 상세 제작 가이드가 없습니다.</div>
                    </div>
                  </details>
                </div>
              </div>
            </div>
          </div>
        </template>

      </div>

      <!-- AI bar (sticky bottom) -->
      <div class="ai-bar">
        <div class="ai-bar-left">
          <div class="ai-bar-ico">✦</div>
          <div>
            <div class="ai-bar-title">AI가 자동으로 최적화합니다</div>
            <div class="ai-bar-desc">선택한 모든 사이즈에 대해 요소 정렬, 텍스트 가독성, 안전 영역을 자동으로 최적화합니다.</div>
          </div>
        </div>
        <button class="ai-bar-btn">⚙ 자동 최적화 설정 ∨</button>
      </div>

      <!-- Toast -->
      <transition name="toast">
        <div v-if="result" class="toast">
          <span class="toast-chk">✓</span>
          <span>작업 접수 완료</span>
          <button class="toast-link" @click="$router.push('/jobs')">작업 목록 →</button>
        </div>
      </transition>
    </div>

  </div>
</template>

<script setup>
import { ref, reactive, onMounted, computed, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { readPsd } from 'ag-psd'
import 'ag-psd/initialize-canvas'
import { uploadPsd, listBannerSpecs, analyzeBanner, analyzePsdLayers, analyzePsdObjects } from '../api/banner.js'
import { useRouter } from 'vue-router'
const router = useRouter()

const loading      = ref(false)
const result       = ref(null)
const aiAnalyzing  = ref(false)
const aiAnalysis   = ref(null)
const aiApplied    = ref(false)
const psdLayerAnalyzing = ref(false)
const psdLayerAnalysis  = ref(null)
const psdCanvas         = ref(null)
const psdNativeW        = ref(0)
const psdNativeH        = ref(0)
const detectedArtboards = ref([])
const selectedArtboardIds = ref([])
const objAnalyzing        = ref(false)
const objAnalysisResult   = ref(null)
const objAnalysisError    = ref(null)
const objAnalysisArtboardId = ref(null)
const objAnalysisId       = ref(null)    // 저장된 PsdObjectAnalysis MongoDB ID (submit 시 전송)
const previewWrapRef      = ref(null)
const previewImgMeta      = ref(null)
const hoveredObjId        = ref(null)
const selectedObjId       = ref(null)
const objFilter           = ref('all')
const objAnalysisMeta     = ref(null)
const allSpecs     = ref([])
const specsLoading = ref(true)
const naverLoadError = ref(false)
const selectedSpecIds      = ref([])
const safeZoneVisibleIds   = ref(new Set())
const dragover     = ref(false)
const materialType = ref('')
const psdMode      = ref('artboard-first')

const isPsdFile = computed(() =>
  form.psdFile?.name?.toLowerCase().endsWith('.psd') ?? false
)

const uploadOpen = ref(true)
const mediaOpen  = ref(true)
const advOpen    = ref(false)
const expandedPlatforms = reactive({})
const viewModes = reactive({})

const previewUrl     = ref(null)
const previewLoading = ref(false)
const previewError   = ref(false)
const previewSize    = ref('')

const form = reactive({
  psdFile: null, advertiser: '', campaignName: '',
  resizeMode: 'smart-fit', smartFitStrength: 'balanced', focalPosition: 'center', outputFormat: 'png',
})

const platformOrder = ['google', 'naver', 'meta', 'criteo', 'mobion', 'kakao', 'linkedin', 'tiktok', 'line']

const platformCfg = {
  google:   { label: 'Google',   region: 'global', color: '#4285F4', tagBg: '#EAF1FE' },
  meta:     { label: 'Meta',     region: 'global', color: '#1877F2', tagBg: '#E8F0FD' },
  naver:    { label: 'Naver',    region: 'korea',  color: '#03C75A', tagBg: '#E6F9EE' },
  kakao:    { label: 'Kakao',    region: 'korea',  color: '#FACC15', tagBg: '#FEF9E7' },
  criteo:   { label: 'Criteo',   region: 'global', color: '#FF4B33', tagBg: '#FFF0EE' },
  mobion:   { label: 'Mobion',   region: 'korea',  color: '#0063CC', tagBg: '#E8F0FD' },
  linkedin: { label: 'LinkedIn', region: 'global', color: '#0A66C2', tagBg: '#E8F0FA' },
  tiktok:   { label: 'TikTok',  region: 'global', color: '#191F28', tagBg: '#F2F4F6' },
  line:     { label: 'LINE',     region: 'japan',  color: '#06C755', tagBg: '#E6F9EE' },
}

const resizeOptions = [
  { value: 'smart-fit', label: '스마트 맞춤' },
  { value: 'cover',     label: '꽉 채우기' },
  { value: 'contain',   label: '전체 보이기' },
  { value: 'blur-bg',   label: '블러 배경' },
]
const smartFitOptions = [
  { value: 'safe',     label: '안전' },
  { value: 'balanced', label: '균형' },
  { value: 'fill',     label: '채움' },
]
const materialTypeOptions = [
  { value: 'text',    label: '텍스트형', strength: 'safe',     strengthLabel: '안전', hint: '텍스트 영역 보호에 최적' },
  { value: 'general', label: '일반형',   strength: 'balanced', strengthLabel: '균형', hint: '자연스러운 배치 추천' },
  { value: 'product', label: '제품형',   strength: 'fill',     strengthLabel: '채움', hint: '제품 최대 노출에 최적' },
]
const focalPositionOptions = [
  { value: 'left-top',     label: '좌상단' },
  { value: 'top',          label: '상단' },
  { value: 'right-top',    label: '우상단' },
  { value: 'left',         label: '좌측' },
  { value: 'center',       label: '중앙' },
  { value: 'right',        label: '우측' },
  { value: 'left-bottom',  label: '좌하단' },
  { value: 'bottom',       label: '하단' },
  { value: 'right-bottom', label: '우하단' },
]
const formatOptions = [
  { value: 'png', label: 'PNG' }, { value: 'jpg', label: 'JPG' }, { value: 'webp', label: 'WebP' },
]

const aiFeatures = [
  { id: 1, icon: '⊞', title: 'AI 자동 정렬',      desc: '주요 요소를 자동으로 배치' },
  { id: 2, icon: '⊙', title: '안전 영역 최적화',   desc: '플랫폼별 안전 영역 보장' },
  { id: 3, icon: 'T', title: '텍스트 보정',        desc: '가독성 높은 텍스트 추천' },
  { id: 4, icon: '✦', title: '품질 향상',          desc: '선명도 및 색감 최적화' },
]

const ALLOWED_EXTS = ['psd', 'png', 'jpg', 'jpeg', 'webp', 'gif', 'tiff', 'bmp']
const fileExt = computed(() => form.psdFile?.name.split('.').pop().toUpperCase() ?? '')

const currentMaterialHint = computed(() => {
  const opt = materialTypeOptions.find(o => o.value === materialType.value)
  return opt ? `AI 추천: ${opt.strengthLabel} — ${opt.hint}` : null
})

function selectMaterialType(type) {
  materialType.value = type
  const opt = materialTypeOptions.find(o => o.value === type)
  if (opt) form.smartFitStrength = opt.strength
}

function selectStrength(value) {
  form.smartFitStrength = value
}

watch(() => form.resizeMode, () => { materialType.value = '' })

const groupedSpecs = computed(() => {
  const map = {}
  for (const spec of allSpecs.value) {
    if (!map[spec.media]) map[spec.media] = []
    map[spec.media].push(spec)
  }
  return map
})

const activePlatforms = computed(() =>
  platformOrder.filter(p => selectedByPlatform(p).length > 0)
)

function selectedByPlatform(p) {
  const ids = new Set(selectedSpecIds.value)
  return (groupedSpecs.value[p] ?? []).filter(s => ids.has(s.id))
}
function platformSelectedCount(p) { return selectedByPlatform(p).length }
function isPlatformAllSelected(p) {
  const sp = groupedSpecs.value[p] ?? []
  return sp.length > 0 && sp.every(s => selectedSpecIds.value.includes(s.id))
}
function isPlatformPartial(p) {
  const sp = groupedSpecs.value[p] ?? []
  const n = sp.filter(s => selectedSpecIds.value.includes(s.id)).length
  return n > 0 && n < sp.length
}
function togglePlatform(p) {
  const ids = (groupedSpecs.value[p] ?? []).map(s => s.id)
  if (isPlatformAllSelected(p)) selectedSpecIds.value = selectedSpecIds.value.filter(id => !ids.includes(id))
  else ids.forEach(id => { if (!selectedSpecIds.value.includes(id)) selectedSpecIds.value.push(id) })
}
function deselectPlatform(p) {
  const ids = (groupedSpecs.value[p] ?? []).map(s => s.id)
  selectedSpecIds.value = selectedSpecIds.value.filter(id => !ids.includes(id))
}
function toggleExpand(p) { expandedPlatforms[p] = !expandedPlatforms[p] }
function getViewMode(p) { return viewModes[p] ?? 'grid' }
function setViewMode(p, mode) { viewModes[p] = mode }
function toggleSpec(id) {
  const i = selectedSpecIds.value.indexOf(id)
  if (i >= 0) selectedSpecIds.value.splice(i, 1)
  else selectedSpecIds.value.push(id)
}
function removeSpec(id) {
  selectedSpecIds.value = selectedSpecIds.value.filter(x => x !== id)
  const s = new Set(safeZoneVisibleIds.value)
  s.delete(id)
  safeZoneVisibleIds.value = s
}

function toggleSafeZone(specId) {
  const s = new Set(safeZoneVisibleIds.value)
  if (s.has(specId)) s.delete(specId)
  else s.add(specId)
  safeZoneVisibleIds.value = s
}

function tagStyle(p) {
  const cfg = platformCfg[p] ?? {}
  return { background: cfg.tagBg ?? '#F2F4F6', color: cfg.color ?? '#6B7684' }
}

function getOrientation(w, h) {
  const r = w / h
  if (r >= 0.9 && r <= 1.1) return '정사각형'
  return r > 1 ? '가로형' : '세로형'
}

function formatSpecLabel(spec) {
  const size = `${spec.width}×${spec.height}`
  const name = spec.placementName || ''
  return name ? `${size} (${name})` : size
}

function getPreviewType(spec) {
  const ratio = spec.width / spec.height
  if (ratio >= 5)    return 'ultra-wide'
  if (ratio >= 1.6)  return 'wide'
  if (ratio <= 0.45) return 'tall'
  if (ratio <= 0.85) return 'vertical'
  return 'square'
}

const RESIZE_LABELS = { 'smart-fit': '스마트 맞춤', cover: '꽉 채우기', contain: '전체 보이기', 'blur-bg': '블러 배경' }
const STRENGTH_LABELS = { safe: '안전', balanced: '균형', fill: '채움' }
const POS_LABELS = {
  center: '중앙', top: '상단', bottom: '하단', left: '좌측', right: '우측',
  'left-top': '좌상단', 'right-top': '우상단', 'left-bottom': '좌하단', 'right-bottom': '우하단',
}
function resizeLabel(v) { return RESIZE_LABELS[v] ?? v }
function strengthLabel(v) { return STRENGTH_LABELS[v] ?? v }
function posLabel(v) { return POS_LABELS[v] ?? v }

const CREATIVE_TYPE_LABELS = { text_heavy: '텍스트형', product_focused: '제품형', balanced_mix: '균형형' }
const DENSITY_LABELS = { high: '높음', medium: '보통', low: '낮음' }
const RISK_LABELS = { high: '높음 ⚠', medium: '보통', low: '낮음' }
function creativeTypeLabel(v) { return CREATIVE_TYPE_LABELS[v] ?? v }
function densityLabel(v) { return DENSITY_LABELS[v] ?? v }
function riskLabel(v) { return RISK_LABELS[v] ?? v }

const GROUP_NAME_MAP = {
  main_product: '메인 제품', main_copy: '메인 카피', sub_copy: '서브 카피',
  price_discount: '가격/할인', cta: 'CTA', logo: '로고', decorations: '장식', background: '배경',
}
function getGroupName(gid, analysis) {
  if (analysis?.elementGroups) {
    const g = analysis.elementGroups.find(eg => eg.id === gid)
    if (g?.name) return g.name
  }
  return GROUP_NAME_MAP[gid] ?? gid
}

function getSimpleRatio(w, h) {
  const known = [[16,9],[4,3],[1,1],[9,16],[3,4],[2,1],[3,2],[21,9],[1,2],[1,3],[3,1]]
  const ratio = w / h
  let best = known[0], minDiff = Infinity
  for (const [rw, rh] of known) {
    const d = Math.abs(ratio - rw/rh)
    if (d < minDiff) { minDiff = d; best = [rw, rh] }
  }
  return `${best[0]}:${best[1]}`
}

function hasDetailContent(spec) {
  return !!(
    spec.headlineMaxChars ||
    spec.descriptionMaxChars ||
    spec.safeZoneParseStatus === 'diagram_unreadable' ||
    spec.bgTransparent === false ||
    spec.lastVerified ||
    spec.sourceUrl
  )
}

function formatFileSize(kb) {
  if (!kb) return null
  return kb >= 1000 ? `${(kb / 1000).toFixed(1)}MB` : `${kb}KB`
}

function hasParsedSafeZone(spec) {
  return spec?.safeZoneParseStatus === 'parsed_text' && spec.safeTop != null
}

function szOverlayStyle(spec) {
  if (!hasParsedSafeZone(spec)) return {}
  return {
    top:    ((spec.safeTop    / spec.height) * 100).toFixed(2) + '%',
    right:  ((spec.safeRight  / spec.width)  * 100).toFixed(2) + '%',
    bottom: ((spec.safeBottom / spec.height) * 100).toFixed(2) + '%',
    left:   ((spec.safeLeft   / spec.width)  * 100).toFixed(2) + '%',
  }
}

function clearFile() {
  form.psdFile = null
  previewUrl.value = null; previewError.value = false; previewSize.value = ''
  aiAnalysis.value = null; aiApplied.value = false
  psdLayerAnalysis.value = null; psdLayerAnalyzing.value = false
  psdCanvas.value = null; psdNativeW.value = 0; psdNativeH.value = 0
  detectedArtboards.value = []; selectedArtboardIds.value = []
  psdMode.value = 'artboard-first'
  objAnalyzing.value = false; objAnalysisResult.value = null; objAnalysisError.value = null
  objAnalysisArtboardId.value = null; previewImgMeta.value = null
  objAnalysisId.value = null
  hoveredObjId.value = null; selectedObjId.value = null
  objFilter.value = 'all'; objAnalysisMeta.value = null
}

async function runAiAnalyze() {
  if (!previewUrl.value) return
  aiAnalyzing.value = true
  aiAnalysis.value = null
  try {
    const blob = await fetch(previewUrl.value).then(r => r.blob())
    const fd = new FormData()
    fd.append('file', blob, 'preview.png')
    const { data } = await analyzeBanner(fd)
    aiAnalysis.value = data
    advOpen.value = true
  } catch (e) {
    ElMessage.error('AI 분석 실패: ' + (e.response?.data?.message ?? e.message))
  } finally {
    aiAnalyzing.value = false
  }
}

watch(detectedArtboards, (v) => {
  if (v.length > 0 && !objAnalysisArtboardId.value) {
    objAnalysisArtboardId.value = v[0].id
  }
})
watch(objAnalysisResult, () => {
  previewImgMeta.value = null
  selectedObjId.value = null
})

const objReflowCanActivate = computed(() => {
  if (!objAnalysisResult.value) return false
  const roles = new Set((objAnalysisResult.value.objects || []).map(o => o.role))
  return roles.has('title') && roles.has('main_image')
})

const IMPORTANCE_ORDER = { required: 0, priority: 1, optional: 2 }
const HIDDEN_ROLES = new Set(['decoration', 'unknown'])
const visibleObjects = computed(() => {
  const objs = objAnalysisResult.value?.objects || []
  return [...objs]
    .filter(o => !HIDDEN_ROLES.has(o.role))
    .sort((a, b) => (IMPORTANCE_ORDER[a.importance] ?? 2) - (IMPORTANCE_ORDER[b.importance] ?? 2))
})
const hiddenObjects = computed(() => {
  const objs = objAnalysisResult.value?.objects || []
  return objs.filter(o => HIDDEN_ROLES.has(o.role))
})

const OBJ_FILTERS = [
  { value: 'all',        label: '전체' },
  { value: 'image',      label: '제품·이미지' },
  { value: 'text',       label: '텍스트' },
  { value: 'logo_cta',   label: '로고·CTA' },
  { value: 'person',     label: '인물' },
  { value: 'bg_deco',    label: '배경·장식' },
  { value: 'unmatched',  label: '매칭실패' },
]

const FILTER_ROLE_MAP = {
  image:     ['main_image'],
  text:      ['title', 'body_text'],
  logo_cta:  ['logo', 'cta', 'badge'],
  person:    ['person'],
  bg_deco:   ['background', 'decoration'],
  unmatched: null,
}

const filteredVisibleObjects = computed(() => {
  const objs = visibleObjects.value
  if (objFilter.value === 'all') return objs
  if (objFilter.value === 'unmatched') return objs.filter(o => o.matchStatus !== 'ready')
  const roles = FILTER_ROLE_MAP[objFilter.value]
  if (!roles) return objs
  return objs.filter(o => roles.includes(o.role))
})

function getFilterCount(filterVal) {
  const objs = visibleObjects.value
  if (filterVal === 'all') return objs.length
  if (filterVal === 'unmatched') return objs.filter(o => o.matchStatus !== 'ready').length
  const roles = FILTER_ROLE_MAP[filterVal]
  if (!roles) return 0
  return objs.filter(o => roles.includes(o.role)).length
}

watch(objFilter, () => {
  if (selectedObjId.value && !filteredVisibleObjects.value.find(o => o.id === selectedObjId.value)) {
    selectedObjId.value = null
  }
})

function toggleSelectObj(id) {
  selectedObjId.value = selectedObjId.value === id ? null : id
}

function normalizeBbox(raw) {
  if (!raw) return null
  if (raw.width !== undefined && raw.x !== undefined) return raw
  if (raw.left !== undefined) {
    return { x: raw.left, y: raw.top, width: raw.right - raw.left, height: raw.bottom - raw.top }
  }
  if (raw.w !== undefined) return { x: raw.x ?? 0, y: raw.y ?? 0, width: raw.w, height: raw.h }
  if (raw.x1 !== undefined) {
    return { x: raw.x1, y: raw.y1, width: raw.x2 - raw.x1, height: raw.y2 - raw.y1 }
  }
  return raw
}

function onPreviewImgLoad(e) {
  const img = e.target
  previewImgMeta.value = {
    natW: img.naturalWidth,
    natH: img.naturalHeight,
    w: img.offsetWidth,
    h: img.offsetHeight,
  }
}

function bboxOverlayStyle(rawBbox) {
  const bbox = normalizeBbox(rawBbox)
  const m = previewImgMeta.value
  if (!m || !bbox || !m.natW || !m.natH || !m.w || !m.h) return { display: 'none' }
  const scale = Math.min(m.w / m.natW, m.h / m.natH)
  const rw = m.natW * scale
  const rh = m.natH * scale
  const ox = (m.w - rw) / 2
  const oy = (m.h - rh) / 2
  return {
    left:   (ox + (bbox.x / m.natW) * rw).toFixed(1) + 'px',
    top:    (oy + (bbox.y / m.natH) * rh).toFixed(1) + 'px',
    width:  ((bbox.width  / m.natW) * rw).toFixed(1) + 'px',
    height: ((bbox.height / m.natH) * rh).toFixed(1) + 'px',
  }
}

const selectedObjCropStyle = computed(() => {
  if (!selectedObjId.value || !objAnalysisResult.value?.previewBase64 || !previewImgMeta.value) return null
  const obj = visibleObjects.value.find(o => o.id === selectedObjId.value)
  if (!obj) return null
  const bbox = normalizeBbox(obj.bbox)
  if (!bbox) return null
  const m = previewImgMeta.value
  const CANVAS_W = 260
  const CANVAS_H = 180
  const scaleToFit = Math.min(CANVAS_W / bbox.width, CANVAS_H / bbox.height) * 0.85
  const bgW = m.natW * scaleToFit
  const bgH = m.natH * scaleToFit
  const bx = -bbox.x * scaleToFit + (CANVAS_W - bbox.width * scaleToFit) / 2
  const by = -bbox.y * scaleToFit + (CANVAS_H - bbox.height * scaleToFit) / 2
  return {
    width: CANVAS_W + 'px',
    height: CANVAS_H + 'px',
    backgroundImage: `url('data:image/jpeg;base64,${objAnalysisResult.value.previewBase64}')`,
    backgroundSize: `${bgW.toFixed(0)}px ${bgH.toFixed(0)}px`,
    backgroundPosition: `${bx.toFixed(0)}px ${by.toFixed(0)}px`,
    backgroundRepeat: 'no-repeat',
    backgroundColor: '#111',
    borderRadius: '6px',
    outline: '2px solid rgba(124,58,237,0.4)',
  }
})

async function runObjectAnalysis() {
  if (!form.psdFile || !psdLayerAnalysis.value) return
  const ab = detectedArtboards.value.find(a => a.id === objAnalysisArtboardId.value)
    || detectedArtboards.value[0]

  objAnalyzing.value = true
  objAnalysisResult.value = null
  objAnalysisError.value = null
  try {
    const fd = new FormData()
    fd.append('psdFile', form.psdFile)
    if (ab) {
      fd.append('selectedArtboardId', String(ab.id))
      fd.append('artboardX', String(ab.x ?? 0))
      fd.append('artboardY', String(ab.y ?? 0))
      fd.append('artboardWidth', String(ab.width))
      fd.append('artboardHeight', String(ab.height))
    } else {
      fd.append('artboardX', '0')
      fd.append('artboardY', '0')
      fd.append('artboardWidth', String(psdNativeW.value || 1200))
      fd.append('artboardHeight', String(psdNativeH.value || 628))
    }
    const { data } = await analyzePsdObjects(fd)
    objAnalysisResult.value = data
    objAnalysisId.value = data?.id ?? null
    objAnalysisMeta.value = {
      cacheHit: data?.analysisCacheHit ?? false,
      model: data?.model ?? null,
      analysisVersion: data?.analysisVersion ?? null,
      gptRequestCount: data?.gptRequestCount ?? null,
      sourceFileSha256: data?.sourceFileSha256 ?? null,
      analyzedAt: data?.analyzedAt ?? null,
    }
    objFilter.value = 'all'
    if (objReflowCanActivate.value) {
      psdMode.value = 'object-reflow'
    }
  } catch (e) {
    objAnalysisError.value = e.response?.data?.message || 'AI 객체 분석 중 오류가 발생했습니다.'
  } finally {
    objAnalyzing.value = false
  }
}


const OBJ_ROLE_LABELS = {
  background: '배경', title: '타이틀', body_text: '본문', main_image: '주요 이미지',
  cta: 'CTA', logo: '로고', badge: '배지', decoration: '장식', unknown: '기타',
}
function objRoleLabel(role) { return OBJ_ROLE_LABELS[role] ?? (role || '') }

const OBJ_IMPORTANCE_LABELS = { required: '필수', priority: '우선', optional: '선택' }
function objImportanceLabel(imp) { return OBJ_IMPORTANCE_LABELS[imp] ?? (imp || '') }

function applyAiAnalysis() {
  if (!aiAnalysis.value) return
  form.resizeMode = aiAnalysis.value.resizeMode ?? form.resizeMode
  form.smartFitStrength = aiAnalysis.value.smartFitStrength ?? form.smartFitStrength
  form.focalPosition = aiAnalysis.value.focalPosition ?? form.focalPosition
  materialType.value = ''
  aiApplied.value = true
  ElMessage.success('AI 추천 설정이 적용되었습니다.')
}

async function loadPreview(file) {
  previewLoading.value = true; previewError.value = false; previewUrl.value = null; previewSize.value = ''
  const ext = file.name.split('.').pop().toLowerCase()
  try {
    if (ext === 'psd') {
      const buf = await file.arrayBuffer()
      const psd = readPsd(buf, { skipLayerImageData: true })
      previewSize.value = `${psd.width}×${psd.height}px`
      if (psd.canvas) {
        previewUrl.value = psd.canvas.toDataURL('image/png')
        psdCanvas.value = psd.canvas
        psdNativeW.value = psd.width
        psdNativeH.value = psd.height
        tryGenerateArtboardThumbnails()
      } else {
        previewError.value = true
      }
    } else {
      const url = URL.createObjectURL(file)
      previewUrl.value = url
      const img = new window.Image()
      img.onload = () => { previewSize.value = `${img.naturalWidth}×${img.naturalHeight}px` }
      img.src = url
    }
  } catch { previewError.value = true }
  finally { previewLoading.value = false }
}

async function runPsdLayerAnalyze(file) {
  if (!file?.name?.toLowerCase().endsWith('.psd')) return
  psdLayerAnalyzing.value = true
  psdLayerAnalysis.value = null
  try {
    const fd = new FormData()
    fd.append('psdFile', file)
    const { data } = await analyzePsdLayers(fd)
    psdLayerAnalysis.value = data
    if (data.layerReadable === false || data.layerReflowAvailable === false) {
      if (psdMode.value === 'layer-reflow') psdMode.value = 'artboard-first'
    }
    tryGenerateArtboardThumbnails()
  } catch (e) {
    psdLayerAnalysis.value = { layerReadable: null, layerReflowAvailable: null }
  } finally {
    psdLayerAnalyzing.value = false
  }
}

function tryGenerateArtboardThumbnails() {
  const canvas = psdCanvas.value
  const artboards = psdLayerAnalysis.value?.artboards
  if (!canvas || !artboards?.length) return

  const MAX_THUMB = 240
  const result = artboards
    .filter(ab => ab.artboardType !== 'full-canvas')
    .map(ab => {
      let thumbnail = null
      try {
        const scale = Math.min(MAX_THUMB / ab.width, MAX_THUMB / ab.height)
        const tw = Math.max(1, Math.round(ab.width * scale))
        const th = Math.max(1, Math.round(ab.height * scale))
        const offscreen = document.createElement('canvas')
        offscreen.width = tw
        offscreen.height = th
        const ctx = offscreen.getContext('2d')
        ctx.drawImage(canvas, ab.x, ab.y, ab.width, ab.height, 0, 0, tw, th)
        thumbnail = offscreen.toDataURL('image/jpeg', 0.82)
      } catch (_) {}
      return { ...ab, thumbnail }
    })

  detectedArtboards.value = result
  selectedArtboardIds.value = result.map(ab => ab.id)
}

const ARTBOARD_TYPE_LABELS_KO = {
  square: '정방형', vertical: '세로형', horizontal: '가로형',
  custom: '커스텀', 'full-canvas': '전체 캔버스', unknown: '미분류',
}
function artboardTypeLabel(t) { return ARTBOARD_TYPE_LABELS_KO[t] ?? (t || '') }

const SOURCE_LABELS = {
  artboard_tag: '아트보드', group_name: '그룹명', layer_bbox: '영역 추정', fallback: '전체 캔버스',
}
function sourceLabel(s) { return SOURCE_LABELS[s] ?? (s || '') }

function toggleArtboard(id) {
  const idx = selectedArtboardIds.value.indexOf(id)
  if (idx >= 0) selectedArtboardIds.value.splice(idx, 1)
  else selectedArtboardIds.value.push(id)
}
function toggleAllArtboards() {
  if (selectedArtboardIds.value.length === detectedArtboards.value.length) {
    selectedArtboardIds.value = []
  } else {
    selectedArtboardIds.value = detectedArtboards.value.map(ab => ab.id)
  }
}

const missingRatioWarning = computed(() => {
  if (!detectedArtboards.value.length) return ''
  const TYPE_LABELS = { square: '정방형', vertical: '세로형', horizontal: '가로형' }
  const selected = detectedArtboards.value.filter(ab => selectedArtboardIds.value.includes(ab.id))
  const detectedSet = new Set(selected.map(ab => ab.artboardType).filter(t => TYPE_LABELS[t]))
  const missing = Object.keys(TYPE_LABELS).filter(t => !detectedSet.has(t))
  if (!missing.length) return ''
  const detectedLabels = [...detectedSet].map(t => TYPE_LABELS[t])
  const missingLabels = missing.map(t => TYPE_LABELS[t])
  if (!detectedLabels.length) return `감지된 비율이 없습니다. 전체 PSD로 처리됩니다.`
  return `${detectedLabels.join('/')}만 감지되었습니다. ${missingLabels.join('/')} 결과는 자동 리사이징되며 품질이 낮을 수 있습니다.`
})

function onInputChange(e) {
  const f = e.target.files?.[0]
  if (f) { form.psdFile = f; loadPreview(f); runPsdLayerAnalyze(f) }
}
function onDrop(e) {
  dragover.value = false
  const f = e.dataTransfer.files?.[0]
  if (!f) return
  const ext = f.name.split('.').pop().toLowerCase()
  if (ALLOWED_EXTS.includes(ext)) { form.psdFile = f; loadPreview(f); runPsdLayerAnalyze(f) }
  else ElMessage.warning('지원하지 않는 파일 형식입니다. (PSD, PNG, JPG, WebP, GIF 등)')
}

async function submit() {
  if (!form.psdFile)              return ElMessage.warning('PSD 파일을 선택해주세요.')
  if (!form.advertiser)           return ElMessage.warning('광고주명을 입력해주세요.')
  if (!form.campaignName)         return ElMessage.warning('캠페인명을 입력해주세요.')
  if (!selectedSpecIds.value.length) return ElMessage.warning('사이즈를 1개 이상 선택해주세요.')

  const fd = new FormData()
  fd.append('psdFile', form.psdFile)
  fd.append('advertiser', form.advertiser)
  fd.append('campaignName', form.campaignName)
  selectedSpecIds.value.forEach(id => fd.append('specIds', id))
  fd.append('resizeMode', 'ai-auto')
  fd.append('smartFitStrength', 'balanced')
  fd.append('focalPosition', 'center')
  fd.append('outputFormat', form.outputFormat)
  if (isPsdFile.value) {
    fd.append('psdMode', 'artboard-first')
  }
  if (objAnalysisId.value) {
    fd.append('objectAnalysisId', objAnalysisId.value)
  }

  loading.value = true
  try {
    const { data } = await uploadPsd(fd)
    router.push(`/job/${data.id}`)
  } catch (e) {
    ElMessage.error('업로드 실패: ' + (e.response?.data?.message ?? e.message))
  } finally { loading.value = false }
}

async function loadSpecs() {
  specsLoading.value = true
  naverLoadError.value = false
  try {
    const { data } = await listBannerSpecs()
    allSpecs.value = data
    for (const s of data) { if (!(s.media in expandedPlatforms)) expandedPlatforms[s.media] = false }
    // API에 존재하지 않는 레거시 ID 자동 제거
    const availableIds = new Set(data.map(s => s.id))
    selectedSpecIds.value = selectedSpecIds.value.filter(id => availableIds.has(id))
  } catch {
    naverLoadError.value = true
    ElMessage.error('매체 가이드 로딩 실패')
  }
  finally { specsLoading.value = false }
}

onMounted(loadSpecs)
</script>

<style scoped>
/* ===== layout ===== */
.two-panel { display: flex; height: calc(100vh - 56px); overflow: hidden; }

/* ===== sidebar ===== */
.sidebar {
  width: 300px; flex-shrink: 0; background: #fff;
  border-right: 1px solid #EAEDF0; display: flex; flex-direction: column; overflow: hidden;
}
.sidebar-scroll { flex: 1; overflow-y: auto; }
.sidebar-scroll::-webkit-scrollbar { width: 3px; }
.sidebar-scroll::-webkit-scrollbar-thumb { background: #E5E8EB; border-radius: 2px; }

.sec { border-bottom: 1px solid #F2F4F6; }
.sec-head {
  display: flex; justify-content: space-between; align-items: center;
  padding: 12px 16px; cursor: pointer; user-select: none;
}
.sec-head:hover { background: #FAFBFC; }
.sec-title { font-size: 12.5px; font-weight: 600; color: #333D4B; }
.sec-hint  { font-weight: 400; color: #B0B8C1; }
.info-badge { color: #B0B8C1; font-size: 11px; }
.chevron { font-size: 15px; color: #C4CAD4; transform: rotate(90deg); display: inline-block; transition: transform 0.18s; }
.chevron.up { transform: rotate(-90deg); }
.sec-body { padding: 0 14px 14px; }

/* drop zone */
.drop-zone {
  border: 1.5px dashed #DDE0E7; border-radius: 10px;
  background: #FAFBFF; padding: 22px 16px; text-align: center;
  cursor: pointer; transition: all 0.12s; margin-bottom: 12px;
}
.drop-zone:hover, .drop-zone.dragover { border-color: #7C3AED; background: #F5F0FF; }
.drop-ico-wrap {
  width: 44px; height: 44px; border-radius: 50%;
  background: linear-gradient(135deg, rgba(124,58,237,0.12), rgba(59,130,246,0.08));
  display: flex; align-items: center; justify-content: center; margin: 0 auto 8px;
}
.drop-svg { display: block; }
.drop-label { font-size: 13px; font-weight: 600; color: #4E5968; }
.drop-hint  { font-size: 11px; color: #B0B8C1; margin-top: 3px; }

/* preview */
.preview-block { margin-bottom: 12px; }
.preview-img-wrap {
  width: 100%; border-radius: 10px; overflow: hidden;
  background: #F2F4F6; margin-bottom: 10px; min-height: 100px;
  display: flex; align-items: center; justify-content: center;
}
.preview-img { width: 100%; display: block; border-radius: 10px; object-fit: contain; }
.preview-skeleton { display: flex; flex-direction: column; align-items: center; gap: 8px; padding: 24px; color: #8B95A1; font-size: 12px; }
.preview-spin { width: 18px; height: 18px; border: 2px solid #E5E8EB; border-top-color: #7C3AED; border-radius: 50%; animation: spin 0.7s linear infinite; display: block; }
.preview-fallback { display: flex; flex-direction: column; align-items: center; gap: 6px; padding: 24px; }
.fallback-badge { background: #4285F4; color: #fff; font-size: 12px; font-weight: 700; padding: 4px 8px; border-radius: 4px; }
.fallback-text  { font-size: 11px; color: #B0B8C1; }
.file-info-row  { display: flex; align-items: center; gap: 10px; }
.file-meta { flex: 1; min-width: 0; }
.file-name { font-size: 12px; font-weight: 600; color: #333D4B; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.file-size { font-size: 11px; color: #8B95A1; margin-top: 1px; }
.file-change { background: none; border: 1px solid #E5E8EB; border-radius: 6px; font-size: 11px; color: #6B7684; padding: 3px 8px; cursor: pointer; font-family: inherit; }
.file-change:hover { border-color: #7C3AED; color: #7C3AED; }

/* PSD 자동 모드 배지 */
.psd-auto-badge {
  margin: 8px 0 6px;
  font-size: 10.5px; font-weight: 500; color: #6D28D9;
  background: #F5F0FF; border-radius: 6px; padding: 5px 9px;
  display: flex; align-items: center; gap: 4px;
}

/* spec 로드 에러 */
.spec-load-error {
  padding: 10px 16px; font-size: 12px; color: #B45309;
  background: #FFFBEB; border-bottom: 1px solid #FDE68A;
  display: flex; align-items: center; gap: 8px;
}
.spec-retry-btn {
  margin-left: auto; font-size: 11px; padding: 2px 8px; border-radius: 5px;
  border: 1px solid #F59E0B; background: #fff; color: #B45309; cursor: pointer; font-family: inherit;
}
.spec-retry-btn:hover { background: #FFFBEB; }

/* safe zone badge on spec item */
.sp-sz-badge {
  font-size: 9px; font-weight: 700; padding: 1px 4px; border-radius: 3px;
  background: #EDE9FF; color: #7C3AED; flex-shrink: 0;
}

/* inputs */
.field-stack { display: flex; flex-direction: column; gap: 8px; }
.input-wrap { position: relative; }
.input-ico { position: absolute; left: 10px; top: 50%; transform: translateY(-50%); pointer-events: none; }
.side-input {
  width: 100%; padding: 9px 12px 9px 30px;
  border: 1.5px solid #EAEDF0; border-radius: 8px;
  font-size: 13px; font-family: inherit; color: #191F28;
  outline: none; transition: border-color 0.12s;
}
.side-input:focus { border-color: #7C3AED; }
.side-input::placeholder { color: #C4CAD0; }

/* platform rows */
.side-loading { padding: 12px 16px; font-size: 13px; color: #B0B8C1; }
.pf-row {
  display: flex; align-items: center; gap: 7px;
  padding: 8px 16px; cursor: pointer; transition: background 0.1s;
}
.pf-row:hover { background: #FAFBFC; }
.pf-dot    { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }
.pf-name   { font-size: 13px; font-weight: 600; color: #333D4B; }
.pf-region { font-size: 11px; color: #B0B8C1; margin-left: 1px; }
.pf-cnt    { font-size: 11px; color: #8B95A1; margin-left: auto; }
.pf-chv    { font-size: 13px; color: #C4CAD4; transform: rotate(90deg); transition: transform 0.18s; margin-left: 4px; }
.pf-chv.open { transform: rotate(-90deg); }

.spec-items { background: #FAFBFC; padding: 2px 0 4px 26px; }
.spec-item { display: flex; align-items: center; gap: 7px; padding: 7px 14px 7px 0; cursor: pointer; border-radius: 6px; transition: background 0.1s; }
.spec-item:hover { background: #F0EEFF; }
.spec-item.on    { background: #EDE9FF; }
.sp-name { font-size: 12px; color: #4E5968; flex: 1; }
.sp-dim  { font-size: 11px; color: #B0B8C1; flex-shrink: 0; }

.check { accent-color: #7C3AED; width: 14px; height: 14px; cursor: pointer; flex-shrink: 0; }
.check.sm { width: 13px; height: 13px; }

/* advanced */
.adv-body  { display: flex; flex-direction: column; gap: 12px; }
.adv-row   { display: flex; align-items: center; gap: 10px; }
.adv-label { font-size: 12px; color: #6B7684; font-weight: 500; width: 46px; flex-shrink: 0; }
.adv-chips { display: flex; gap: 5px; flex-wrap: wrap; }
.adv-chip  { padding: 4px 10px; border-radius: 100px; border: 1px solid #E5E8EB; background: #fff; font-size: 11px; color: #6B7684; cursor: pointer; font-family: inherit; transition: all 0.1s; }
.adv-chip:hover { border-color: #7C3AED; color: #7C3AED; }
.adv-chip.on    { background: #333D4B; border-color: #333D4B; color: #fff; font-weight: 600; }

.ai-only-notice {
  display: flex; align-items: flex-start; gap: 10px;
  background: linear-gradient(135deg, #F0F4FF 0%, #F5F0FF 100%);
  border: 1px solid #C4B5FD; border-radius: 10px;
  padding: 12px 13px; margin-bottom: 12px;
}
.ai-only-icon { font-size: 16px; color: #7C3AED; flex-shrink: 0; margin-top: 1px; }
.ai-only-content { display: flex; flex-direction: column; gap: 3px; }
.ai-only-title { font-size: 12px; font-weight: 700; color: #5B21B6; }
.ai-only-desc { font-size: 10.5px; color: #6D28D9; line-height: 1.5; }

.ai-strength-hint {
  display: flex; align-items: center; gap: 5px;
  font-size: 11px; font-weight: 500; color: #6D28D9;
  background: #F5F0FF; border-radius: 7px; padding: 6px 10px;
  margin-top: -4px;
}
.ai-hint-star { font-size: 9px; opacity: 0.7; }

/* AI analyze */
.ai-analyze-btn {
  width: 100%; margin-top: 10px; padding: 8px;
  background: linear-gradient(135deg, rgba(124,58,237,0.08), rgba(59,130,246,0.06));
  border: 1px dashed #C4B5FD; border-radius: 8px;
  font-size: 12px; font-weight: 600; color: #7C3AED;
  cursor: pointer; font-family: inherit; display: flex; align-items: center; justify-content: center; gap: 6px;
  transition: all 0.12s;
}
.ai-analyze-btn:hover:not(:disabled) { background: #EDE9FF; border-color: #7C3AED; }
.ai-analyze-btn:disabled { opacity: 0.5; cursor: not-allowed; }

.ai-result-card {
  margin-top: 10px; background: #FAFBFF;
  border: 1px solid #E0D9F9; border-radius: 10px; padding: 12px;
}
.ai-result-head {
  font-size: 11.5px; font-weight: 700; color: #7C3AED;
  display: flex; align-items: center; gap: 5px; margin-bottom: 8px;
}
.ai-result-star { font-size: 10px; }
.ai-conf { margin-left: auto; font-size: 10px; font-weight: 500; color: #8B95A1; }
.ai-analysis-section { margin-bottom: 10px; display: flex; flex-direction: column; gap: 5px; }
.ai-analysis-row { display: flex; align-items: center; gap: 6px; font-size: 11px; }
.ai-analysis-label { color: #8B95A1; font-weight: 600; min-width: 58px; flex-shrink: 0; }
.ai-subject-desc { color: #4E5968; font-size: 11px; line-height: 1.4; }

.ai-badge {
  display: inline-block; padding: 2px 8px; border-radius: 100px;
  font-size: 10.5px; font-weight: 700;
  background: #F0ECFF; color: #7C3AED;
}
.ai-badge.text_heavy    { background: #EFF6FF; color: #2563EB; }
.ai-badge.product_focused { background: #F0FDF4; color: #16A34A; }
.ai-badge.balanced_mix  { background: #FFF7ED; color: #EA580C; }
.ai-badge.density-high  { background: #FEF2F2; color: #DC2626; }
.ai-badge.density-medium { background: #FEFCE8; color: #CA8A04; }
.ai-badge.density-low   { background: #F0FDF4; color: #16A34A; }
.ai-badge.risk-high     { background: #FEF2F2; color: #DC2626; }
.ai-badge.risk-medium   { background: #FEFCE8; color: #CA8A04; }
.ai-badge.risk-low      { background: #F0FDF4; color: #16A34A; }

.ai-result-reason { font-size: 11.5px; color: #4E5968; line-height: 1.55; margin-bottom: 8px; }
.ai-result-settings { display: flex; gap: 5px; flex-wrap: wrap; margin-bottom: 8px; }
.ai-tag {
  font-size: 10.5px; font-weight: 600; color: #7C3AED;
  background: #EDE9FF; border-radius: 5px; padding: 3px 8px;
}
.ai-result-warnings { margin-bottom: 8px; }
.ai-warn-item { font-size: 10.5px; color: #B45309; margin-bottom: 3px; line-height: 1.4; }
.ai-quality-section { margin-bottom: 7px; }
.ai-quality-label { font-size: 10px; font-weight: 700; color: #8B95A1; text-transform: uppercase; letter-spacing: 0.4px; margin-bottom: 3px; }
.ai-quality-item { font-size: 11px; line-height: 1.45; padding: 2px 0; }
.ai-quality-good { color: #16A34A; }
.ai-quality-warn { color: #B45309; }
.ai-quality-danger { color: #DC2626; }

/* AI 요소 분석 그룹 */
.ai-element-section { margin-bottom: 8px; display: flex; flex-direction: column; gap: 5px; }
.ai-element-row { display: flex; align-items: flex-start; gap: 6px; }
.ai-el-label {
  font-size: 9.5px; font-weight: 700; padding: 2px 6px; border-radius: 4px;
  flex-shrink: 0; margin-top: 1px; white-space: nowrap;
}
.ai-el-required  { background: #FEF2F2; color: #DC2626; }
.ai-el-priority  { background: #FEFCE8; color: #CA8A04; }
.ai-el-optional  { background: #F2F4F6; color: #6B7684; }
.ai-el-tags { display: flex; flex-wrap: wrap; gap: 4px; }
.ai-el-tag {
  font-size: 10px; font-weight: 600; padding: 1.5px 7px;
  border-radius: 4px; border: 1px solid transparent;
}
.ai-el-tag-required { background: #FFF0F0; color: #DC2626; border-color: #FCA5A5; }
.ai-el-tag-priority { background: #FFFBEB; color: #B45309; border-color: #FCD34D; }
.ai-el-tag-optional { background: #F9FAFB; color: #6B7684; border-color: #E5E8EB; }

.ai-apply-btn {
  width: 100%; padding: 7px;
  background: #7C3AED; border: none; border-radius: 7px;
  font-size: 12px; font-weight: 700; color: #fff;
  cursor: pointer; font-family: inherit; transition: all 0.12s;
}
.ai-apply-btn:hover { opacity: 0.88; }
.ai-apply-btn.applied { background: #16A34A; cursor: default; }

.adv-row-pos { align-items: flex-start; }
.adv-pos-grid {
  display: grid; grid-template-columns: repeat(3, 1fr); gap: 4px;
}
.adv-chip-pos {
  padding: 5px 0; width: 100%; text-align: center;
  border-radius: 7px; font-size: 10.5px;
}

/* sidebar footer */
.sidebar-foot { padding: 14px 16px; border-top: 1px solid #EAEDF0; background: #fff; flex-shrink: 0; }
.foot-info { font-size: 12px; color: #6B7684; margin-bottom: 8px; }
.foot-info b { color: #7C3AED; font-weight: 700; }
.gen-btn {
  width: 100%; padding: 12px;
  background: linear-gradient(135deg, #7C3AED, #3B82F6);
  color: #fff; border: none; border-radius: 10px;
  font-size: 14px; font-weight: 700; font-family: inherit;
  cursor: pointer; display: flex; align-items: center; justify-content: center; gap: 7px;
  transition: opacity 0.12s;
}
.gen-btn:hover:not(:disabled) { opacity: 0.88; }
.gen-btn:disabled { opacity: 0.45; cursor: not-allowed; }
.gen-star { font-size: 13px; }
.spinner { width: 14px; height: 14px; border: 2px solid rgba(255,255,255,0.3); border-top-color: #fff; border-radius: 50%; animation: spin 0.7s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

/* ===== right panel ===== */
.right-panel {
  flex: 1; display: flex; flex-direction: column; overflow: hidden;
  background:
    radial-gradient(ellipse at 20% 10%, rgba(124,58,237,0.07) 0%, transparent 50%),
    radial-gradient(ellipse at 80% 85%, rgba(59,130,246,0.06) 0%, transparent 55%),
    radial-gradient(ellipse at 55% 45%, rgba(236,72,153,0.04) 0%, transparent 50%),
    #F9F8FD;
}
.right-scroll { flex: 1; overflow-y: auto; padding: 28px 28px 20px; }

/* hero */
.hero-row {
  display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; margin-bottom: 28px;
}
.hero-left { flex: 1; position: relative; }
.sparkles { position: absolute; top: -8px; left: -4px; pointer-events: none; }
.sp { position: absolute; color: #7C3AED; font-size: 12px; opacity: 0.5; }
.sp1 { top: 0; left: 0; font-size: 14px; opacity: 0.7; }
.sp2 { top: -10px; left: 80px; font-size: 10px; opacity: 0.4; }
.sp3 { top: 14px; left: 50px; font-size: 8px; opacity: 0.35; }
.hero-title {
  font-size: 26px; font-weight: 800; color: #191F28;
  letter-spacing: -0.8px; margin-bottom: 6px; padding-top: 4px;
}
.accent-num {
  background: linear-gradient(135deg, #7C3AED, #3B82F6);
  -webkit-background-clip: text; -webkit-text-fill-color: transparent;
  background-clip: text;
}
.hero-sub { font-size: 13px; color: #8B95A1; }

/* AI insight card */
.ai-insight-card {
  background: #fff; border-radius: 16px; padding: 16px 18px;
  box-shadow: 0 1px 4px rgba(0,0,0,0.06); min-width: 280px; max-width: 320px; flex-shrink: 0;
  border: 1px solid #F0EEF8;
}
.ai-insight-head { font-size: 12px; font-weight: 700; color: #7C3AED; margin-bottom: 12px; display: flex; align-items: center; gap: 5px; }
.ai-star { font-size: 11px; }
.ai-features { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.ai-feat { }
.ai-feat-ico   { font-size: 16px; color: #7C3AED; display: block; margin-bottom: 4px; }
.ai-feat-title { font-size: 11px; font-weight: 700; color: #333D4B; margin-bottom: 2px; }
.ai-feat-desc  { font-size: 10px; color: #8B95A1; line-height: 1.4; }

/* empty */
.empty-hint { display: flex; flex-direction: column; align-items: center; justify-content: center; height: 200px; }
.empty-icon { font-size: 36px; color: #D1D8E0; margin-bottom: 12px; }
.empty-text { font-size: 13px; color: #B0B8C1; }

/* platform group */
.pf-group { margin-bottom: 28px; }
.pf-group-head { display: flex; align-items: center; gap: 8px; margin-bottom: 14px; }
.pf-badge {
  width: 26px; height: 26px; border-radius: 50%;
  color: #fff; font-size: 12px; font-weight: 700;
  display: flex; align-items: center; justify-content: center; flex-shrink: 0;
}
.pf-group-name { font-size: 14px; font-weight: 700; color: #191F28; }
.pf-group-cnt  { font-size: 13px; font-weight: 700; color: #6B7684; margin-right: auto; }
.desel-btn { padding: 4px 12px; border-radius: 100px; border: 1.5px solid #E5E8EB; background: #fff; font-size: 12px; color: #6B7684; cursor: pointer; font-family: inherit; transition: all 0.1s; }
.desel-btn:hover { border-color: #7C3AED; color: #7C3AED; }
.view-toggle { display: flex; gap: 4px; margin-left: 8px; }
.vt-btn { width: 28px; height: 28px; border: 1.5px solid #E5E8EB; background: #fff; border-radius: 6px; cursor: pointer; font-size: 14px; color: #B0B8C1; display: flex; align-items: center; justify-content: center; }
.vt-btn.on { border-color: #7C3AED; color: #7C3AED; background: #F5F0FF; }

/* horizontal cards */
.hcards-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.hcards-list { display: flex; flex-direction: column; gap: 8px; }
.hcards-list .hcard { height: 72px; }
.hcards-list .hcard-preview { width: 96px; }
.hcards-list .spec-preview-canvas { padding: 6px; }
.hcards-list .spec-preview-frame { max-width: 82px; max-height: 58px; }
.hcards-list .spec-preview-frame.vertical,
.hcards-list .spec-preview-frame.tall { height: 58px; width: auto; }
.hcards-list .hcard-name { -webkit-line-clamp: 1; }
.hcards-list .hcard-meta { display: none; }

.hcard {
  display: flex; background: #fff; border-radius: 14px;
  border: 1px solid #EAEDF0; overflow: hidden; min-height: 130px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.04); transition: box-shadow 0.12s;
}
.hcard:hover { box-shadow: 0 4px 12px rgba(0,0,0,0.08); }
.hcards-list .hcard-guide,
.hcards-list .hcard-details { display: none; }

/* 세이프존 시각화 오버레이 — .spec-preview-frame(position:relative) 내부에만 표시 */
.sz-frame-overlay {
  position: absolute;
  box-sizing: border-box;
  border: 1.5px dashed rgba(124,58,237,0.80);
  border-radius: 1px;
  pointer-events: none;
  z-index: 2;
  box-shadow: inset 0 0 0 1px rgba(124,58,237,0.15);
}

/* 제작 가이드 */
.hcard-guide { display: flex; flex-direction: column; gap: 3px; margin-top: 6px; }
.hcard-guide-row { display: flex; align-items: center; flex-wrap: wrap; gap: 3px; }
.hcard-fmt-chip {
  font-size: 9px; font-weight: 700; padding: 1px 5px; border-radius: 4px;
  background: #EEF2FF; color: #4338CA; border: 1px solid #C7D2FE;
}
.hcard-guide-size { font-size: 9.5px; color: #6B7280; font-weight: 500; }
.hcard-sz-label {
  font-size: 9px; font-weight: 700; color: #7C3AED;
  background: #F5F3FF; border-radius: 4px; padding: 1px 5px;
  border: 1px solid #DDD6FE; flex-shrink: 0;
  cursor: pointer; user-select: none;
  transition: background 0.1s, box-shadow 0.1s;
}
.hcard-sz-label:hover { background: #EDE9FE; }
.hcard-sz-active {
  background: #7C3AED !important; color: #fff !important;
  border-color: #6D28D9 !important;
  box-shadow: 0 0 0 2px rgba(124,58,237,0.2);
}
.hcard-sz-chip {
  font-size: 9px; font-weight: 600; padding: 1px 4px; border-radius: 3px;
  background: #EDE9FE; color: #5B21B6; font-family: monospace;
}
.hcard-notes-text {
  font-size: 9.5px; color: #6B7280; line-height: 1.4;
  background: #FFF7ED; border-radius: 4px; padding: 2px 5px;
  border: 1px solid #FDE68A;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}

/* 상세 가이드 접기 */
.hcard-details { margin-top: 6px; }
.hcard-details-lbl {
  font-size: 10px; font-weight: 600; color: #9CA3AF;
  cursor: pointer; list-style: none; user-select: none; padding: 2px 0;
}
.hcard-details-lbl::-webkit-details-marker { display: none; }
.hcard-details-lbl::before { content: '▶ '; font-size: 7px; }
details[open] > .hcard-details-lbl::before { content: '▼ '; }
.hcard-details-body {
  margin-top: 4px; display: flex; flex-direction: column; gap: 3px;
  padding: 6px 7px; background: #F9FAFB; border-radius: 6px;
  border: 1px solid #EAEDF0;
}
.hcard-dk-row { display: flex; align-items: flex-start; gap: 6px; }
.hcard-dk { font-size: 9.5px; color: #9CA3AF; min-width: 52px; flex-shrink: 0; }
.hcard-dv { font-size: 9.5px; color: #374151; }
.hcard-dv-muted { color: #9CA3AF; }
.hcard-source-link { font-size: 9.5px; color: #7C3AED; font-weight: 600; text-decoration: none; }
.hcard-source-link:hover { text-decoration: underline; }
.hcard-no-detail { font-size: 9.5px; color: #9CA3AF; font-style: italic; }

.hcard-preview {
  width: 155px; flex-shrink: 0; overflow: hidden; background: #F2F4F6;
  display: flex; align-items: center; justify-content: center;
}
.spec-preview-canvas {
  width: 100%; height: 100%;
  display: flex; align-items: center; justify-content: center;
  padding: 10px; box-sizing: border-box;
}
.spec-preview-frame {
  max-width: 133px; max-height: 108px;
  background: #fff;
  border: 1px solid rgba(148, 163, 184, 0.3);
  border-radius: 7px; overflow: hidden;
  box-shadow: 0 3px 10px rgba(15, 23, 42, 0.1);
  min-height: 20px;
  position: relative; /* sz-frame-overlay의 containing block */
}
.spec-preview-img { width: 100%; height: 100%; object-fit: cover; display: block; }
.spec-preview-frame.ultra-wide { width: 133px; }
.spec-preview-frame.wide       { width: 133px; }
.spec-preview-frame.square     { width: 90px; }
.spec-preview-frame.vertical   { height: 108px; width: auto; }
.spec-preview-frame.tall       { height: 108px; width: auto; }
.spec-preview-ph {
  width: 100%; height: 100%; min-width: 40px; min-height: 20px;
  display: flex; align-items: center; justify-content: center;
  font-size: 10px; color: #8B95A1; font-weight: 600;
}

.hcard-info { flex: 1; padding: 14px 12px 12px; position: relative; min-width: 0; }
.hcard-x {
  position: absolute; top: 10px; right: 10px;
  width: 20px; height: 20px; background: #F2F4F6; border: none; border-radius: 50%;
  font-size: 14px; color: #8B95A1; cursor: pointer; display: flex; align-items: center; justify-content: center; padding: 0;
}
.hcard-x:hover { background: #FFE5E5; color: #FF3B30; }
.hcard-name { font-size: 11.5px; font-weight: 600; color: #333D4B; margin-bottom: 4px; padding-right: 22px; line-height: 1.4; overflow: hidden; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
.hcard-dim  { font-size: 11px; color: #8B95A1; margin-bottom: 8px; }
.hcard-tag  { display: inline-block; font-size: 10px; font-weight: 700; padding: 2px 7px; border-radius: 5px; margin-bottom: 7px; }
.hcard-meta { display: flex; gap: 6px; flex-wrap: wrap; }
.hcard-ratio, .hcard-orient {
  font-size: 10px; color: #6B7684; background: #F2F4F6;
  padding: 2px 7px; border-radius: 4px; font-weight: 500;
}

/* ── AI 객체 분석 섹션 ─────────────────────────────────────── */
.oa-section {
  border: 1.5px solid #E5E7EB; border-radius: 12px;
  background: #F8FAFC; padding: 14px 16px; margin-bottom: 16px;
}
/* 툴바 */
.oa-toolbar { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.oa-toolbar-left { display: flex; align-items: center; gap: 6px; flex: 1; min-width: 0; flex-wrap: wrap; }
.oa-toolbar-actions { display: flex; align-items: center; gap: 6px; flex-shrink: 0; }
.oa-title { font-size: 13px; font-weight: 700; color: #1F2937; }
.oa-beta {
  font-size: 9px; font-weight: 700; padding: 1px 5px; border-radius: 4px;
  background: #EDE9FE; color: #7C3AED; flex-shrink: 0;
}
.oa-artboard-info {
  font-size: 10.5px; color: #6B7280;
  background: #F1F5F9; border-radius: 5px; padding: 2px 7px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.oa-btn {
  font-size: 11px; font-weight: 600; padding: 6px 14px; border-radius: 7px;
  background: linear-gradient(135deg, #7C3AED, #3B82F6); color: #fff; border: none;
  cursor: pointer; white-space: nowrap; transition: opacity 0.15s; flex-shrink: 0;
}
.oa-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.oa-view-all-btn {
  font-size: 11px; font-weight: 600; padding: 5px 10px; border-radius: 7px;
  background: #fff; color: #7C3AED; border: 1.5px solid #DDD6FE;
  cursor: pointer; white-space: nowrap; transition: all 0.12s;
}
.oa-view-all-btn:hover { background: #F5F3FF; }
/* 로딩 */
.oa-loading { display: flex; align-items: center; gap: 8px; font-size: 11px; color: #6B7280; padding: 8px 0; }
.oa-spinner {
  width: 14px; height: 14px; border: 2px solid #E5E7EB;
  border-top-color: #7C3AED; border-radius: 50%; animation: oa-spin 0.7s linear infinite; flex-shrink: 0;
}
@keyframes oa-spin { to { transform: rotate(360deg); } }
/* 에러 */
.oa-error { font-size: 11px; color: #DC2626; background: #FEF2F2; border-radius: 6px; padding: 7px 10px; }
/* compact 상태 바 */
.oa-status-bar {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  padding: 5px 8px; border-radius: 7px; background: #F1F5F9; margin-bottom: 10px;
}
.oa-rf-badge { font-size: 10px; font-weight: 700; padding: 2px 8px; border-radius: 10px; white-space: nowrap; }
.rf-ok { background: #D1FAE5; color: #065F46; }
.rf-ng { background: #FEE2E2; color: #991B1B; }
.oa-missing { font-size: 10px; color: #92400E; }
.oa-selected-info { font-size: 10px; color: #7C3AED; font-weight: 600; }
/* 본문 레이아웃 */
.oa-body { display: flex; gap: 12px; align-items: flex-start; }
/* 프리뷰 패널 */
.oa-preview-panel { flex: 0 0 58%; min-width: 0; }
.oa-preview-wrap {
  position: relative; width: 100%;
  min-height: 360px; height: 360px;
  background: #111; border-radius: 8px; overflow: hidden;
  display: flex; align-items: center; justify-content: center;
}
.oa-preview-img { width: 100%; height: 100%; object-fit: contain; display: block; }
/* bbox 오버레이 */
.oa-bbox {
  position: absolute; border: 2px solid; border-radius: 2px;
  box-sizing: border-box; pointer-events: auto; cursor: pointer;
  transition: box-shadow 0.12s, opacity 0.12s;
}
.oa-bbox-hl { box-shadow: 0 0 0 2px rgba(255,255,255,0.5); z-index: 10; }
.oa-bbox-selected { box-shadow: 0 0 0 3px rgba(255,255,255,0.9) !important; z-index: 20; border-width: 3px; }
.oa-bbox-dim { opacity: 0.2; }
.oa-bbox-label {
  position: absolute; top: 0; left: 0; font-size: 9px; font-weight: 700;
  padding: 1px 3px; border-radius: 0 0 2px 0; white-space: nowrap; line-height: 1.4;
}
/* role별 색상 */
.oa-role-background { border-color: rgba(107,114,128,0.5); }
.oa-role-background .oa-bbox-label { background: rgba(107,114,128,0.75); color: #fff; }
.oa-role-title { border-color: rgba(59,130,246,0.85); }
.oa-role-title .oa-bbox-label { background: rgba(59,130,246,0.85); color: #fff; }
.oa-role-main_image { border-color: rgba(16,185,129,0.85); }
.oa-role-main_image .oa-bbox-label { background: rgba(16,185,129,0.85); color: #fff; }
.oa-role-cta { border-color: rgba(239,68,68,0.85); }
.oa-role-cta .oa-bbox-label { background: rgba(239,68,68,0.85); color: #fff; }
.oa-role-logo { border-color: rgba(124,58,237,0.85); }
.oa-role-logo .oa-bbox-label { background: rgba(124,58,237,0.85); color: #fff; }
.oa-role-body_text { border-color: rgba(245,158,11,0.85); }
.oa-role-body_text .oa-bbox-label { background: rgba(245,158,11,0.85); color: #fff; }
.oa-role-badge { border-color: rgba(234,88,12,0.85); }
.oa-role-badge .oa-bbox-label { background: rgba(234,88,12,0.85); color: #fff; }
.oa-role-decoration, .oa-role-unknown { border-color: rgba(156,163,175,0.4); border-style: dashed; }
.oa-role-decoration .oa-bbox-label, .oa-role-unknown .oa-bbox-label { background: rgba(156,163,175,0.5); color: #374151; }
/* 매칭 상태 */
.oa-ms-ready { opacity: 1; }
.oa-ms-matched_low_confidence { opacity: 0.75; }
.oa-ms-missing_layer { opacity: 0.35; border-style: dashed; }

/* crop preview */
.oa-crop-preview {
  margin-top: 10px; border-radius: 8px; overflow: hidden;
  border: 1px solid #E5E7EB; background: #111;
}
.oa-crop-header {
  display: flex; align-items: center; padding: 6px 10px;
  background: #1F2937; border-bottom: 1px solid #374151;
}
.oa-crop-title { font-size: 11px; font-weight: 600; color: #E5E7EB; flex: 1; }
.oa-crop-close {
  background: none; border: none; color: #9CA3AF; cursor: pointer; font-size: 16px;
  padding: 0 2px; line-height: 1; font-family: inherit;
}
.oa-crop-close:hover { color: #F87171; }
.oa-crop-canvas { display: block; margin: 0 auto; }
.oa-crop-meta {
  font-size: 10px; color: #6B7280; text-align: center;
  padding: 4px; background: #1F2937;
}
.oa-preview-hint {
  margin-top: 6px; font-size: 10.5px; color: #9CA3AF;
  text-align: center;
}

/* 인스펙터 패널 */
.oa-inspector {
  flex: 1; min-width: 0;
  max-height: 600px; overflow-y: auto;
  display: flex; flex-direction: column; gap: 4px;
}
.oa-inspector::-webkit-scrollbar { width: 4px; }
.oa-inspector::-webkit-scrollbar-track { background: transparent; }
.oa-inspector::-webkit-scrollbar-thumb { background: #DDE0E7; border-radius: 2px; }
.oa-inspector-hint {
  font-size: 10px; color: #9CA3AF; margin-bottom: 6px;
  background: #F9FAFB; border-radius: 5px; padding: 4px 7px;
  line-height: 1.4;
}
/* 인스펙터 카드 */
.oa-ins-card {
  border: 1px solid #E5E7EB; border-radius: 7px;
  padding: 6px 9px; background: #fff;
  cursor: pointer; transition: border-color 0.1s, background 0.1s;
}
.oa-ins-card:hover { border-color: #C4B5FD; }
.oa-ins-hl { border-color: #A78BFA !important; background: #F5F3FF !important; }
.oa-ins-selected { border-color: #7C3AED !important; background: #EDE9FF !important; box-shadow: 0 0 0 2px rgba(124,58,237,0.15); }
.oa-ins-secondary { opacity: 0.7; }
.oa-ins-row { display: flex; align-items: center; gap: 5px; }
.oa-role-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.oa-dot-background { background: #6B7280; }
.oa-dot-title { background: #3B82F6; }
.oa-dot-main_image { background: #10B981; }
.oa-dot-cta { background: #EF4444; }
.oa-dot-logo { background: #7C3AED; }
.oa-dot-body_text { background: #F59E0B; }
.oa-dot-badge { background: #EA580C; }
.oa-dot-decoration { background: #9CA3AF; }
.oa-dot-unknown { background: #D1D5DB; }
.oa-ins-role { font-size: 11px; font-weight: 700; color: #374151; white-space: nowrap; flex-shrink: 0; }
.oa-ins-label { font-size: 11px; color: #6B7280; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.oa-imp-chip { font-size: 9px; font-weight: 700; padding: 1px 5px; border-radius: 6px; white-space: nowrap; flex-shrink: 0; }
.imp-required { background: #FEE2E2; color: #991B1B; }
.imp-priority  { background: #FEF3C7; color: #92400E; }
.imp-optional  { background: #F3F4F6; color: #6B7280; }
.oa-ins-detail {
  display: flex; align-items: center; gap: 6px; margin-top: 2px;
  font-size: 10px; color: #9CA3AF;
}
.oa-ins-bbox { font-family: monospace; }
.oa-ins-conf { font-weight: 600; color: #7C3AED; }
.oa-ins-pipeline { display: flex; align-items: center; gap: 3px; margin-top: 3px; flex-wrap: wrap; }
.oa-pipe-arrow { font-size: 9px; color: #D1D5DB; flex-shrink: 0; }
.oa-stage {
  font-size: 9px; font-weight: 600; padding: 1px 5px; border-radius: 6px;
  white-space: nowrap; flex-shrink: 0;
}
.oa-stage-ok   { background: #D1FAE5; color: #065F46; }
.oa-stage-warn { background: #FEF3C7; color: #92400E; }
.oa-stage-fail { background: #FEE2E2; color: #991B1B; }
.oa-stage-info { background: #DBEAFE; color: #1E40AF; }
.oa-stage-muted { background: #F3F4F6; color: #9CA3AF; }
.oa-ins-layer { font-size: 10px; color: #6B7280; font-family: monospace; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; margin-left: 4px; }
.oa-ins-score { font-size: 10px; font-weight: 700; color: #374151; flex-shrink: 0; }
.oa-ins-more { margin-top: 2px; }
.oa-ins-more-lbl { font-size: 10px; color: #9CA3AF; cursor: pointer; padding: 3px 0; list-style: none; }
.oa-ins-more summary::-webkit-details-marker { display: none; }

/* AI bar */
.ai-bar {
  flex-shrink: 0; padding: 12px 24px;
  background: rgba(255,255,255,0.92); backdrop-filter: blur(8px);
  border-top: 1px solid rgba(124,58,237,0.12);
  display: flex; align-items: center; gap: 14px;
}
.ai-bar-left { display: flex; align-items: center; gap: 12px; flex: 1; }
.ai-bar-ico {
  width: 34px; height: 34px; border-radius: 8px; flex-shrink: 0;
  background: linear-gradient(135deg, #7C3AED, #3B82F6);
  color: #fff; font-size: 14px; display: flex; align-items: center; justify-content: center;
}
.ai-bar-title { font-size: 13px; font-weight: 700; color: #333D4B; }
.ai-bar-desc  { font-size: 11px; color: #8B95A1; margin-top: 1px; }
.ai-bar-btn {
  padding: 7px 16px; border-radius: 8px; border: 1.5px solid #E5E8EB; background: #fff;
  font-size: 12px; font-weight: 600; color: #6B7684; cursor: pointer; font-family: inherit;
  white-space: nowrap; flex-shrink: 0;
}
.ai-bar-btn:hover { border-color: #7C3AED; color: #7C3AED; }

/* toast */
.toast {
  position: fixed; bottom: 28px; right: 28px;
  background: #191F28; color: #fff; padding: 12px 18px; border-radius: 12px;
  display: flex; align-items: center; gap: 10px; font-size: 13px; font-weight: 500;
  box-shadow: 0 4px 20px rgba(0,0,0,0.2); z-index: 100;
}
.toast-chk  { color: #0DC780; font-weight: 700; }
.toast-link { color: #A0C4FB; background: none; border: none; cursor: pointer; font-family: inherit; font-size: 13px; font-weight: 600; padding: 0; }
.toast-link:hover { text-decoration: underline; }
.toast-enter-active, .toast-leave-active { transition: all 0.3s; }
.toast-enter-from { opacity: 0; transform: translateY(16px); }
.toast-leave-to   { opacity: 0; transform: translateY(16px); }

/* oa-btn-spin */
.oa-btn-spin {
  display: inline-block; width: 10px; height: 10px; margin-right: 4px; vertical-align: middle;
  border: 1.5px solid rgba(255,255,255,0.35); border-top-color: #fff;
  border-radius: 50%; animation: oa-spin 0.7s linear infinite;
}

/* IDLE state */
.oa-idle-state {
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  padding: 28px 16px; text-align: center; gap: 6px;
}
.oa-idle-icon { font-size: 24px; color: #7C3AED; opacity: 0.6; }
.oa-idle-title { font-size: 13px; font-weight: 700; color: #374151; }
.oa-idle-desc { font-size: 11px; color: #9CA3AF; line-height: 1.6; }

/* meta bar */
.oa-meta-bar {
  display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
  padding: 4px 8px; border-radius: 6px; background: #F1F5F9;
  font-size: 10px; margin-bottom: 6px;
}
.oa-meta-badge-new {
  font-weight: 700; padding: 1px 6px; border-radius: 6px;
  background: #EDE9FE; color: #7C3AED;
}
.oa-meta-badge-cached {
  font-weight: 700; padding: 1px 6px; border-radius: 6px;
  background: #D1FAE5; color: #065F46;
}
.oa-meta-sep { color: #D1D5DB; }
.oa-meta-count { color: #6B7280; font-weight: 600; }
.oa-meta-model {
  color: #9CA3AF; font-family: monospace; font-size: 9.5px;
  background: #F9FAFB; border: 1px solid #E5E7EB; border-radius: 4px; padding: 1px 5px;
}
.oa-meta-saved {
  color: #059669; font-size: 9.5px; font-weight: 600;
  background: #ECFDF5; border: 1px solid #A7F3D0; border-radius: 4px; padding: 1px 5px; margin-left: auto;
}

/* filter chips */
.oa-filter-row {
  display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: 8px;
}
.oa-filter-chip {
  display: flex; align-items: center; gap: 3px;
  font-size: 10px; font-weight: 600; padding: 3px 8px; border-radius: 100px;
  border: 1.5px solid #E5E7EB; background: #fff; color: #6B7280;
  cursor: pointer; transition: all 0.1s; font-family: inherit;
}
.oa-filter-chip:hover { border-color: #C4B5FD; color: #7C3AED; }
.oa-filter-chip.active { border-color: #7C3AED; background: #EDE9FE; color: #7C3AED; }
.oa-filter-cnt {
  font-size: 9px; font-weight: 700; min-width: 14px; text-align: center;
  background: #F3F4F6; color: #9CA3AF; border-radius: 100px; padding: 0 4px;
}
.oa-filter-chip.active .oa-filter-cnt { background: #DDD6FE; color: #5B21B6; }
.oa-filter-empty {
  font-size: 11px; color: #9CA3AF; text-align: center; padding: 16px 0;
}

/* tech details collapse */
.oa-tech-details {
  margin-top: 6px; border-radius: 7px;
  border: 1px solid #E5E7EB; background: #F9FAFB; overflow: hidden;
}
.oa-tech-summary {
  font-size: 10.5px; font-weight: 600; color: #9CA3AF;
  padding: 6px 10px; cursor: pointer; list-style: none; user-select: none;
}
.oa-tech-summary::-webkit-details-marker { display: none; }
.oa-tech-summary::before { content: '▶ '; font-size: 8px; }
details[open] .oa-tech-summary::before { content: '▼ '; }
.oa-tech-body { padding: 4px 10px 8px; display: flex; flex-direction: column; gap: 3px; }
.oa-tech-row { display: flex; align-items: flex-start; gap: 6px; font-size: 10.5px; }
.oa-tech-key { color: #9CA3AF; white-space: nowrap; min-width: 80px; flex-shrink: 0; }
.oa-tech-val { color: #374151; flex: 1; min-width: 0; }
.oa-tech-mono { font-family: monospace; font-size: 10px; }
.oa-tech-truncate { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* mobile responsive */
@media (max-width: 900px) {
  .oa-body { flex-direction: column; }
  .oa-preview-panel { flex: none; width: 100%; }
  .oa-preview-wrap { min-height: 280px; height: 280px; }
  .oa-inspector { max-height: 300px; }
}

/* dark mode */
@media (prefers-color-scheme: dark) {
  .oa-section { background: #1A1D27; border-color: #2D3142; }
  .oa-title { color: #F9FAFB; }
  .oa-artboard-info { background: #252836; color: #9CA3AF; }
  .oa-status-bar { background: #252836; }
  .oa-preview-wrap { background: #0D0D0D; }
  .oa-ins-card { background: #252836; border-color: #374151; }
  .oa-ins-hl { border-color: #7C3AED !important; background: #2D1F4A !important; }
  .oa-ins-selected { border-color: #7C3AED !important; background: #2D1F4A !important; }
  .oa-ins-role { color: #E5E7EB; }
  .oa-ins-label { color: #9CA3AF; }
  .oa-ins-layer { color: #6B7280; }
  .oa-ins-score { color: #E5E7EB; }
  .oa-error { background: #3B1212; color: #FCA5A5; }
  .imp-optional { background: #374151; color: #9CA3AF; }
  .oa-stage-ok   { background: #064E3B; color: #6EE7B7; }
  .oa-stage-warn { background: #451A03; color: #FCD34D; }
  .oa-stage-fail { background: #450A0A; color: #FCA5A5; }
  .oa-stage-info { background: #1E3A5F; color: #93C5FD; }
  .oa-stage-muted { background: #374151; color: #6B7280; }
  .oa-ins-more-lbl { color: #6B7280; }
  .oa-crop-preview { border-color: #374151; }
  .oa-inspector-hint { background: #252836; color: #6B7280; }
  .psd-auto-badge { background: #2D1F4A; color: #C4B5FD; }
  .oa-view-all-btn { background: #252836; border-color: #4C3D6E; color: #C4B5FD; }
  .oa-idle-title { color: #E5E7EB; }
  .oa-meta-bar { background: #252836; }
  .oa-meta-model { background: #1F2937; border-color: #374151; color: #9CA3AF; }
  .oa-meta-saved { background: #064E3B; border-color: #065F46; color: #6EE7B7; }
  .oa-filter-chip { background: #1F2937; border-color: #374151; color: #9CA3AF; }
  .oa-filter-chip:hover { border-color: #7C3AED; color: #C4B5FD; }
  .oa-filter-chip.active { background: #2D1F4A; border-color: #7C3AED; color: #C4B5FD; }
  .oa-filter-cnt { background: #374151; color: #6B7280; }
  .oa-tech-details { background: #1F2937; border-color: #374151; }
  .oa-tech-summary { color: #6B7280; }
  .oa-tech-key { color: #6B7280; }
  .oa-tech-val { color: #E5E7EB; }
}
</style>
