<template>
  <div class="detail-wrap">
    <!-- 헤더 바 -->
    <div class="top-bar">
      <button class="back-btn" @click="router.push('/jobs')">
        <span>←</span> 작업 목록
      </button>
      <div class="top-actions" v-if="job && job.status === 'done'">
        <button class="btn-zip" @click="handleZipDownload">
          ↓ ZIP 전체 다운로드
        </button>
      </div>
    </div>

    <!-- 로딩 -->
    <div v-if="!job" class="center-state">
      <div class="spinner" />
      <p>불러오는 중...</p>
    </div>

    <!-- 본문 -->
    <template v-else>
      <!-- 작업 정보 카드 -->
      <div class="info-card">
        <div class="info-left">
          <div class="job-title">{{ job.advertiser }} — {{ job.campaignName }}</div>
          <div class="job-meta">
            <span class="badge-media" v-for="m in job.targetMedia" :key="m">{{ mediaLabel(m) }}</span>
            <span class="meta-item">{{ job.resizeMode }}</span>
            <span class="meta-item">{{ job.outputFormat?.toUpperCase() }}</span>
            <span class="meta-item">{{ fmtDate(job.createdAt) }}</span>
          </div>
        </div>
        <div class="status-area">
          <span class="status-badge" :class="job.status">{{ statusLabel(job.status) }}</span>
          <div v-if="isPolling" class="polling-hint">처리 중... 자동 갱신</div>
        </div>
      </div>

      <!-- 처리 중 -->
      <div v-if="job.status === 'pending' || job.status === 'processing'" class="center-state">
        <div class="spinner" />
        <p class="poll-msg">{{ job.status === 'pending' ? '대기 중입니다...' : '배너를 생성하고 있습니다...' }}</p>
        <p class="poll-sub">완료되면 자동으로 결과가 표시됩니다.</p>
      </div>

      <!-- 실패 (results 없을 때) -->
      <div v-else-if="job.status === 'fail' && !job.results?.length" class="fail-box">
        <div class="fail-icon">✕</div>
        <div class="fail-title">생성 실패</div>
        <div class="fail-msg">{{ job.errorMessage || '알 수 없는 오류가 발생했습니다.' }}</div>
      </div>

      <!-- 완료 또는 fail이지만 results 있을 때 -->
      <div v-else-if="job.status === 'done' || job.results?.length" class="results-area">
        <!-- fail 상태 오류 배너 -->
        <div v-if="job.status === 'fail'" class="fail-banner">
          <span class="fail-banner-icon">✕</span>
          <div>
            <div class="fail-banner-title">작업 실패</div>
            <div class="fail-banner-msg">{{ job.errorMessage }}</div>
          </div>
        </div>
        <div class="results-header">
          <span class="results-count">{{ job.results?.length || 0 }}개 배너 생성 완료</span>
        </div>
        <div class="img-grid">
          <div class="img-card" v-for="r in job.results" :key="r.fileName" :class="{ invalid: r.valid === false }">
            <div class="img-thumb" :style="thumbStyle(r)">
              <img
                :src="resultPreviewUrl(r)"
                :alt="r.name"
                @error="onImgError($event)"
              />
            </div>
            <div class="img-info">
              <div class="img-name">{{ r.name || r.slug }}</div>
              <div class="img-meta">
                <span>{{ r.width }} × {{ r.height }}</span>
                <span v-if="r.fileSize"> · {{ fmtSize(r.fileSize) }}</span>
                <template v-if="r.actualPsdRenderMode === 'layer-reflow'">
                  <span class="mode-badge-layer"> · PSD 레이어 재배치</span>
                </template>
                <template v-else-if="r.layerReflowAttempted && !r.layerReflowSucceeded">
                  <span class="mode-badge-fallback"> · PSD 대체 렌더링</span>
                </template>
                <template v-else-if="job.resizeMode">
                  <span class="mode-badge"> · {{ job.resizeMode }}{{ job.resizeMode === 'smart-fit' && job.smartFitStrength ? ' / ' + strengthLabel[job.smartFitStrength] : '' }}</span>
                </template>
                <span class="valid-badge" :class="resultStatusClass(r)">
                  {{ resultStatusLabel(r) }}
                </span>
              </div>
              <div class="valid-msg" v-if="r.valid === false">{{ r.validationMessage }}</div>
              <div v-if="r.selectedArtboardName" class="artboard-badge">
                ▣ PSD 아트보드: {{ r.selectedArtboardName }}
              </div>
              <div v-if="r.actualPsdRenderMode === 'layer-reflow'" class="layer-reflow-badge">
                ⊞ PSD 레이어 재배치
                <span v-if="r.layerReflowTemplate" class="reflow-template">{{ r.layerReflowTemplate }}</span>
              </div>
              <div v-if="r.usedLayerRoles && r.usedLayerRoles.length" class="reflow-roles">
                <span v-for="role in r.usedLayerRoles" :key="role" class="reflow-role-tag">{{ roleLabel(role) }}</span>
              </div>
              <div v-if="r.layerReflowAttempted && !r.layerReflowSucceeded" class="fallback-reason">
                레이어 재배치 실패: {{ r.layerReflowError || '알 수 없는 이유로 대체 렌더링 처리됨' }}
              </div>
              <div v-if="r.renderSource && r.renderSource !== 'unknown'" class="render-source-badge" :class="renderSourceClass(r.renderSource)">
                {{ renderSourceLabel(r.renderSource) }}
              </div>
              <div v-if="r.fallbackUsed" class="fallback-notice">
                ⚠ PSD 호환 문제로 이미지 기반 리사이징이 적용되었습니다.
              </div>
              <!-- 4차-4: wide-banner-smart-fit 뱃지 -->
              <div v-if="r.resizeStrategy === 'wide-banner-smart-fit'" class="wide-banner-badge">
                ◈ AI 스마트 맞춤
                <span class="wide-banner-candidate">{{ candidateTypeLabel(r.candidateType) }}</span>
                <span v-if="r.candidateScore != null"
                      class="wide-banner-score"
                      :class="qualityScoreClass(r.candidateScore)">
                  {{ r.candidateScore }}점
                </span>
                <span v-if="r.qualityLabel"
                      class="quality-label-badge"
                      :class="qualityLabelClass(r.qualityLabel)">
                  {{ r.qualityLabel }}
                </span>
                <span v-if="r.qualityGate" class="quality-gate-badge">
                  ⚠ 품질 게이트
                </span>
              </div>
              <!-- 4차-5: Layer Reflow 품질 뱃지 -->
              <div v-if="r.safeZonePass != null" class="safe-zone-badge" :class="r.safeZonePass ? 'safe-zone-ok' : 'safe-zone-fail'">
                {{ r.safeZonePass ? '✓ 세이프존 통과' : '✕ 세이프존 미달' }}
              </div>
              <div v-if="r.requiredLayerMissing" class="required-missing-badge">
                ⚠ 필수 레이어 부족
              </div>
              <div v-if="r.aiCompareApplied" class="ai-applied-badge">
                ✦ AI 후보 적용됨 · {{ strengthKr(r.selectedCandidate) }}
              </div>
            </div>
            <div class="card-actions">
              <button class="btn-dl-single" @click="handleSingleDownload(r)">↓ 다운로드</button>
              <button v-if="job.resizeMode === 'smart-fit' && r.specId"
                class="btn-compare"
                :disabled="compareLoading[r.specId]"
                @click="runCompare(r.specId)">
                <span v-if="compareLoading[r.specId]" class="spin-xs" />
                <span v-else>✦</span>
                {{ compareLoading[r.specId] ? '비교 중...' : 'AI 비교' }}
              </button>
            </div>
          </div>
        </div>
      </div>
    </template>

    <!-- AI 후보 비교 모달 -->
    <div v-if="compareModal" class="cmp-overlay" @click.self="compareModal = false">
      <div class="cmp-modal">
        <div class="cmp-header">
          <span class="cmp-star">✦</span> AI 후보 비교 결과
          <span class="cmp-best-badge">추천: {{ strengthKr(compareResult?.bestCandidate) }} {{ compareResult?.bestScore }}점</span>
          <button class="cmp-close" @click="compareModal = false">✕</button>
        </div>
        <div class="cmp-summary">{{ compareResult?.summary }}</div>
        <div class="cmp-candidates">
          <div v-for="c in compareResult?.candidates" :key="c.strength"
            class="cmp-card" :class="{ best: c.strength === compareResult?.bestCandidate }">
            <div class="cmp-card-head">
              <span class="cmp-strength">{{ strengthKr(c.strength) }}</span>
              <span class="cmp-score" :class="scoreClass(c.score)">{{ c.score }}점</span>
              <span v-if="c.strength === compareResult?.bestCandidate" class="cmp-crown">★ 추천</span>
            </div>
            <div class="cmp-tags" v-if="strengthTags(c.strength).length">
              <span v-for="tag in strengthTags(c.strength)" :key="tag" class="cmp-tag">{{ tag }}</span>
            </div>
            <div class="cmp-desc" v-if="strengthDesc(c.strength)">{{ strengthDesc(c.strength) }}</div>
            <div class="cmp-thumb-wrap">
              <img :src="compareFileUrl(compareResult.id, c.fileName)" class="cmp-thumb" :alt="c.strength" @error="$event.target.style.display='none'" />
            </div>
            <!-- 요소 보존 평가 (3.5차) -->
            <div v-if="c.preservedRequiredGroups?.length || c.lostRequiredGroups?.length || c.preservedPriorityGroups?.length || c.lostPriorityGroups?.length" class="cmp-element-section">
              <div v-if="c.preservedRequiredGroups?.length" class="cmp-el-row">
                <span class="cmp-el-label cmp-el-preserved-req">✓ 필수 유지</span>
                <div class="cmp-el-tags">
                  <span v-for="g in c.preservedRequiredGroups" :key="g" class="cmp-el-tag cmp-el-tag-preserved-req">{{ groupLabel(g) }}</span>
                </div>
              </div>
              <div v-if="c.lostRequiredGroups?.length" class="cmp-el-row">
                <span class="cmp-el-label cmp-el-lost-req">✕ 필수 손실</span>
                <div class="cmp-el-tags">
                  <span v-for="g in c.lostRequiredGroups" :key="g" class="cmp-el-tag cmp-el-tag-lost-req">{{ groupLabel(g) }}</span>
                </div>
              </div>
              <div v-if="c.preservedPriorityGroups?.length" class="cmp-el-row">
                <span class="cmp-el-label cmp-el-preserved-pri">△ 우선 유지</span>
                <div class="cmp-el-tags">
                  <span v-for="g in c.preservedPriorityGroups" :key="g" class="cmp-el-tag cmp-el-tag-preserved-pri">{{ groupLabel(g) }}</span>
                </div>
              </div>
              <div v-if="c.lostPriorityGroups?.length" class="cmp-el-row">
                <span class="cmp-el-label cmp-el-lost-pri">△ 우선 손실</span>
                <div class="cmp-el-tags">
                  <span v-for="g in c.lostPriorityGroups" :key="g" class="cmp-el-tag cmp-el-tag-lost-pri">{{ groupLabel(g) }}</span>
                </div>
              </div>
            </div>
            <div v-if="c.pros?.length" class="cmp-pros">
              <div v-for="p in c.pros" :key="p" class="cmp-pro-item">✓ {{ p }}</div>
            </div>
            <div v-if="c.cons?.length" class="cmp-cons">
              <div v-for="n in c.cons" :key="n" class="cmp-con-item">✕ {{ n }}</div>
            </div>
            <div class="cmp-apply-area">
              <button class="btn-cmp-apply"
                :class="{ 'btn-cmp-apply-best': c.strength === compareResult?.bestCandidate }"
                :disabled="applyLoading[`${compareResult?.specId}_${c.strength}`]"
                @click="runApply(c.strength)">
                <span v-if="applyLoading[`${compareResult?.specId}_${c.strength}`]" class="spin-xs" />
                {{ c.strength === compareResult?.bestCandidate ? '★ 추천 적용' : '이 후보 적용' }}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { getJob, downloadZip, downloadImage, previewUrl, compareJob, compareFileUrl, applyCompare } from '../api/banner.js'
import { ElMessage } from 'element-plus'

const route = useRoute()
const router = useRouter()
const job = ref(null)
const isPolling = ref(false)
let pollTimer = null

// AI 후보 비교
const compareLoading = ref({})   // { specId: true/false }
const compareModal = ref(false)
const compareResult = ref(null)

// AI 후보 적용
const applyLoading = ref({})     // { specId_candidate: true/false }

const strengthLabel = { safe: '안전', balanced: '균형', fill: '채움', 'center-crop': '비율 크롭', letterbox: '전체 보존', 'focus-fill': 'AI 포커스 채움', 'object-aware-fit': '오브젝트 보호 맞춤', 'poster-reflow': 'AI 포스터 재구성' }

const MEDIA_LABELS = {
  google: 'Google', meta: 'Meta', naver: 'Naver',
  kakao: 'Kakao', linkedin: 'LinkedIn', tiktok: 'TikTok',
}
const STATUS_LABELS = {
  pending: '대기', processing: '처리 중', done: '완료', fail: '실패',
}

const mediaLabel = (m) => MEDIA_LABELS[m] || m
const statusLabel = (s) => STATUS_LABELS[s] || s

function fmtDate(dt) {
  if (!dt) return ''
  const d = new Date(dt)
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  const hh = d.getHours()
  const min = String(d.getMinutes()).padStart(2, '0')
  const ampm = hh < 12 ? '오전' : '오후'
  const h12 = hh % 12 || 12
  return `${mm}.${dd}. ${ampm} ${h12}:${min}`
}

function thumbStyle(r) {
  const ratio = r.width / r.height
  return ratio > 1.5 ? { paddingBottom: '52%' }
       : ratio < 0.7 ? { paddingBottom: '130%' }
       : { paddingBottom: '100%' }
}

function getPreviewUrl(fileName) {
  return previewUrl(route.params.id, fileName)
}

function resultPreviewUrl(r) {
  const base = previewUrl(route.params.id, r.fileName)
  const version = r.aiCompareApplied
    ? `${r.selectedCompareId || ''}-${r.selectedCandidate || ''}`
    : job.value?.updatedAt || ''
  return version ? `${base}?v=${encodeURIComponent(version)}` : base
}

function fmtSize(bytes) {
  if (!bytes) return ''
  if (bytes >= 1024 * 1024) return (bytes / 1024 / 1024).toFixed(1) + 'MB'
  return Math.round(bytes / 1024) + 'KB'
}

function onImgError(e) {
  e.target.style.display = 'none'
  e.target.parentElement.classList.add('no-img')
}

async function loadJob() {
  try {
    const res = await getJob(route.params.id)
    job.value = res.data
    if (job.value.status === 'pending' || job.value.status === 'processing') {
      startPolling()
    } else {
      stopPolling()
    }
  } catch {
    job.value = { status: 'fail', errorMessage: '작업을 불러올 수 없습니다.' }
  }
}

function startPolling() {
  if (isPolling.value) return
  isPolling.value = true
  pollTimer = setInterval(async () => {
    try {
      const res = await getJob(route.params.id)
      job.value = res.data
      if (job.value.status !== 'pending' && job.value.status !== 'processing') {
        stopPolling()
      }
    } catch {}
  }, 2500)
}

function stopPolling() {
  isPolling.value = false
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
}

async function handleZipDownload() {
  try {
    const res = await downloadZip(route.params.id)
    const url = URL.createObjectURL(res.data)
    const a = document.createElement('a')
    a.href = url
    a.download = `${job.value.advertiser}_${job.value.campaignName}.zip`
    a.click()
    URL.revokeObjectURL(url)
  } catch { alert('다운로드에 실패했습니다.') }
}

async function handleSingleDownload(r) {
  try {
    const res = await downloadImage(route.params.id, r.fileName)
    const url = URL.createObjectURL(res.data)
    const a = document.createElement('a')
    a.href = url
    a.download = r.fileName
    a.click()
    URL.revokeObjectURL(url)
  } catch { alert('다운로드에 실패했습니다.') }
}

function getSpecIdFromResult(r) {
  return r.specId ?? ''
}

function scoreClass(score) {
  if (score >= 85) return 'score-high'
  if (score >= 70) return 'score-mid'
  return 'score-low'
}

async function runCompare(specId) {
  if (compareLoading.value[specId]) return
  compareLoading.value[specId] = true
  try {
    const { data } = await compareJob(route.params.id, specId)
    compareResult.value = data
    compareModal.value = true
  } catch (e) {
    ElMessage.error('AI 비교 실패: ' + (e.response?.data?.message ?? e.message))
  } finally {
    compareLoading.value[specId] = false
  }
}

const STRENGTH_KR = { safe: '안전', balanced: '균형', fill: '채움', 'center-crop': '비율 크롭', letterbox: '전체 보존', 'focus-fill': 'AI 포커스 채움', 'object-aware-fit': '오브젝트 보호 맞춤', 'poster-reflow': 'AI 포스터 재구성' }
function strengthKr(v) { return STRENGTH_KR[v] ?? v }

const STRENGTH_DESC = {
  'object-aware-fit': '중요 요소를 보존하면서 최대한 크게 맞춤',
  'poster-reflow': '포스터를 배너 규격에 맞게 재구성',
  'focus-fill': 'AI가 감지한 핵심 피사체 중심으로 채움',
  'letterbox': '원본을 절대 자르지 않고 전체 보존',
  'center-crop': '목표 규격을 완전히 채우도록 중앙 crop',
}
function strengthDesc(v) { return STRENGTH_DESC[v] ?? '' }

const STRENGTH_TAGS = {
  'object-aware-fit': ['보존형', '안전형', '중요 요소 유지'],
  'poster-reflow': ['재구성형', '공격형', '일부 생략 가능'],
  'letterbox': ['무손실', '여백 발생'],
  'center-crop': ['꽉 채움', '가장자리 손실 위험'],
  'focus-fill': ['피사체 중심', 'AI 크롭'],
}
function strengthTags(v) { return STRENGTH_TAGS[v] ?? [] }

const GROUP_LABELS = {
  main_product: '메인 제품', main_copy: '메인 카피', sub_copy: '서브 카피',
  price_discount: '가격/할인', cta: 'CTA', logo: '로고', decorations: '장식', background: '배경',
}
function groupLabel(gid) { return GROUP_LABELS[gid] ?? gid }

const PSD_RENDER_MODE_LABELS = {
  'artboard': '아트보드',
  'full-canvas': '전체 캔버스 fallback',
  'imagemagick-flatten': 'ImageMagick fallback',
  'failed': '렌더링 실패',
}
function psdRenderModeLabel(mode) { return PSD_RENDER_MODE_LABELS[mode] ?? mode }

const RENDER_SOURCE_LABELS = {
  'psd_tools_composite': 'PSD 원본 렌더링',
  'psd_layer_reflow': 'PSD 레이어 재배치',
  'imagemagick_magick_first_page': 'ImageMagick 합성 (IM7)',
  'imagemagick_convert_first_page': 'ImageMagick 합성 (IM6)',
  'imagemagick_flatten': 'ImageMagick flatten',
  'pillow_image': '이미지 기반 리사이징',
}
function renderSourceLabel(src) { return RENDER_SOURCE_LABELS[src] ?? src }
function renderSourceClass(src) {
  if (src === 'psd_tools_composite' || src === 'psd_layer_reflow') return 'render-source-ok'
  if (src === 'pillow_image') return 'render-source-neutral'
  if (src.startsWith('imagemagick')) return 'render-source-fallback'
  return ''
}

const ROLE_LABELS = {
  background: '배경', logo: '로고', headline: '메인 카피',
  subcopy: '보조 문구', cta: 'CTA', product: '제품',
  person: '인물', visual: '비주얼', decoration: '장식',
  badge: '배지', price: '가격',
}
function roleLabel(role) { return ROLE_LABELS[role] ?? role }

const CANDIDATE_TYPE_LABELS = {
  safe:          '안전형',
  balanced:      '균형형',
  fill:          '채움형',
  'focus-crop':  '포커스 크롭',
}
function candidateTypeLabel(t) { return CANDIDATE_TYPE_LABELS[t] ?? (t || '') }

function qualityScoreClass(score) {
  if (score == null) return ''
  if (score >= 70) return 'score-good'
  if (score >= 50) return 'score-warn'
  return 'score-bad'
}
function qualityLabelClass(label) {
  if (label === '정상')    return 'ql-good'
  if (label === '주의')    return 'ql-warn'
  return 'ql-bad'
}

// 결과 카드 상태 뱃지: qualityLabel 우선, 없으면 valid 기준
function resultStatusLabel(r) {
  if (r.valid === false) return '규격 불일치'
  if (r.qualityLabel)   return r.qualityLabel
  return '정상'
}
function resultStatusClass(r) {
  if (r.valid === false)             return 'invalid'
  if (r.qualityLabel === '주의')     return 'warn'
  if (r.qualityLabel === '품질 낮음') return 'quality-bad'
  return 'ok'
}

async function runApply(candidate) {
  if (!compareResult.value) return
  const key = `${compareResult.value.specId}_${candidate}`
  if (applyLoading.value[key]) return
  applyLoading.value[key] = true
  try {
    await applyCompare(route.params.id, compareResult.value.id, compareResult.value.specId, candidate)
    compareModal.value = false
    await loadJob()
    ElMessage.success(`${strengthKr(candidate)} 후보가 적용되었습니다.`)
  } catch (e) {
    ElMessage.error('적용 실패: ' + (e.response?.data?.message ?? e.message))
  } finally {
    applyLoading.value[key] = false
  }
}

onMounted(loadJob)
onUnmounted(stopPolling)
</script>

<style scoped>
.detail-wrap {
  max-width: 1200px;
  margin: 0 auto;
  padding: 24px 28px 60px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

/* 상단 바 */
.top-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.back-btn {
  background: none;
  border: none;
  cursor: pointer;
  color: #6B7280;
  font-size: 14px;
  padding: 6px 0;
  display: flex;
  align-items: center;
  gap: 6px;
}
.back-btn:hover { color: #111; }
.btn-zip {
  background: #7C3AED;
  color: #fff;
  border: none;
  border-radius: 8px;
  padding: 9px 20px;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
}
.btn-zip:hover { background: #6D28D9; }

/* 작업 정보 카드 */
.info-card {
  background: #fff;
  border-radius: 12px;
  border: 1px solid #E5E7EB;
  padding: 20px 24px;
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}
.job-title {
  font-size: 17px;
  font-weight: 700;
  color: #111827;
  margin-bottom: 10px;
}
.job-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.badge-media {
  background: #EDE9FE;
  color: #6D28D9;
  padding: 2px 10px;
  border-radius: 20px;
  font-size: 12px;
  font-weight: 600;
}
.meta-item {
  font-size: 12px;
  color: #6B7280;
  background: #F3F4F6;
  padding: 2px 8px;
  border-radius: 6px;
}
.status-area { display: flex; flex-direction: column; align-items: flex-end; gap: 6px; }
.status-badge {
  padding: 4px 14px;
  border-radius: 20px;
  font-size: 13px;
  font-weight: 600;
}
.status-badge.pending    { background: #FEF3C7; color: #92400E; }
.status-badge.processing { background: #DBEAFE; color: #1E40AF; }
.status-badge.done       { background: #D1FAE5; color: #065F46; }
.status-badge.fail       { background: #FEE2E2; color: #991B1B; }
.polling-hint { font-size: 11px; color: #9CA3AF; }

/* 공통 중앙 상태 */
.center-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 80px 0;
  gap: 16px;
}
.spinner {
  width: 40px; height: 40px;
  border: 3px solid #E5E7EB;
  border-top-color: #7C3AED;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
.poll-msg  { font-size: 16px; font-weight: 600; color: #374151; }
.poll-sub  { font-size: 13px; color: #9CA3AF; }

/* 실패 */
.fail-box {
  background: #FFF5F5;
  border: 1px solid #FECACA;
  border-radius: 12px;
  padding: 48px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  text-align: center;
}
.fail-icon  { font-size: 36px; color: #EF4444; }
.fail-title { font-size: 18px; font-weight: 700; color: #B91C1C; }
.fail-msg   { font-size: 14px; color: #6B7280; max-width: 480px; line-height: 1.6; }

/* fail 배너 (results 있을 때) */
.fail-banner {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  background: #FFF5F5;
  border: 1px solid #FECACA;
  border-radius: 10px;
  padding: 14px 18px;
}
.fail-banner-icon { font-size: 18px; color: #EF4444; flex-shrink: 0; }
.fail-banner-title { font-size: 14px; font-weight: 700; color: #B91C1C; margin-bottom: 3px; }
.fail-banner-msg   { font-size: 13px; color: #6B7280; }

/* 결과 그리드 */
.results-area { display: flex; flex-direction: column; gap: 16px; }
.results-header {
  display: flex;
  align-items: center;
}
.results-count { font-size: 14px; font-weight: 600; color: #374151; }

.img-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 16px;
}
.img-card {
  background: #fff;
  border: 1px solid #E5E7EB;
  border-radius: 12px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  transition: box-shadow 0.15s;
}
.img-card:hover { box-shadow: 0 4px 16px rgba(0,0,0,0.08); }
.img-card.invalid { border-color: #FECACA; }

.img-thumb {
  position: relative;
  width: 100%;
  background: #F9FAFB;
  overflow: hidden;
}
.img-thumb img {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: contain;
}
.img-thumb.no-img {
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  color: #9CA3AF;
}
.img-thumb.no-img::after { content: '미리보기 없음'; }

.img-info {
  padding: 12px 14px 8px;
  flex: 1;
}
.img-name { font-size: 13px; font-weight: 600; color: #374151; margin-bottom: 4px; }
.img-meta { font-size: 12px; color: #9CA3AF; display: flex; align-items: center; flex-wrap: wrap; gap: 4px; margin-bottom: 4px; }
.mode-badge { font-size: 11px; color: #7C3AED; font-weight: 500; }

.valid-badge {
  font-size: 11px;
  font-weight: 600;
  padding: 1px 7px;
  border-radius: 20px;
}
.valid-badge.ok          { background: #D1FAE5; color: #065F46; }
.valid-badge.invalid     { background: #FEE2E2; color: #991B1B; }
.valid-badge.warn        { background: #FEF3C7; color: #92400E; }
.valid-badge.quality-bad { background: #FEE2E2; color: #991B1B; }
.valid-msg { font-size: 11px; color: #EF4444; margin-top: 2px; line-height: 1.4; word-break: break-all; }

.card-actions { display: flex; gap: 6px; margin: 0 14px 14px; }

.btn-dl-single {
  flex: 1;
  background: #F5F3FF;
  color: #7C3AED;
  border: none;
  border-radius: 7px;
  padding: 7px 0;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
}
.btn-dl-single:hover { background: #EDE9FE; }

.btn-compare {
  flex: 1;
  background: linear-gradient(135deg, rgba(124,58,237,0.1), rgba(59,130,246,0.08));
  color: #7C3AED;
  border: 1px dashed #C4B5FD;
  border-radius: 7px;
  padding: 7px 0;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  display: flex; align-items: center; justify-content: center; gap: 4px;
  font-family: inherit;
}
.btn-compare:hover:not(:disabled) { background: #EDE9FE; border-color: #7C3AED; }
.btn-compare:disabled { opacity: 0.5; cursor: not-allowed; }
.spin-xs {
  width: 10px; height: 10px;
  border: 1.5px solid rgba(124,58,237,0.3); border-top-color: #7C3AED;
  border-radius: 50%; animation: spin 0.7s linear infinite; display: inline-block;
}

/* 비교 모달 */
.cmp-overlay {
  position: fixed; inset: 0; background: rgba(0,0,0,0.45);
  display: flex; align-items: center; justify-content: center;
  z-index: 200; padding: 20px;
}
.cmp-modal {
  background: #fff; border-radius: 16px; width: 100%; max-width: 980px;
  max-height: 90vh; overflow-y: auto;
  box-shadow: 0 20px 60px rgba(0,0,0,0.2);
  padding: 24px;
}
.cmp-header {
  display: flex; align-items: center; gap: 8px;
  font-size: 15px; font-weight: 700; color: #7C3AED; margin-bottom: 10px;
}
.cmp-star { font-size: 12px; }
.cmp-best-badge {
  margin-left: auto; background: #EDE9FE; color: #6D28D9;
  font-size: 12px; font-weight: 700; padding: 3px 10px; border-radius: 20px;
}
.cmp-close {
  background: none; border: none; cursor: pointer; color: #9CA3AF;
  font-size: 16px; padding: 0 0 0 8px;
}
.cmp-close:hover { color: #374151; }
.cmp-summary {
  font-size: 13px; color: #4E5968; line-height: 1.55;
  background: #F9F8FD; border-radius: 8px; padding: 10px 14px; margin-bottom: 16px;
}
.cmp-candidates { display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; }
@media (max-width: 640px) { .cmp-candidates { grid-template-columns: 1fr; } }

.cmp-card {
  border: 1.5px solid #E5E7EB; border-radius: 12px; overflow: hidden;
  transition: box-shadow 0.15s;
}
.cmp-card.best { border-color: #7C3AED; box-shadow: 0 0 0 2px rgba(124,58,237,0.15); }

.cmp-card-head {
  display: flex; align-items: center; gap: 6px;
  padding: 10px 12px 8px; background: #FAFBFF;
  border-bottom: 1px solid #F0EEF8;
}
.cmp-strength { font-size: 13px; font-weight: 700; color: #374151; }
.cmp-score { font-size: 12px; font-weight: 700; padding: 2px 7px; border-radius: 20px; }
.cmp-score.score-high { background: #D1FAE5; color: #065F46; }
.cmp-score.score-mid  { background: #FEF3C7; color: #92400E; }
.cmp-score.score-low  { background: #FEE2E2; color: #991B1B; }
.cmp-crown { margin-left: auto; font-size: 11px; font-weight: 700; color: #7C3AED; }

.cmp-tags {
  display: flex; flex-wrap: wrap; gap: 4px; padding: 5px 12px 0;
}
.cmp-tag {
  font-size: 10px; font-weight: 600; padding: 2px 6px; border-radius: 10px;
  background: #EDE9FE; color: #5B21B6;
}
.cmp-desc {
  font-size: 11px; color: #6B7280; padding: 3px 12px 6px;
}

.cmp-thumb-wrap {
  background: #F2F4F6; width: 100%; aspect-ratio: 4/3;
  display: flex; align-items: center; justify-content: center; overflow: hidden;
}
.cmp-thumb { width: 100%; height: 100%; object-fit: contain; }

/* 요소 보존 평가 (3.5차) */
.cmp-element-section { padding: 6px 12px 4px; border-top: 1px solid #F0EEF8; display: flex; flex-direction: column; gap: 4px; }
.cmp-el-row { display: flex; align-items: flex-start; gap: 5px; }
.cmp-el-label {
  font-size: 9.5px; font-weight: 700; padding: 2px 5px; border-radius: 4px;
  flex-shrink: 0; white-space: nowrap; margin-top: 1px;
}
.cmp-el-preserved-req { background: #DCFCE7; color: #15803D; }
.cmp-el-lost-req      { background: #FEE2E2; color: #B91C1C; }
.cmp-el-preserved-pri { background: #FEF9C3; color: #92400E; }
.cmp-el-lost-pri      { background: #FEF3C7; color: #B45309; }
.cmp-el-tags { display: flex; flex-wrap: wrap; gap: 3px; }
.cmp-el-tag {
  font-size: 10px; font-weight: 600; padding: 1px 6px; border-radius: 4px;
}
.cmp-el-tag-preserved-req { background: #F0FDF4; color: #15803D; border: 1px solid #BBF7D0; }
.cmp-el-tag-lost-req      { background: #FFF5F5; color: #B91C1C; border: 1px solid #FECACA; }
.cmp-el-tag-preserved-pri { background: #FFFBEB; color: #92400E; border: 1px solid #FDE68A; }
.cmp-el-tag-lost-pri      { background: #FFFBEB; color: #B45309; border: 1px solid #FCD34D; }

.cmp-pros, .cmp-cons { padding: 8px 12px; }
.cmp-pros { border-top: 1px solid #F0FDF4; }
.cmp-cons { border-top: 1px solid #FEF2F2; }
.cmp-pro-item { font-size: 11px; color: #16A34A; line-height: 1.5; }
.cmp-con-item { font-size: 11px; color: #DC2626; line-height: 1.5; }

.cmp-apply-area { padding: 8px 12px 12px; }
.btn-cmp-apply {
  width: 100%;
  padding: 7px 0;
  border-radius: 7px;
  border: 1px solid #D1D5DB;
  background: #F9FAFB;
  color: #374151;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  display: flex; align-items: center; justify-content: center; gap: 5px;
  font-family: inherit;
  transition: background 0.15s, border-color 0.15s;
}
.btn-cmp-apply:hover:not(:disabled) { background: #F3F4F6; border-color: #9CA3AF; }
.btn-cmp-apply:disabled { opacity: 0.5; cursor: not-allowed; }
.btn-cmp-apply-best {
  background: linear-gradient(135deg, #EDE9FE, #DDD6FE);
  border-color: #7C3AED;
  color: #6D28D9;
}
.btn-cmp-apply-best:hover:not(:disabled) { background: #DDD6FE; }

.artboard-badge {
  margin-top: 4px;
  font-size: 11px; font-weight: 600;
  color: #1D4ED8; background: #EFF6FF;
  border-radius: 6px; padding: 2px 8px;
  display: inline-block;
}
.psd-fallback-badge {
  margin-top: 3px;
  font-size: 10px; font-weight: 500;
  color: #B45309; background: #FFFBEB;
  border-radius: 6px; padding: 2px 8px;
  display: inline-block;
}
.render-source-badge {
  margin-top: 3px;
  font-size: 10px; font-weight: 500;
  border-radius: 6px; padding: 2px 8px;
  display: inline-block;
}
.render-source-ok { color: #065F46; background: #ECFDF5; }
.render-source-fallback { color: #B45309; background: #FFFBEB; }
.render-source-neutral { color: #374151; background: #F3F4F6; }
.fallback-notice {
  margin-top: 3px;
  font-size: 10px; color: #92400E; background: #FEF3C7;
  border-radius: 6px; padding: 2px 8px;
  display: inline-block; line-height: 1.4;
}
.layer-reflow-badge {
  margin-top: 4px;
  font-size: 11px; font-weight: 600;
  color: #0369A1; background: #E0F2FE;
  border-radius: 6px; padding: 2px 8px;
  display: inline-flex; align-items: center; gap: 6px;
}
.reflow-template {
  font-size: 10px; font-weight: 500;
  color: #0284C7; background: #BAE6FD;
  border-radius: 4px; padding: 1px 5px;
}
.reflow-roles {
  margin-top: 3px; display: flex; flex-wrap: wrap; gap: 3px;
}
.reflow-role-tag {
  font-size: 10px; font-weight: 500;
  color: #374151; background: #F3F4F6;
  border-radius: 4px; padding: 1px 5px;
}
.fallback-reason {
  margin-top: 3px;
  font-size: 10px; color: #B45309; background: #FFFBEB;
  border-radius: 6px; padding: 2px 8px;
  display: inline-block; word-break: break-all; line-height: 1.4;
}
.mode-badge-layer {
  font-size: 11px; color: #0369A1; font-weight: 600;
}
.mode-badge-fallback {
  font-size: 11px; color: #B45309; font-weight: 500;
}

.ai-applied-badge {
  margin-top: 5px;
  font-size: 11px;
  font-weight: 600;
  color: #6D28D9;
  background: #EDE9FE;
  border-radius: 6px;
  padding: 2px 8px;
  display: inline-block;
}

/* 4차-4: Wide-Banner Smart-Fit */
.wide-banner-badge {
  margin-top: 3px;
  font-size: 10px; font-weight: 600;
  color: #1E40AF; background: #EFF6FF;
  border-radius: 6px; padding: 2px 8px;
  display: inline-flex; align-items: center; gap: 5px;
}
.wide-banner-candidate {
  font-size: 10px; font-weight: 500;
  color: #1D4ED8; background: #BFDBFE;
  border-radius: 4px; padding: 1px 5px;
}
.wide-banner-score {
  font-size: 10px; font-weight: 700;
  color: #065F46; background: #D1FAE5;
  border-radius: 4px; padding: 1px 5px;
}
.wide-banner-score.score-good { color: #065F46; background: #D1FAE5; }
.wide-banner-score.score-warn { color: #92400E; background: #FEF3C7; }
.wide-banner-score.score-bad  { color: #991B1B; background: #FEE2E2; }
.quality-label-badge {
  font-size: 10px; font-weight: 700;
  border-radius: 4px; padding: 1px 5px;
}
.quality-label-badge.ql-good { color: #065F46; background: #D1FAE5; }
.quality-label-badge.ql-warn { color: #92400E; background: #FEF3C7; }
.quality-label-badge.ql-bad  { color: #991B1B; background: #FEE2E2; }
.quality-gate-badge {
  font-size: 10px; font-weight: 600;
  color: #7C3AED; background: #EDE9FE;
  border-radius: 4px; padding: 1px 5px;
}

/* 4차-5: Safe Zone / Required Layer */
.safe-zone-badge {
  margin-top: 3px;
  font-size: 10px; font-weight: 500;
  border-radius: 6px; padding: 2px 8px;
  display: inline-block;
}
.safe-zone-ok   { color: #065F46; background: #D1FAE5; }
.safe-zone-fail { color: #B45309; background: #FEF3C7; }
.required-missing-badge {
  margin-top: 3px;
  font-size: 10px; font-weight: 500;
  color: #B91C1C; background: #FEE2E2;
  border-radius: 6px; padding: 2px 8px;
  display: inline-block;
}
</style>
