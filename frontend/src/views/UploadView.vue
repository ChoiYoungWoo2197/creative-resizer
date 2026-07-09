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
              <!-- PSD 처리 방식 선택 -->
            <div v-if="isPsdFile" class="psd-mode-section">
              <div class="psd-mode-title">
                PSD 처리 방식
                <span v-if="psdLayerAnalyzing" class="psd-compat-loading">
                  <span class="spinner" style="width:10px;height:10px;border-width:1.5px;display:inline-block;" />
                  레이어 분석 중...
                </span>
              </div>
              <!-- PSD 호환성 진단 결과 -->
              <div v-if="!psdLayerAnalyzing && psdLayerAnalysis" class="psd-compat-status">
                <template v-if="psdLayerAnalysis.layerReadable === false">
                  <div class="psd-compat-row psd-compat-error">
                    <span class="psd-compat-icon">✕</span>
                    <div>
                      <div class="psd-compat-label">PSD 레이어 분석 불가</div>
                      <div class="psd-compat-reason">
                        사유: {{ psdLayerAnalysis.layerReadErrorCode === 'PSD_VERSION_8_UNSUPPORTED' ? 'Invalid version 8' : (psdLayerAnalysis.layerReadError || '알 수 없음') }}
                      </div>
                      <div class="psd-compat-hint">이 PSD는 레이어 재배치를 사용할 수 없습니다. 단순 이미지 처리로 자동 전환됩니다.</div>
                    </div>
                  </div>
                </template>
                <template v-else-if="psdLayerAnalysis.psdCompatPatched">
                  <div class="psd-compat-row psd-compat-warn">
                    <span class="psd-compat-icon">⚠</span>
                    <div>
                      <div class="psd-compat-label">PSD 호환 모드 적용됨</div>
                      <div class="psd-compat-hint">일부 최신 PSD 기능은 제한될 수 있습니다.</div>
                    </div>
                  </div>
                </template>
                <template v-else-if="psdLayerAnalysis.layerReflowAvailable">
                  <div class="psd-compat-row psd-compat-ok">
                    <span class="psd-compat-icon">✓</span>
                    <div>
                      <div class="psd-compat-label">PSD 레이어 분석 가능</div>
                      <div class="psd-compat-hint">레이어 {{ psdLayerAnalysis.layerCount }}개 감지 · 레이어 재배치 Beta를 사용할 수 있습니다.</div>
                    </div>
                  </div>
                </template>
                <template v-else-if="psdLayerAnalysis.layerReadable">
                  <div class="psd-compat-row psd-compat-warn">
                    <span class="psd-compat-icon">⚠</span>
                    <div>
                      <div class="psd-compat-label">레이어 재배치 지원 불가</div>
                      <div class="psd-compat-hint">필수 레이어(메인카피, 제품/비주얼 또는 CTA)가 감지되지 않았습니다.</div>
                    </div>
                  </div>
                </template>
              </div>
              <div class="psd-mode-options">
                <label class="psd-mode-option" :class="{ on: psdMode === 'artboard-first' }">
                  <input type="radio" name="psdMode" value="artboard-first" v-model="psdMode" style="display:none" />
                  <div class="psd-mode-body" @click="psdMode = 'artboard-first'">
                    <div class="psd-mode-name">아트보드 우선 <span class="psd-mode-badge">기본</span></div>
                    <div class="psd-mode-desc">PSD 안의 가로형·세로형·정방형 아트보드를 자동 선택합니다.</div>
                  </div>
                </label>
                <label class="psd-mode-option" :class="{ on: psdMode === 'flatten' }">
                  <input type="radio" name="psdMode" value="flatten" v-model="psdMode" style="display:none" />
                  <div class="psd-mode-body" @click="psdMode = 'flatten'">
                    <div class="psd-mode-name">단순 이미지 처리</div>
                    <div class="psd-mode-desc">PSD 전체를 하나의 이미지로 렌더링한 뒤 기존 리사이징을 적용합니다.</div>
                  </div>
                </label>
                <label class="psd-mode-option"
                  :class="{ on: psdMode === 'layer-reflow', disabled: psdLayerAnalysis && !psdLayerAnalysis.layerReflowAvailable }">
                  <input type="radio" name="psdMode" value="layer-reflow" v-model="psdMode" style="display:none"
                    :disabled="psdLayerAnalysis && !psdLayerAnalysis.layerReflowAvailable" />
                  <div class="psd-mode-body"
                    @click="(!psdLayerAnalysis || psdLayerAnalysis.layerReflowAvailable) ? psdMode = 'layer-reflow' : null">
                    <div class="psd-mode-name">
                      레이어 재배치 <span class="psd-mode-badge psd-mode-beta">Beta</span>
                    </div>
                    <div class="psd-mode-desc">
                      <template v-if="psdLayerAnalysis && !psdLayerAnalysis.layerReflowAvailable">
                        사용 불가: PSD 레이어 분석이 지원되지 않는 파일입니다.
                      </template>
                      <template v-else>
                        PSD 레이어를 분석해 배너 규격에 맞게 다시 배치합니다. 현재 1250×560 가로형 우선 지원.
                      </template>
                    </div>
                  </div>
                </label>
                <label v-if="objReflowCanActivate"
                  class="psd-mode-option"
                  :class="{ on: psdMode === 'object-reflow', 'psd-mode-option-warn': !objAnalysisResult?.reflowReady }">
                  <input type="radio" name="psdMode" value="object-reflow" v-model="psdMode" style="display:none" />
                  <div class="psd-mode-body" @click="psdMode = 'object-reflow'">
                    <div class="psd-mode-name">
                      객체 기반 재배치 <span class="psd-mode-badge psd-mode-beta">Beta</span>
                    </div>
                    <div class="psd-mode-desc">
                      <template v-if="objAnalysisResult?.reflowReady">
                        AI 객체 분석 결과를 기반으로 레이아웃을 재구성합니다.
                      </template>
                      <template v-else>
                        ⚠ 일부 객체가 레이어 미매칭 상태입니다. AI 영역 crop으로 재배치합니다(품질이 낮을 수 있음).
                      </template>
                    </div>
                  </div>
                </label>
              </div>
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
                  </div>
                </div>
              </div>
            </template>
          </div>
        </div>

        <!-- Advanced -->
        <div class="sec">
          <div class="sec-head" @click="advOpen = !advOpen">
            <span class="sec-title">고급 옵션</span>
            <span class="chevron" :class="{ up: advOpen }">›</span>
          </div>
          <div v-show="advOpen" class="sec-body adv-body">
            <div class="adv-row">
              <span class="adv-label">리사이즈</span>
              <div class="adv-chips">
                <button v-for="r in resizeOptions" :key="r.value" type="button"
                  class="adv-chip" :class="{ on: form.resizeMode === r.value }"
                  @click="form.resizeMode = r.value">{{ r.label }}</button>
              </div>
            </div>
            <div v-if="form.resizeMode === 'smart-fit'" class="adv-row">
              <span class="adv-label">소재 유형</span>
              <div class="adv-chips">
                <button v-for="t in materialTypeOptions" :key="t.value" type="button"
                  class="adv-chip" :class="{ on: materialType === t.value }"
                  @click="selectMaterialType(t.value)">{{ t.label }}</button>
              </div>
            </div>
            <div v-if="form.resizeMode === 'smart-fit'" class="adv-row">
              <span class="adv-label">강도</span>
              <div class="adv-chips">
                <button v-for="s in smartFitOptions" :key="s.value" type="button"
                  class="adv-chip" :class="{ on: form.smartFitStrength === s.value }"
                  @click="selectStrength(s.value)">{{ s.label }}</button>
              </div>
            </div>
            <div v-if="form.resizeMode === 'smart-fit' && currentMaterialHint" class="ai-strength-hint">
              <span class="ai-hint-star">✦</span> {{ currentMaterialHint }}
            </div>
            <div v-if="form.resizeMode === 'smart-fit'" class="adv-row adv-row-pos">
              <span class="adv-label">위치</span>
              <div class="adv-pos-grid">
                <button v-for="p in focalPositionOptions" :key="p.value" type="button"
                  class="adv-chip adv-chip-pos" :class="{ on: form.focalPosition === p.value }"
                  @click="form.focalPosition = p.value">{{ p.label }}</button>
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

        <!-- PSD 아트보드 선택 섹션 -->
        <div v-if="isPsdFile && (psdLayerAnalyzing || psdLayerAnalysis)" class="azs-section">
          <div class="azs-header">
            <div>
              <div class="azs-title">AI가 분석할 광고 영역을 선택해 주세요</div>
              <div class="azs-sub">리사이즈할 배너의 가로, 세로 비율에 맞는 배너를 선택해 주세요.</div>
            </div>
            <button v-if="detectedArtboards.length > 0" class="azs-select-all"
              @click="toggleAllArtboards">
              {{ selectedArtboardIds.length === detectedArtboards.length ? '전체 해제' : '전체 선택' }}
            </button>
          </div>

          <!-- 분석 중 skeleton -->
          <div v-if="psdLayerAnalyzing" class="azs-cards">
            <div v-for="i in 3" :key="i" class="azs-card azs-card-skeleton">
              <div class="azs-thumb-skeleton" />
              <div class="azs-skeleton-line" style="width:60%;margin-top:8px" />
              <div class="azs-skeleton-line" style="width:40%;margin-top:4px" />
            </div>
          </div>

          <!-- 카드 목록 -->
          <div v-else-if="detectedArtboards.length > 0" class="azs-cards">
            <div v-for="ab in detectedArtboards" :key="ab.id"
                 class="azs-card"
                 :class="[
                   { 'azs-selected': selectedArtboardIds.includes(ab.id) },
                   'azs-' + ab.artboardType
                 ]"
                 @click="toggleArtboard(ab.id)">
              <div class="azs-thumb-wrap">
                <img v-if="ab.thumbnail" :src="ab.thumbnail" class="azs-thumb" :alt="ab.name" />
                <div v-else class="azs-thumb-placeholder">
                  {{ ab.width }}×{{ ab.height }}
                </div>
              </div>
              <div class="azs-card-body">
                <div class="azs-card-badges">
                  <div class="azs-type-badge" :class="'azst-' + ab.artboardType">
                    {{ artboardTypeLabel(ab.artboardType) }}
                  </div>
                  <div v-if="ab.source" class="azs-source-badge" :class="'azss-' + ab.source">
                    {{ sourceLabel(ab.source) }}
                  </div>
                </div>
                <div class="azs-card-name">{{ ab.name }}</div>
                <div class="azs-card-size">{{ ab.width }}×{{ ab.height }}</div>
              </div>
              <div class="azs-check" :class="{ 'azs-checked': selectedArtboardIds.includes(ab.id) }">
                <span v-if="selectedArtboardIds.includes(ab.id)">✓</span>
              </div>
            </div>
          </div>

          <!-- 아트보드 감지 불가 (분석 완료 후 멀티존 없음) -->
          <div v-else class="azs-empty">
            <div class="azs-empty-icon">◻</div>
            <div class="azs-empty-text">멀티 아트보드를 감지하지 못했습니다.<br>전체 캔버스를 원본으로 사용합니다.</div>
          </div>

          <!-- 부족한 비율 경고 -->
          <div v-if="!psdLayerAnalyzing && detectedArtboards.length > 0 && missingRatioWarning" class="azs-warn">
            ⚠ {{ missingRatioWarning }}
          </div>
        </div>

        <!-- AI 객체 분석 섹션 -->
        <div v-if="isPsdFile && psdLayerAnalysis && !psdLayerAnalyzing" class="oa-section">
          <!-- 툴바 -->
          <div class="oa-toolbar">
            <div class="oa-toolbar-left">
              <span class="oa-title">AI 객체 분석</span>
              <span class="oa-beta">Beta</span>
              <select v-if="detectedArtboards.length > 0" v-model="objAnalysisArtboardId" class="oa-artboard-sel">
                <option v-for="ab in detectedArtboards" :key="ab.id" :value="ab.id">
                  {{ ab.name }} · {{ ab.width }}×{{ ab.height }}
                </option>
              </select>
            </div>
            <button class="oa-btn" :disabled="objAnalyzing" @click="runObjectAnalysis">
              {{ objAnalyzing ? '분석 중…' : 'AI 객체 분석' }}
            </button>
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
            <!-- 상태 바 -->
            <div class="oa-status-bar">
              <span class="oa-rf-badge" :class="objAnalysisResult.reflowReady ? 'rf-ok' : 'rf-ng'">
                {{ objAnalysisResult.reflowReady ? '✓ 재배치 준비 완료' : '✗ 재배치 준비 미완료' }}
              </span>
              <span v-if="!objAnalysisResult.reflowReady && objAnalysisResult.missingRequiredRoles?.length" class="oa-missing">
                누락: {{ objAnalysisResult.missingRequiredRoles.map(objRoleLabel).join(' · ') }}
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
                    :class="['oa-role-' + obj.role, 'oa-ms-' + obj.matchStatus, { 'oa-bbox-hl': hoveredObjId === obj.id }]"
                    :style="bboxOverlayStyle(obj.bbox)"
                    @mouseenter="hoveredObjId = obj.id"
                    @mouseleave="hoveredObjId = null"
                  >
                    <span class="oa-bbox-label">{{ objRoleLabel(obj.role) }}</span>
                  </div>
                </div>
              </div>

              <!-- 인스펙터 패널 -->
              <div class="oa-inspector">
                <div
                  v-for="obj in visibleObjects"
                  :key="'ins-' + obj.id"
                  class="oa-ins-card"
                  :class="{ 'oa-ins-hl': hoveredObjId === obj.id }"
                  @mouseenter="hoveredObjId = obj.id"
                  @mouseleave="hoveredObjId = null"
                >
                  <div class="oa-ins-row">
                    <span class="oa-role-dot" :class="'oa-dot-' + obj.role" />
                    <span class="oa-ins-role">{{ objRoleLabel(obj.role) }}</span>
                    <span class="oa-ins-label">{{ obj.label }}</span>
                    <span class="oa-imp-chip" :class="'imp-' + obj.importance">{{ objImportanceLabel(obj.importance) }}</span>
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
import { uploadPsd, listSpecs, analyzeBanner, analyzePsdLayers, analyzePsdObjects } from '../api/banner.js'
import { useRouter } from 'vue-router'
const router = useRouter()

const loading      = ref(false)
const result       = ref(null)
const aiAnalyzing  = ref(false)
const aiAnalysis   = ref(null)
const aiApplied    = ref(false)
const psdLayerAnalyzing = ref(false)
const psdLayerAnalysis  = ref(null)
const psdCanvas         = ref(null)   // ag-psd 렌더링된 캔버스 (아트보드 썸네일용)
const psdNativeW        = ref(0)
const psdNativeH        = ref(0)
const detectedArtboards = ref([])     // {id, name, width, height, artboardType, thumbnail}
const selectedArtboardIds = ref([])   // 사용자가 선택한 아트보드 ID 목록
const objAnalyzing        = ref(false)
const objAnalysisResult   = ref(null)
const objAnalysisError    = ref(null)
const objAnalysisArtboardId = ref(null)
const previewWrapRef      = ref(null)
const previewImgMeta      = ref(null)
const hoveredObjId        = ref(null)
const allSpecs     = ref([])
const specsLoading = ref(true)
const selectedSpecIds = ref([])
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
function removeSpec(id) { selectedSpecIds.value = selectedSpecIds.value.filter(x => x !== id) }

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

function clearFile() {
  form.psdFile = null
  previewUrl.value = null; previewError.value = false; previewSize.value = ''
  aiAnalysis.value = null; aiApplied.value = false
  psdLayerAnalysis.value = null; psdLayerAnalyzing.value = false
  psdCanvas.value = null; psdNativeW.value = 0; psdNativeH.value = 0
  detectedArtboards.value = []; selectedArtboardIds.value = []
  psdMode.value = 'artboard-first'
  objAnalyzing.value = false; objAnalysisResult.value = null; objAnalysisError.value = null
  objAnalysisArtboardId.value = null; previewImgMeta.value = null; hoveredObjId.value = null
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

// 드롭다운 기본값: detectedArtboards가 채워지면 첫 아이템으로 세팅
watch(detectedArtboards, (v) => {
  if (v.length > 0 && !objAnalysisArtboardId.value) {
    objAnalysisArtboardId.value = v[0].id
  }
})
// 결과 변경 시 이미지 메타 초기화
watch(objAnalysisResult, () => { previewImgMeta.value = null })

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

function onPreviewImgLoad(e) {
  const img = e.target
  previewImgMeta.value = {
    natW: img.naturalWidth,
    natH: img.naturalHeight,
    w: img.offsetWidth,
    h: img.offsetHeight,
  }
}

function bboxOverlayStyle(bbox) {
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

async function runObjectAnalysis() {
  if (!form.psdFile || !psdLayerAnalysis.value) return
  // 드롭다운에서 선택한 아트보드
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

const OBJ_MATCH_LABELS = {
  ready: '매칭 완료', matched_low_confidence: '낮은 신뢰도', missing_layer: '레이어 없음',
}
function objMatchLabel(s) { return OBJ_MATCH_LABELS[s] ?? (s || '') }

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
    // 레이어 재배치 불가 시 기본 모드로 전환
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
  artboard_tag: '아트보드',
  group_name: '그룹명',
  layer_bbox: '영역 추정',
  fallback: '전체 캔버스',
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
  fd.append('resizeMode', form.resizeMode)
  fd.append('smartFitStrength', form.smartFitStrength)
  fd.append('focalPosition', form.focalPosition)
  fd.append('outputFormat', form.outputFormat)
  if (isPsdFile.value) {
    fd.append('psdMode', psdMode.value || 'artboard-first')
    if (selectedArtboardIds.value.length > 0) {
      selectedArtboardIds.value.forEach(id => fd.append('selectedArtboardIds', id))
    }
  }
  if (aiAnalysis.value?.id) {
    fd.append('aiAnalysisId', aiAnalysis.value.id)
    fd.append('aiApplied', String(aiApplied.value))
    fd.append('aiRecommendedResizeMode', aiAnalysis.value.resizeMode ?? '')
    fd.append('aiRecommendedSmartFitStrength', aiAnalysis.value.smartFitStrength ?? '')
    fd.append('aiRecommendedFocalPosition', aiAnalysis.value.focalPosition ?? '')
  }
  if (isPsdFile.value && psdMode.value === 'object-reflow' && objAnalysisResult.value?.id) {
    fd.append('objectAnalysisId', objAnalysisResult.value.id)
    fd.append('objectReflowEnabled', 'true')
  }

  loading.value = true
  try {
    const { data } = await uploadPsd(fd)
    router.push(`/job/${data.id}`)
  } catch (e) {
    ElMessage.error('업로드 실패: ' + (e.response?.data?.message ?? e.message))
  } finally { loading.value = false }
}

onMounted(async () => {
  try {
    const { data } = await listSpecs()
    allSpecs.value = data
    for (const s of data) { if (!(s.media in expandedPlatforms)) expandedPlatforms[s.media] = false }
  } catch { ElMessage.error('규격 로딩 실패') }
  finally { specsLoading.value = false }
})
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

/* ── PSD 아트보드 선택 섹션 ── */
.azs-section {
  margin: 0 0 24px;
  padding: 20px 24px;
  background: #F9FAFB;
  border-radius: 14px;
  border: 1px solid #E5E8EB;
}
.azs-header {
  display: flex; align-items: flex-start; justify-content: space-between;
  margin-bottom: 16px;
}
.azs-title { font-size: 15px; font-weight: 700; color: #1A1D27; margin-bottom: 3px; }
.azs-sub   { font-size: 12px; color: #6B7684; }
.azs-select-all {
  font-size: 11px; font-weight: 600; color: #7C3AED;
  background: none; border: 1px solid #DDD6FE; border-radius: 6px;
  padding: 4px 10px; cursor: pointer; white-space: nowrap; font-family: inherit;
  flex-shrink: 0; margin-top: 2px;
}
.azs-select-all:hover { background: #F5F3FF; }
.azs-cards {
  display: flex; flex-wrap: wrap; gap: 12px;
}
.azs-card {
  position: relative; cursor: pointer;
  border: 2px solid #E5E8EB; border-radius: 12px;
  background: #fff; overflow: hidden;
  transition: border-color 0.15s, box-shadow 0.15s;
  flex: 0 0 calc(33.333% - 8px);
  min-width: 120px;
  display: flex; flex-direction: column;
}
.azs-card:hover { border-color: #C4B5FD; box-shadow: 0 2px 8px rgba(124,58,237,0.1); }
.azs-selected { border-color: #7C3AED !important; box-shadow: 0 0 0 3px rgba(124,58,237,0.12) !important; }
.azs-thumb-wrap {
  width: 100%; height: 110px; flex-shrink: 0;
  overflow: hidden; background: #F3F4F6; display: flex; align-items: center; justify-content: center;
}
.azs-thumb { max-width: 100%; max-height: 100%; object-fit: contain; display: block; }
.azs-thumb-placeholder {
  font-size: 11px; color: #9CA3AF; font-weight: 600; padding: 8px;
}
.azs-card-body { padding: 8px 10px 10px; }
.azs-card-badges { display: flex; gap: 4px; flex-wrap: wrap; }
.azs-card-name { font-size: 11px; color: #374151; font-weight: 500; margin: 4px 0 2px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.azs-card-size { font-size: 10px; color: #9CA3AF; }
.azs-type-badge {
  display: inline-block; font-size: 10px; font-weight: 700; padding: 1px 6px;
  border-radius: 4px; margin-bottom: 2px;
}
.azst-square     { background: #ECFDF5; color: #065F46; }
.azst-vertical   { background: #EFF6FF; color: #1E40AF; }
.azst-horizontal { background: #FFFBEB; color: #92400E; }
.azst-custom, .azst-unknown { background: #F3F4F6; color: #6B7684; }
/* source badge */
.azs-source-badge {
  display: inline-block; font-size: 9.5px; font-weight: 600; padding: 1px 5px;
  border-radius: 4px; background: #F1F5F9; color: #64748B;
}
.azss-artboard_tag { background: #EFF6FF; color: #3B82F6; }
.azss-group_name   { background: #F0FDF4; color: #16A34A; }
.azss-layer_bbox   { background: #FFF7ED; color: #EA580C; }
/* empty state */
.azs-empty {
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  padding: 24px 16px; background: #F9FAFB; border-radius: 10px; gap: 6px;
}
.azs-empty-icon { font-size: 24px; color: #D1D5DB; }
.azs-empty-text { font-size: 12px; color: #9CA3AF; text-align: center; line-height: 1.6; }
.azs-check {
  position: absolute; top: 6px; right: 6px;
  width: 20px; height: 20px; border-radius: 50%;
  border: 2px solid #D1D5DB; background: #fff;
  display: flex; align-items: center; justify-content: center;
  font-size: 11px; font-weight: 700; color: #fff;
  transition: background 0.15s, border-color 0.15s;
}
.azs-checked { background: #7C3AED !important; border-color: #7C3AED !important; }
.azs-warn {
  margin-top: 12px; padding: 10px 12px;
  background: #FFFBEB; border: 1px solid #FDE68A; border-radius: 8px;
  font-size: 12px; color: #92400E; line-height: 1.5;
}
/* skeleton */
.azs-card-skeleton { pointer-events: none; }
.azs-thumb-skeleton {
  width: 100%; aspect-ratio: 1/1; background: linear-gradient(90deg,#F3F4F6 25%,#E5E7EB 50%,#F3F4F6 75%);
  background-size: 200% 100%; animation: azs-shimmer 1.4s infinite;
}
.azs-skeleton-line {
  height: 10px; border-radius: 5px; margin: 0 10px;
  background: linear-gradient(90deg,#F3F4F6 25%,#E5E7EB 50%,#F3F4F6 75%);
  background-size: 200% 100%; animation: azs-shimmer 1.4s infinite;
}
@keyframes azs-shimmer { 0%{background-position:200% 0} 100%{background-position:-200% 0} }

/* ── AI 객체 분석 섹션 ─────────────────────────────────────── */
.oa-section {
  border: 1.5px solid #E5E7EB; border-radius: 12px;
  background: #F8FAFC; padding: 11px 13px; margin-bottom: 12px;
}
/* 툴바 */
.oa-toolbar { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.oa-toolbar-left { display: flex; align-items: center; gap: 6px; flex: 1; min-width: 0; }
.oa-title { font-size: 12px; font-weight: 700; color: #1F2937; }
.oa-beta {
  font-size: 9px; font-weight: 700; padding: 1px 5px; border-radius: 4px;
  background: #EDE9FE; color: #7C3AED; flex-shrink: 0;
}
.oa-artboard-sel {
  font-size: 11px; padding: 3px 6px; border-radius: 6px; font-family: inherit;
  border: 1px solid #DDE0E7; background: #fff; color: #374151;
  max-width: 180px; cursor: pointer; flex-shrink: 1; min-width: 0;
}
.oa-btn {
  font-size: 11px; font-weight: 600; padding: 5px 12px; border-radius: 7px;
  background: linear-gradient(135deg, #7C3AED, #3B82F6); color: #fff; border: none;
  cursor: pointer; white-space: nowrap; transition: opacity 0.15s; flex-shrink: 0;
}
.oa-btn:disabled { opacity: 0.5; cursor: not-allowed; }
/* 로딩 */
.oa-loading { display: flex; align-items: center; gap: 8px; font-size: 11px; color: #6B7280; padding: 8px 0; }
.oa-spinner {
  width: 14px; height: 14px; border: 2px solid #E5E7EB;
  border-top-color: #7C3AED; border-radius: 50%; animation: oa-spin 0.7s linear infinite; flex-shrink: 0;
}
@keyframes oa-spin { to { transform: rotate(360deg); } }
/* 에러 */
.oa-error { font-size: 11px; color: #DC2626; background: #FEF2F2; border-radius: 6px; padding: 7px 10px; }
/* 상태 바 */
.oa-status-bar {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  padding: 5px 8px; border-radius: 7px; background: #F1F5F9; margin-bottom: 9px;
}
.oa-rf-badge { font-size: 10px; font-weight: 700; padding: 2px 8px; border-radius: 10px; white-space: nowrap; }
.rf-ok { background: #D1FAE5; color: #065F46; }
.rf-ng { background: #FEE2E2; color: #991B1B; }
.oa-missing { font-size: 10px; color: #92400E; }
/* 본문 레이아웃 */
.oa-body { display: flex; gap: 10px; align-items: flex-start; }
/* 프리뷰 패널 */
.oa-preview-panel { flex: 0 0 55%; min-width: 0; }
.oa-preview-wrap {
  position: relative; width: 100%; height: 280px;
  background: #111; border-radius: 8px; overflow: hidden;
  display: flex; align-items: center; justify-content: center;
}
.oa-preview-img { width: 100%; height: 100%; object-fit: contain; display: block; }
/* bbox 오버레이 — JS로 px 기반 위치 계산 */
.oa-bbox {
  position: absolute; border: 2px solid; border-radius: 2px;
  box-sizing: border-box; pointer-events: auto; cursor: default;
  transition: box-shadow 0.12s;
}
.oa-bbox-hl { box-shadow: 0 0 0 2px rgba(255,255,255,0.5); z-index: 10; }
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
/* 매칭 상태 투명도 */
.oa-ms-ready { opacity: 1; }
.oa-ms-matched_low_confidence { opacity: 0.75; }
.oa-ms-missing_layer { opacity: 0.35; border-style: dashed; }
/* 인스펙터 패널 */
.oa-inspector {
  flex: 1; min-width: 0;
  max-height: 280px; overflow-y: auto;
  display: flex; flex-direction: column; gap: 3px;
}
.oa-inspector::-webkit-scrollbar { width: 4px; }
.oa-inspector::-webkit-scrollbar-track { background: transparent; }
.oa-inspector::-webkit-scrollbar-thumb { background: #DDE0E7; border-radius: 2px; }
/* 인스펙터 카드 */
.oa-ins-card {
  border: 1px solid #E5E7EB; border-radius: 7px;
  padding: 5px 8px; background: #fff; cursor: default; transition: border-color 0.1s;
}
.oa-ins-hl { border-color: #A78BFA !important; background: #F5F3FF !important; }
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
/* 기타 접기 */
.oa-ins-more { margin-top: 2px; }
.oa-ins-more-lbl { font-size: 10px; color: #9CA3AF; cursor: pointer; padding: 3px 0; list-style: none; }
.oa-ins-more summary::-webkit-details-marker { display: none; }

@media (prefers-color-scheme: dark) {
  .oa-section { background: #1A1D27; border-color: #2D3142; }
  .oa-title { color: #F9FAFB; }
  .oa-artboard-sel { background: #252836; border-color: #374151; color: #E5E7EB; }
  .oa-status-bar { background: #252836; }
  .oa-preview-wrap { background: #0D0D0D; }
  .oa-ins-card { background: #252836; border-color: #374151; }
  .oa-ins-hl { border-color: #7C3AED !important; background: #2D1F4A !important; }
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
  .psd-mode-option-warn { border-color: #92400E !important; background: #451A03; }
  .psd-mode-option-warn.on { border-color: #D97706 !important; background: #78350F; }
  .psd-mode-option-warn .psd-mode-desc { color: #FCD34D; }
}

@media (prefers-color-scheme: dark) {
  .azs-section { background: #1A1D27; border-color: #2D3142; }
  .azs-title { color: #F9FAFB; }
  .azs-sub { color: #9CA3AF; }
  .azs-card { background: #252836; border-color: #374151; }
  .azs-card:hover { border-color: #7C3AED; }
  .azs-thumb-wrap { background: #1F2937; }
  .azs-card-name { color: #E5E7EB; }
  .azs-warn { background: #451A03; border-color: #92400E; color: #FCD34D; }
  .azs-empty { background: #1A1D27; }
  .azs-empty-text { color: #6B7280; }
  .azs-thumb-skeleton,.azs-skeleton-line { background: linear-gradient(90deg,#1F2937 25%,#374151 50%,#1F2937 75%); background-size:200% 100%; }
}

/* PSD 처리 방식 */
.psd-mode-section { margin: 10px 0 8px; }
.psd-mode-title { font-size: 11px; font-weight: 700; color: #6B7684; text-transform: uppercase; letter-spacing: 0.4px; margin-bottom: 6px; }
.psd-mode-options { display: flex; flex-direction: column; gap: 5px; }
.psd-mode-option {
  border: 1.5px solid #EAEDF0; border-radius: 8px;
  padding: 8px 10px; cursor: pointer; transition: all 0.12s;
  display: block;
}
.psd-mode-option:hover:not(.psd-mode-disabled) { border-color: #C4B5FD; background: #FAFAFF; }
.psd-mode-option.on { border-color: #7C3AED; background: #F5F0FF; }
.psd-mode-disabled { cursor: default; opacity: 0.5; }
.psd-mode-body { pointer-events: none; }
.psd-mode-option:not(.psd-mode-disabled) .psd-mode-body { pointer-events: auto; cursor: pointer; }
.psd-mode-name { font-size: 12px; font-weight: 600; color: #333D4B; display: flex; align-items: center; gap: 5px; margin-bottom: 2px; }
.psd-mode-desc { font-size: 10.5px; color: #8B95A1; line-height: 1.4; }
.psd-mode-badge {
  font-size: 9.5px; font-weight: 700; padding: 1px 5px; border-radius: 4px;
  background: #7C3AED; color: #fff;
}
.psd-mode-beta {
  background: #0891B2; color: #fff;
}
.psd-mode-soon {
  font-size: 9.5px; font-weight: 600; padding: 1px 5px; border-radius: 4px;
  background: #F2F4F6; color: #8B95A1;
}
.psd-mode-option.disabled { opacity: 0.5; cursor: not-allowed; }
.psd-mode-option.disabled .psd-mode-body { pointer-events: none; }
.psd-mode-option-warn { border-color: #FDE68A !important; background: #FFFBEB; }
.psd-mode-option-warn.on { border-color: #D97706 !important; background: #FEF3C7; }
.psd-mode-option-warn .psd-mode-desc { color: #92400E; }

/* PSD 호환성 진단 */
.psd-compat-loading { font-size: 10px; color: #8B95A1; font-weight: 400; margin-left: 6px; display: inline-flex; align-items: center; gap: 4px; }
.psd-compat-status { margin-bottom: 8px; }
.psd-compat-row {
  display: flex; align-items: flex-start; gap: 7px;
  padding: 7px 9px; border-radius: 7px; font-size: 11px; margin-bottom: 4px;
}
.psd-compat-ok  { background: #F0FFF4; border: 1px solid #D1FAE5; }
.psd-compat-warn { background: #FFFBEB; border: 1px solid #FDE68A; }
.psd-compat-error { background: #FFF5F5; border: 1px solid #FED7D7; }
.psd-compat-icon { font-size: 12px; margin-top: 1px; flex-shrink: 0; }
.psd-compat-ok .psd-compat-icon  { color: #22C55E; }
.psd-compat-warn .psd-compat-icon { color: #D97706; }
.psd-compat-error .psd-compat-icon { color: #EF4444; }
.psd-compat-label { font-weight: 600; color: #333D4B; }
.psd-compat-reason { color: #6B7684; font-size: 10px; margin-top: 1px; }
.psd-compat-hint  { color: #8B95A1; font-size: 10px; margin-top: 2px; line-height: 1.4; }

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

/* list mode */
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
  border: 1px solid #EAEDF0; overflow: hidden; height: 130px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.04); transition: box-shadow 0.12s;
}
.hcard:hover { box-shadow: 0 4px 12px rgba(0,0,0,0.08); }

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
</style>
