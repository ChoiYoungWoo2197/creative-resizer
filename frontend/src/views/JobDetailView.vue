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
                :src="getPreviewUrl(r.fileName)"
                :alt="r.name"
                @error="onImgError($event)"
              />
            </div>
            <div class="img-info">
              <div class="img-name">{{ r.name || r.slug }}</div>
              <div class="img-meta">
                <span>{{ r.width }} × {{ r.height }}</span>
                <span v-if="r.fileSize"> · {{ fmtSize(r.fileSize) }}</span>
                <span class="valid-badge" :class="r.valid === false ? 'invalid' : 'ok'">
                  {{ r.valid === false ? '규격 불일치' : '정상' }}
                </span>
              </div>
              <div class="valid-msg" v-if="r.valid === false">{{ r.validationMessage }}</div>
            </div>
            <button class="btn-dl-single" @click="handleSingleDownload(r)">
              ↓ 다운로드
            </button>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { getJob, downloadZip, downloadImage, previewUrl } from '../api/banner.js'

const route = useRoute()
const router = useRouter()
const job = ref(null)
const isPolling = ref(false)
let pollTimer = null

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
.valid-badge {
  font-size: 11px;
  font-weight: 600;
  padding: 1px 7px;
  border-radius: 20px;
}
.valid-badge.ok      { background: #D1FAE5; color: #065F46; }
.valid-badge.invalid { background: #FEE2E2; color: #991B1B; }
.valid-msg { font-size: 11px; color: #EF4444; margin-top: 2px; line-height: 1.4; word-break: break-all; }

.btn-dl-single {
  margin: 0 14px 14px;
  background: #F5F3FF;
  color: #7C3AED;
  border: none;
  border-radius: 7px;
  padding: 7px 0;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  width: calc(100% - 28px);
}
.btn-dl-single:hover { background: #EDE9FE; }
</style>
