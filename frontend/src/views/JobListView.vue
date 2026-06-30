<template>
  <div class="page-wrap">

    <!-- Page header -->
    <div class="page-top">
      <div>
        <h1 class="page-title">작업 목록 <span class="title-star">✦</span></h1>
        <p class="page-desc">생성된 배너 작업을 확인하고 다운로드할 수 있습니다.</p>
      </div>
      <button class="refresh-btn" @click="load" :disabled="loading">
        <span class="refresh-ico">↺</span> 새로고침
      </button>
    </div>

    <!-- Stats cards -->
    <div class="stats-row">
      <div class="stat-card">
        <div class="stat-ico" style="background:#EDE9FF"><span style="color:#7C3AED">☰</span></div>
        <div class="stat-body">
          <div class="stat-label">전체 작업</div>
          <div class="stat-num">{{ total.toLocaleString() }}</div>
          <div class="stat-sub">전체 생성 작업 수</div>
        </div>
      </div>
      <div class="stat-card">
        <div class="stat-ico" style="background:#D1FAE5">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="10" stroke="#059669" stroke-width="2"/><path d="M7 12l3 3 7-7" stroke="#059669" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
        </div>
        <div class="stat-body">
          <div class="stat-label">완료</div>
          <div class="stat-num" style="color:#059669">{{ done.toLocaleString() }}</div>
          <div class="stat-sub">{{ doneRate }}%</div>
        </div>
      </div>
      <div class="stat-card">
        <div class="stat-ico" style="background:#FEE2E2">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="10" stroke="#DC2626" stroke-width="2"/><path d="M12 7v5M12 16v.5" stroke="#DC2626" stroke-width="2" stroke-linecap="round"/></svg>
        </div>
        <div class="stat-body">
          <div class="stat-label">실패</div>
          <div class="stat-num" style="color:#DC2626">{{ fail.toLocaleString() }}</div>
          <div class="stat-sub">{{ failRate }}%</div>
        </div>
      </div>
      <div class="stat-card">
        <div class="stat-ico" style="background:#DBEAFE">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="10" stroke="#2563EB" stroke-width="2"/><path d="M12 7v5l3 3" stroke="#2563EB" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
        </div>
        <div class="stat-body">
          <div class="stat-label">진행 중</div>
          <div class="stat-num" style="color:#2563EB">{{ processing.toLocaleString() }}</div>
          <div class="stat-sub">{{ processingRate }}%</div>
        </div>
      </div>
      <div class="stat-card ai-card">
        <div class="ai-card-inner">
          <div class="ai-card-head"><span>✦</span> AI 인사이트</div>
          <div class="ai-card-msg">{{ aiInsight }}</div>
        </div>
        <div class="ai-card-chart">
          <div v-for="(h, i) in chartBars" :key="i" class="chart-bar" :style="{ height: h + 'px' }" />
        </div>
      </div>
    </div>

    <!-- Filter bar -->
    <div class="filter-bar">
      <div class="search-wrap">
        <svg class="search-ico" width="14" height="14" viewBox="0 0 24 24" fill="none">
          <circle cx="11" cy="11" r="7" stroke="#B0B8C1" stroke-width="2"/>
          <path d="M16.5 16.5L21 21" stroke="#B0B8C1" stroke-width="2" stroke-linecap="round"/>
        </svg>
        <input v-model="search" class="search-input" placeholder="작업 ID, 광고주, 캠페인 검색" />
      </div>
      <select v-model="filterMedia" class="filter-select">
        <option value="">매체 전체</option>
        <option value="google">Google</option>
        <option value="meta">Meta</option>
        <option value="naver">Naver</option>
        <option value="kakao">Kakao</option>
      </select>
      <select v-model="filterStatus" class="filter-select">
        <option value="">상태 전체</option>
        <option value="done">완료</option>
        <option value="fail">실패</option>
        <option value="pending">대기</option>
        <option value="processing">처리중</option>
      </select>
      <button class="reset-btn" @click="resetFilter">↺ 필터 초기화</button>
      <div class="filter-right">
        <span class="result-cnt">{{ filtered.length }}건</span>
      </div>
    </div>

    <!-- Table -->
    <div class="table-wrap">
      <div v-if="loading && jobs.length === 0" class="tbl-empty">불러오는 중...</div>
      <div v-else-if="filtered.length === 0" class="tbl-empty">조건에 맞는 작업이 없습니다.</div>
      <template v-else>
        <div class="tbl-head">
          <span class="c-id" @click="sort('id')">작업 ID <span class="sort-ico">↕</span></span>
          <span class="c-ad" @click="sort('advertiser')">광고주 <span class="sort-ico">↕</span></span>
          <span class="c-camp" @click="sort('campaignName')">캠페인 <span class="sort-ico">↕</span></span>
          <span class="c-media">매체</span>
          <span class="c-status" @click="sort('status')">상태 <span class="sort-ico">↕</span></span>
          <span class="c-date" @click="sort('createdAt')">생성일 <span class="sort-ico">↕</span></span>
          <span class="c-dl">다운로드</span>
        </div>
        <div v-for="job in paginated" :key="job.id" class="tbl-row clickable-row" @click="goDetail(job.id)">
          <span class="c-id">
            <span class="job-id">{{ job.id.slice(0, 10) }}...</span>
          </span>
          <span class="c-ad fw">{{ job.advertiser }}</span>
          <span class="c-camp fw">{{ job.campaignName }}</span>
          <span class="c-media">
            <span v-for="m in job.targetMedia" :key="m" class="media-tag" :class="m">{{ m }}</span>
          </span>
          <span class="c-status">
            <span class="badge" :class="job.status">{{ statusLabel(job.status) }}</span>
          </span>
          <span class="c-date gray">{{ formatDate(job.createdAt) }}</span>
          <span class="c-dl" @click.stop>
            <button v-if="job.status === 'done'" class="dl-btn" @click="download(job)">ZIP ↓</button>
            <span v-else-if="job.status === 'fail'" class="err-txt" @click.stop="openError(job)">오류 ↗</span>
            <span v-else class="dash">—</span>
          </span>
        </div>
      </template>
    </div>

    <!-- 오류 상세 모달 -->
    <div v-if="errorJob" class="modal-overlay" @click.self="errorJob = null">
      <div class="modal-box">
        <div class="modal-head">
          <span>오류 상세</span>
          <button class="modal-close" @click="errorJob = null">✕</button>
        </div>
        <div class="modal-body">
          <table class="error-tbl">
            <tr><th>작업 ID</th><td class="mono">{{ errorJob.id }}</td></tr>
            <tr><th>광고주</th><td>{{ errorJob.advertiser }}</td></tr>
            <tr><th>캠페인</th><td>{{ errorJob.campaignName }}</td></tr>
            <tr><th>오류 메시지</th><td class="err-cell">{{ errorJob.errorMessage || '알 수 없는 오류' }}</td></tr>
          </table>
          <template v-if="errorJob.results?.some(r => r.valid === false)">
            <div class="invalid-section-title">규격 불일치 이미지</div>
            <div v-for="r in errorJob.results.filter(r => r.valid === false)" :key="r.fileName" class="invalid-row">
              <span class="invalid-name">{{ r.name || r.slug }}</span>
              <span class="invalid-msg">{{ r.validationMessage }}</span>
            </div>
          </template>
        </div>
      </div>
    </div>

    <!-- Pagination -->
    <div v-if="filtered.length > 0" class="pagination">
      <span class="pg-info">전체 {{ filtered.length }}건</span>
      <div class="pg-btns">
        <button class="pg-arrow" :disabled="page === 1" @click="page--">‹</button>
        <template v-for="p in pageButtons" :key="p">
          <span v-if="p === '...'" class="pg-ellipsis">···</span>
          <button v-else class="pg-btn" :class="{ active: page === p }" @click="page = p">{{ p }}</button>
        </template>
        <button class="pg-arrow" :disabled="page === totalPages" @click="page++">›</button>
      </div>
      <select v-model="pageSize" class="pg-size" @change="page = 1">
        <option :value="10">10 / 페이지</option>
        <option :value="20">20 / 페이지</option>
        <option :value="50">50 / 페이지</option>
      </select>
    </div>

  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { listJobs, downloadZip } from '../api/banner.js'

const router = useRouter()
const goDetail = (id) => router.push(`/job/${id}`)

const jobs     = ref([])
const loading  = ref(false)
const errorJob = ref(null)

function openError(job) {
  errorJob.value = job
}

const search       = ref('')
const filterMedia  = ref('')
const filterStatus = ref('')
const sortField    = ref('createdAt')
const sortDir      = ref(-1)
const page         = ref(1)
const pageSize     = ref(10)

const total      = computed(() => jobs.value.length)
const done       = computed(() => jobs.value.filter(j => j.status === 'done').length)
const fail       = computed(() => jobs.value.filter(j => j.status === 'fail').length)
const processing = computed(() => jobs.value.filter(j => j.status === 'processing' || j.status === 'pending').length)
const doneRate       = computed(() => total.value ? (done.value / total.value * 100).toFixed(1) : 0)
const failRate       = computed(() => total.value ? (fail.value / total.value * 100).toFixed(1) : 0)
const processingRate = computed(() => total.value ? (processing.value / total.value * 100).toFixed(1) : 0)

const aiInsight = computed(() => {
  if (!total.value) return '아직 작업이 없습니다.'
  if (Number(doneRate.value) >= 80) return `완료율이 ${doneRate.value}%! 매우 우수합니다 🎉`
  if (Number(failRate.value) >= 30) return `실패율 ${failRate.value}% — PSD 포맷을 확인해 보세요.`
  return `완료율 ${doneRate.value}%, 총 ${total.value}건 처리됐습니다.`
})

const chartBars = computed(() => {
  const d = done.value, f = fail.value, p = processing.value
  const mx = Math.max(d, f, p, 1)
  return [d, f, p, d, f].map(v => Math.max(4, Math.round(v / mx * 28)))
})

const filtered = computed(() => {
  let list = jobs.value
  if (search.value) {
    const q = search.value.toLowerCase()
    list = list.filter(j =>
      j.id.toLowerCase().includes(q) ||
      j.advertiser.toLowerCase().includes(q) ||
      j.campaignName.toLowerCase().includes(q)
    )
  }
  if (filterMedia.value)  list = list.filter(j => j.targetMedia?.includes(filterMedia.value))
  if (filterStatus.value) list = list.filter(j => j.status === filterStatus.value)
  return [...list].sort((a, b) => {
    const av = a[sortField.value] ?? '', bv = b[sortField.value] ?? ''
    return av < bv ? -sortDir.value : av > bv ? sortDir.value : 0
  })
})

const totalPages = computed(() => Math.ceil(filtered.value.length / pageSize.value))
const paginated  = computed(() => {
  const s = (page.value - 1) * pageSize.value
  return filtered.value.slice(s, s + pageSize.value)
})
const pageButtons = computed(() => {
  const tp = totalPages.value, p = page.value
  if (tp <= 7) return Array.from({ length: tp }, (_, i) => i + 1)
  if (p <= 4)  return [1,2,3,4,5,'...',tp]
  if (p >= tp - 3) return [1,'...',tp-4,tp-3,tp-2,tp-1,tp]
  return [1,'...',p-1,p,p+1,'...',tp]
})

function sort(field) {
  if (sortField.value === field) sortDir.value *= -1
  else { sortField.value = field; sortDir.value = -1 }
  page.value = 1
}

function resetFilter() {
  search.value = ''; filterMedia.value = ''; filterStatus.value = ''; page.value = 1
}

const statusLabel = s => ({ pending: '대기', processing: '처리중', done: '완료', fail: '실패' }[s] ?? s)

function formatDate(d) {
  if (!d) return '-'
  const dt = new Date(d)
  const m = dt.getMonth() + 1, day = dt.getDate()
  const h = dt.getHours(), min = dt.getMinutes().toString().padStart(2, '0')
  const ampm = h < 12 ? '오전' : '오후'
  return `${m.toString().padStart(2,'0')}.${day.toString().padStart(2,'0')}. ${ampm} ${(h%12||12)}:${min}`
}

async function load() {
  loading.value = true
  try {
    const { data } = await listJobs()
    jobs.value = data.sort((a, b) => new Date(b.createdAt) - new Date(a.createdAt))
  } catch { ElMessage.error('목록 로딩 실패') }
  finally { loading.value = false }
}

async function download(row) {
  try {
    const { data } = await downloadZip(row.id)
    const url = URL.createObjectURL(data)
    const a = document.createElement('a')
    a.href = url; a.download = `${row.advertiser}_${row.campaignName}.zip`; a.click()
    URL.revokeObjectURL(url)
  } catch { ElMessage.error('다운로드 실패') }
}

onMounted(load)
</script>

<style scoped>
.page-wrap { max-width: 1200px; margin: 0 auto; padding: 32px 28px 60px; }

/* header */
.page-top { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 24px; }
.page-title { font-size: 22px; font-weight: 800; letter-spacing: -0.5px; color: #191F28; }
.title-star { font-size: 14px; color: #7C3AED; margin-left: 4px; }
.page-desc  { margin-top: 5px; font-size: 13px; color: #8B95A1; }
.refresh-btn {
  display: flex; align-items: center; gap: 6px;
  padding: 9px 18px; border-radius: 10px; border: 1.5px solid #E5E8EB;
  background: #fff; font-size: 13px; font-weight: 600; color: #4E5968;
  cursor: pointer; font-family: inherit; white-space: nowrap; transition: all 0.12s;
}
.refresh-btn:hover { border-color: #C4CAD4; color: #191F28; }
.refresh-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.refresh-ico { font-size: 14px; }

/* stats */
.stats-row { display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px; margin-bottom: 20px; }

.stat-card {
  background: #fff; border-radius: 14px; padding: 18px 20px;
  box-shadow: 0 1px 4px rgba(0,0,0,0.05); display: flex; gap: 14px; align-items: flex-start;
  border: 1px solid #F0F2F4;
}
.stat-ico {
  width: 38px; height: 38px; border-radius: 10px; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center; font-size: 18px;
}
.stat-label { font-size: 12px; color: #8B95A1; font-weight: 500; margin-bottom: 4px; }
.stat-num   { font-size: 22px; font-weight: 800; color: #191F28; letter-spacing: -0.5px; line-height: 1.1; margin-bottom: 3px; }
.stat-sub   { font-size: 11px; color: #B0B8C1; }

/* AI stat card */
.ai-card {
  background: linear-gradient(135deg, #6D28D9, #3B82F6) !important;
  border: none !important; color: #fff; justify-content: space-between; align-items: center;
}
.ai-card-inner { flex: 1; }
.ai-card-head { font-size: 12px; font-weight: 700; color: rgba(255,255,255,0.85); margin-bottom: 8px; display: flex; gap: 5px; align-items: center; }
.ai-card-msg  { font-size: 13px; font-weight: 600; color: #fff; line-height: 1.4; }
.ai-card-chart { display: flex; align-items: flex-end; gap: 3px; height: 36px; flex-shrink: 0; }
.chart-bar { width: 6px; border-radius: 3px; background: rgba(255,255,255,0.35); }

/* filter bar */
.filter-bar {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  margin-bottom: 12px;
}
.search-wrap { position: relative; flex: 1; min-width: 200px; max-width: 320px; }
.search-ico  { position: absolute; left: 12px; top: 50%; transform: translateY(-50%); pointer-events: none; }
.search-input {
  width: 100%; padding: 9px 12px 9px 34px;
  border: 1.5px solid #EAEDF0; border-radius: 10px;
  font-size: 13px; font-family: inherit; outline: none; color: #191F28;
  transition: border-color 0.12s;
}
.search-input:focus { border-color: #7C3AED; }
.search-input::placeholder { color: #C4CAD0; }
.filter-select {
  padding: 9px 12px; border: 1.5px solid #EAEDF0; border-radius: 10px;
  font-size: 13px; font-family: inherit; color: #4E5968; background: #fff;
  outline: none; cursor: pointer;
}
.filter-select:focus { border-color: #7C3AED; }
.reset-btn {
  padding: 9px 14px; border-radius: 10px; border: 1.5px solid #EAEDF0; background: #fff;
  font-size: 13px; color: #6B7684; cursor: pointer; font-family: inherit; white-space: nowrap;
}
.reset-btn:hover { color: #7C3AED; border-color: #7C3AED; }
.filter-right { margin-left: auto; }
.result-cnt { font-size: 13px; color: #8B95A1; font-weight: 500; }

/* table */
.table-wrap { background: #fff; border-radius: 14px; border: 1px solid #EAEDF0; overflow: hidden; margin-bottom: 16px; }
.tbl-empty  { padding: 60px; text-align: center; color: #B0B8C1; font-size: 14px; }

.tbl-head, .tbl-row {
  display: grid;
  grid-template-columns: 130px 90px 1fr 180px 80px 140px 100px;
  align-items: center; padding: 0 20px; gap: 8px;
}
.tbl-head {
  background: #F8F9FA; border-bottom: 1px solid #EAEDF0;
  height: 42px; font-size: 12px; font-weight: 600; color: #8B95A1;
}
.tbl-head span { cursor: pointer; user-select: none; display: flex; align-items: center; gap: 3px; }
.tbl-head span:hover { color: #4E5968; }
.sort-ico { font-size: 10px; opacity: 0.5; }
.tbl-row {
  height: 52px; border-bottom: 1px solid #F5F6F8; font-size: 13px; color: #191F28;
  transition: background 0.1s;
}
.tbl-row:last-child { border-bottom: none; }
.tbl-row:hover { background: #FAFBFF; }
.clickable-row { cursor: pointer; }
.clickable-row:hover { background: #F5F3FF; }

.job-id { font-family: monospace; font-size: 12px; color: #6B7684; }
.fw { font-weight: 500; }
.gray { color: #8B95A1; font-size: 12px; }

.media-tag {
  display: inline-block; padding: 2px 8px; border-radius: 100px;
  font-size: 11px; font-weight: 600; margin: 1px;
}
.media-tag.naver   { background: #E6F9EE; color: #03C75A; }
.media-tag.google  { background: #EAF1FE; color: #4285F4; }
.media-tag.meta    { background: #E8F0FD; color: #1877F2; }
.media-tag.kakao   { background: #FEF9E7; color: #B8960C; }
.media-tag         { background: #F2F4F6; color: #6B7684; }

.badge {
  display: inline-block; padding: 4px 10px; border-radius: 100px;
  font-size: 12px; font-weight: 600;
}
.badge.done       { background: #D1FAE5; color: #059669; }
.badge.fail       { background: #FEE2E2; color: #DC2626; }
.badge.pending    { background: #F3F4F6; color: #6B7684; }
.badge.processing { background: #FFF8E6; color: #D97706; }

.dl-btn {
  padding: 5px 12px; border-radius: 7px; border: 1.5px solid #7C3AED;
  background: #fff; color: #7C3AED; font-size: 12px; font-weight: 700;
  cursor: pointer; font-family: inherit; transition: all 0.12s;
}
.dl-btn:hover { background: #7C3AED; color: #fff; }
.err-txt { font-size: 12px; color: #DC2626; font-weight: 600; cursor: pointer; text-decoration: underline; }
.err-txt:hover { color: #B91C1C; }

/* 오류 모달 */
.modal-overlay {
  position: fixed; inset: 0; background: rgba(0,0,0,0.4);
  display: flex; align-items: center; justify-content: center; z-index: 9999;
}
.modal-box {
  background: #fff; border-radius: 14px; width: 480px; max-width: 90vw;
  box-shadow: 0 8px 32px rgba(0,0,0,0.18); overflow: hidden;
}
.modal-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: 16px 20px; border-bottom: 1px solid #F0F2F4;
  font-size: 15px; font-weight: 700; color: #191F28;
}
.modal-close {
  background: none; border: none; font-size: 16px; color: #8B95A1;
  cursor: pointer; line-height: 1; padding: 2px 4px;
}
.modal-close:hover { color: #191F28; }
.modal-body { padding: 20px; display: flex; flex-direction: column; gap: 14px; }
.error-tbl { width: 100%; border-collapse: collapse; font-size: 13px; }
.error-tbl th {
  text-align: left; width: 90px; padding: 7px 0; color: #8B95A1;
  font-weight: 600; vertical-align: top; white-space: nowrap;
}
.error-tbl td { padding: 7px 0; color: #191F28; word-break: break-all; }
.error-tbl tr { border-bottom: 1px solid #F5F6F8; }
.error-tbl tr:last-child { border-bottom: none; }
.mono { font-family: monospace; font-size: 12px; }
.err-cell { color: #DC2626; }
.invalid-section-title { font-size: 13px; font-weight: 700; color: #374151; margin-bottom: 6px; }
.invalid-row { background: #FFF5F5; border-radius: 7px; padding: 8px 12px; margin-bottom: 4px; }
.invalid-name { font-size: 13px; font-weight: 600; color: #374151; display: block; margin-bottom: 2px; }
.invalid-msg  { font-size: 12px; color: #EF4444; }
.dash    { color: #D1D8E0; }
.more-btn {
  width: 28px; height: 28px; border-radius: 6px; border: 1.5px solid #EAEDF0;
  background: #fff; color: #B0B8C1; cursor: pointer; font-size: 16px;
  display: inline-flex; align-items: center; justify-content: center;
  margin-left: 6px; font-family: inherit;
}
.more-btn:hover { border-color: #C4CAD4; color: #6B7684; }

/* pagination */
.pagination { display: flex; align-items: center; gap: 8px; justify-content: center; flex-wrap: wrap; }
.pg-info    { font-size: 13px; color: #8B95A1; margin-right: 8px; }
.pg-btns    { display: flex; gap: 4px; align-items: center; }
.pg-btn {
  width: 32px; height: 32px; border-radius: 8px; border: 1.5px solid #EAEDF0;
  background: #fff; font-size: 13px; color: #4E5968; cursor: pointer; font-family: inherit;
  display: flex; align-items: center; justify-content: center; transition: all 0.1s;
}
.pg-btn:hover  { border-color: #7C3AED; color: #7C3AED; }
.pg-btn.active { background: #7C3AED; border-color: #7C3AED; color: #fff; font-weight: 700; }
.pg-arrow {
  width: 32px; height: 32px; border-radius: 8px; border: 1.5px solid #EAEDF0;
  background: #fff; font-size: 16px; color: #6B7684; cursor: pointer;
  display: flex; align-items: center; justify-content: center;
}
.pg-arrow:hover:not(:disabled) { border-color: #7C3AED; color: #7C3AED; }
.pg-arrow:disabled { opacity: 0.35; cursor: not-allowed; }
.pg-ellipsis { color: #B0B8C1; font-size: 14px; padding: 0 4px; }
.pg-size {
  margin-left: 8px; padding: 7px 10px; border: 1.5px solid #EAEDF0; border-radius: 8px;
  font-size: 12px; font-family: inherit; color: #6B7684; background: #fff; outline: none; cursor: pointer;
}
</style>
