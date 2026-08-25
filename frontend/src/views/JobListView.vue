<template>
  <div class="page-wrap">

    <!-- Page header -->
    <div class="page-top">
      <div>
        <h1 class="page-title">작업 목록 <span class="title-star">✦</span></h1>
        <p class="page-desc">생성된 배너 작업을 확인하고 다운로드할 수 있습니다.</p>
      </div>
      <div style="display:flex;gap:8px;align-items:center">
        <button v-if="selectedIds.size > 0" class="del-sel-btn" @click="deleteSelected">
          선택 삭제 ({{ selectedIds.size }})
        </button>
        <button class="refresh-btn" @click="load" :disabled="loading">
          <span class="refresh-ico">↺</span> 새로고침
        </button>
      </div>
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
        <input v-model="search" class="search-input" placeholder="작업 ID, 제목, 설명 검색" />
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
      <div class="date-preset-group">
        <button
          v-for="p in datePresets" :key="p.value"
          class="preset-btn" :class="{ active: filterDatePreset === p.value }"
          @click="setDatePreset(p.value)"
        >{{ p.label }}</button>
      </div>
      <div class="date-range-group">
        <input type="date" v-model="filterDateFrom" class="date-input" @change="onDateInputChange" />
        <span class="date-sep">~</span>
        <input type="date" v-model="filterDateTo" class="date-input" @change="onDateInputChange" />
      </div>
      <button class="reset-btn" @click="resetFilter">↺ 초기화</button>
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
          <span class="c-chk"><input type="checkbox" class="chk" :checked="allSelected" ref="allCheckboxRef" @change="toggleAll" /></span>
          <span class="c-id" @click="sort('id')">작업 ID <span class="sort-ico">↕</span></span>
          <span class="c-ad" @click="sort('advertiser')">제목 <span class="sort-ico">↕</span></span>
          <span class="c-camp" @click="sort('campaignName')">설명 <span class="sort-ico">↕</span></span>
          <span class="c-media">매체</span>
          <span class="c-status" @click="sort('status')">상태 <span class="sort-ico">↕</span></span>
          <span class="c-date" @click="sort('createdAt')">생성일 <span class="sort-ico">↕</span></span>
          <span class="c-dl">미리보기 | 다운로드</span>
        </div>
        <div v-for="job in paginated" :key="job.id" class="tbl-row" :class="{ 'row-selected': selectedJob && selectedJob.id === job.id, 'row-checked': selectedIds.has(job.id), 'row-editing': editingCell?.jobId === job.id, ['status-' + job.status]: true }">
          <span class="c-chk" @click.stop>
            <input type="checkbox" class="chk" :checked="selectedIds.has(job.id)" @change="toggleRow(job.id)" />
          </span>
          <span class="c-id">
            <span class="job-id">{{ job.id }}</span>
          </span>
          <span class="c-ad fw" @dblclick.stop="startEdit(job, 'advertiser')">
            <template v-if="editingCell?.jobId === job.id && editingCell?.field === 'advertiser'">
              <input class="inline-edit" v-model="editingCell.value"
                @keyup.enter="saveEdit" @keyup.escape="cancelEdit"
                @click.stop @mousedown.stop autofocus />
              <div class="edit-btns">
                <button class="edit-cancel-btn" @click.stop="cancelEdit">CANCEL</button>
                <button class="edit-update-btn" @click.stop="saveEdit">UPDATE</button>
              </div>
            </template>
            <template v-else>{{ job.advertiser || '—' }}</template>
          </span>
          <span class="c-camp fw" @dblclick.stop="startEdit(job, 'campaignName')">
            <template v-if="editingCell?.jobId === job.id && editingCell?.field === 'campaignName'">
              <input class="inline-edit" v-model="editingCell.value"
                @keyup.enter="saveEdit" @keyup.escape="cancelEdit"
                @click.stop @mousedown.stop autofocus />
              <div class="edit-btns">
                <button class="edit-cancel-btn" @click.stop="cancelEdit">CANCEL</button>
                <button class="edit-update-btn" @click.stop="saveEdit">UPDATE</button>
              </div>
            </template>
            <template v-else>{{ job.campaignName || '—' }}</template>
          </span>
          <span class="c-media">
            <span v-for="m in job.targetMedia" :key="m" class="media-tag" :class="m">{{ m }}</span>
          </span>
          <span class="c-status">
            <span class="badge" :class="job.status">
              <span v-if="job.status === 'processing' || job.status === 'pending'" class="badge-spinner"></span>
              {{ statusLabel(job.status) }}
            </span>
          </span>
          <span class="c-date gray">{{ formatDate(job.createdAt) }}</span>
          <span class="c-dl" @click.stop>
            <button class="preview-btn" @click="selectJob(job)" title="미리보기">👁</button>
            <button v-if="job.status === 'done'" class="dl-btn" @click="download(job)">ZIP ↓</button>
            <span v-else-if="job.status === 'fail'" class="err-txt" @click.stop="openError(job)">오류 ↗</span>
            <span v-else class="dash">—</span>
          </span>
        </div>
      </template>
    </div>

    <!-- 라이트박스 -->
    <div v-if="lightboxItem" class="lightbox-overlay" @click.self="closeLightbox">
      <div class="lightbox-box">
        <button class="lightbox-close" @click="closeLightbox">✕</button>
        <img :src="getPreviewUrl(selectedJob.id, lightboxItem.fileName)" class="lightbox-img" :alt="lightboxItem.name" />
        <div class="lightbox-info">
          <span class="lightbox-name">{{ lightboxItem.name || lightboxItem.slug }}</span>
          <span v-if="lightboxItem.width && lightboxItem.height" class="lightbox-size">{{ lightboxItem.width }}×{{ lightboxItem.height }}</span>
        </div>
      </div>
    </div>

    <!-- 우측 디테일 패널 오버레이 -->
    <div v-if="selectedJob" class="panel-overlay" @click="closePanel" />
    <div class="detail-panel" :class="{ open: selectedJob }">
      <template v-if="selectedJob">
        <div class="dp-accent-strip"></div>
        <div class="dp-head">
          <div class="dp-head-left">
            <span class="dp-job-id">{{ selectedJob.id }}</span>
            <span class="badge" :class="selectedJob.status">
              <span v-if="selectedJob.status === 'processing' || selectedJob.status === 'pending'" class="badge-spinner"></span>
              {{ statusLabel(selectedJob.status) }}
            </span>
          </div>
          <button class="dp-close" @click="closePanel">✕</button>
        </div>
        <div class="dp-meta">
          <div class="dp-meta-row"><span class="dp-meta-label">광고주</span><span class="dp-meta-val">{{ selectedJob.advertiser || '—' }}</span></div>
          <div class="dp-meta-row"><span class="dp-meta-label">캠페인</span><span class="dp-meta-val">{{ selectedJob.campaignName || '—' }}</span></div>
          <div class="dp-meta-row"><span class="dp-meta-label">생성일</span><span class="dp-meta-val gray">{{ formatDate(selectedJob.createdAt) }}</span></div>
          <div class="dp-meta-row"><span class="dp-meta-label">매체</span>
            <span>
              <span v-for="m in selectedJob.targetMedia" :key="m" class="media-tag" :class="m">{{ m }}</span>
            </span>
          </div>
        </div>
        <div class="dp-memo-section">
          <div class="dp-memo-head">메모</div>
          <textarea class="dp-memo-area" v-model="memoEdit" placeholder="메모를 입력하세요..." />
          <button v-if="memoDirty" class="dp-memo-save" @click="saveMemo" :disabled="isSavingMemo">
            {{ isSavingMemo ? '저장 중...' : '저장' }}
          </button>
        </div>
        <div class="dp-section-title">결과 이미지 <span class="dp-cnt">{{ (selectedJob.results || []).length }}개</span></div>
        <div class="dp-results">
          <div v-if="!selectedJob.results || selectedJob.results.length === 0" class="dp-empty">
            {{ selectedJob.status === 'done' ? '결과가 없습니다.' : '생성이 완료되면 여기에 표시됩니다.' }}
          </div>
          <template v-else>
            <div v-for="r in selectedJob.results" :key="r.fileName" class="dp-result-card">
              <div class="dp-thumb-wrap">
                <img :src="getPreviewUrl(selectedJob.id, r.fileName)" class="dp-thumb" :alt="r.name || r.slug" />
                <div class="dp-card-hover">
                  <button class="dp-card-action" @click.stop="openLightbox(r)" title="크게보기">⤢</button>
                  <button class="dp-card-action" @click.stop="downloadSingle(selectedJob.id, r.fileName)" title="다운로드">↓</button>
                  <button class="dp-card-action" @click.stop="goEditor(selectedJob.id, r.fileName)" title="P5 에디터">✏</button>
                </div>
              </div>
              <div class="dp-result-label">{{ r.name || r.slug || r.fileName }}</div>
              <div class="dp-result-size" v-if="r.width && r.height">{{ r.width }}×{{ r.height }}</div>
              <div class="dp-result-file" :title="r.fileName">{{ r.fileName }}</div>
            </div>
          </template>
        </div>
        <div class="dp-actions">
          <button v-if="selectedJob.status === 'done'" class="dp-btn dp-btn-primary" @click="download(selectedJob)">ZIP 다운로드 ↓</button>
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
import { ref, computed, watch, watchEffect, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { listJobs, downloadZip, downloadImage, previewUrl, updateJobMeta, deleteJobs } from '../api/banner.js'

const router = useRouter()
const goDetail = (id) => router.push(`/job/${id}`)

const jobs        = ref([])
const loading     = ref(false)
const errorJob    = ref(null)
const selectedJob = ref(null)
const lightboxItem = ref(null)

const selectedIds    = ref(new Set())
const editingCell    = ref(null)
const allCheckboxRef = ref(null)
const memoEdit       = ref('')
const memoOriginal   = ref('')
const isSavingMemo   = ref(false)

function selectJob(job)  {
  selectedJob.value = job
  memoEdit.value     = job.memo || ''
  memoOriginal.value = job.memo || ''
}
function closePanel()    { selectedJob.value = null }
function getPreviewUrl(jobId, fileName) { return previewUrl(jobId, fileName) }
function openLightbox(result)  { lightboxItem.value = result }
function closeLightbox() { lightboxItem.value = null }
function goEditor(jobId, fileName) { router.push({ name: 'jobEdit', params: { id: jobId }, query: { fileName } }) }

async function downloadSingle(jobId, fileName) {
  try {
    const { data } = await downloadImage(jobId, fileName)
    const url = URL.createObjectURL(data)
    const a = document.createElement('a')
    a.href = url; a.download = fileName; a.click()
    URL.revokeObjectURL(url)
  } catch { ElMessage.error('다운로드 실패') }
}

function openError(job) {
  errorJob.value = job
}

const search           = ref('')
const filterMedia      = ref('')
const filterStatus     = ref('')
const filterDatePreset = ref('')
const filterDateFrom   = ref('')
const filterDateTo     = ref('')
const sortField        = ref('createdAt')
const sortDir          = ref(-1)
const page             = ref(1)
const pageSize         = ref(10)

const datePresets = [
  { value: 'today', label: '오늘' },
  { value: 'week',  label: '이번 주' },
  { value: 'month', label: '이번 달' },
]

function toDateStr(d) {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

function setDatePreset(preset) {
  if (filterDatePreset.value === preset) {
    filterDatePreset.value = ''
    filterDateFrom.value = ''
    filterDateTo.value = ''
    return
  }
  filterDatePreset.value = preset
  const now = new Date()
  const today = toDateStr(now)
  if (preset === 'today') {
    filterDateFrom.value = today
    filterDateTo.value   = today
  } else if (preset === 'week') {
    const mon = new Date(now)
    mon.setDate(now.getDate() - ((now.getDay() + 6) % 7))
    filterDateFrom.value = toDateStr(mon)
    filterDateTo.value   = today
  } else if (preset === 'month') {
    filterDateFrom.value = toDateStr(new Date(now.getFullYear(), now.getMonth(), 1))
    filterDateTo.value   = today
  }
  page.value = 1
}

function onDateInputChange() {
  filterDatePreset.value = ''
  page.value = 1
}

const total      = computed(() => filtered.value.length)
const done       = computed(() => filtered.value.filter(j => j.status === 'done').length)
const fail       = computed(() => filtered.value.filter(j => j.status === 'fail').length)
const processing = computed(() => filtered.value.filter(j => j.status === 'processing' || j.status === 'pending').length)
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
      (j.advertiser || '').toLowerCase().includes(q) ||
      (j.campaignName || '').toLowerCase().includes(q)
    )
  }
  if (filterMedia.value)  list = list.filter(j => j.targetMedia?.includes(filterMedia.value))
  if (filterStatus.value) list = list.filter(j => j.status === filterStatus.value)
  if (filterDateFrom.value || filterDateTo.value) {
    list = list.filter(j => {
      if (!j.createdAt) return false
      const d = j.createdAt.slice(0, 10)
      if (filterDateFrom.value && d < filterDateFrom.value) return false
      if (filterDateTo.value   && d > filterDateTo.value)   return false
      return true
    })
  }
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
  search.value = ''; filterMedia.value = ''; filterStatus.value = ''
  filterDatePreset.value = ''; filterDateFrom.value = ''; filterDateTo.value = ''
  page.value = 1
}

const allSelected  = computed(() => paginated.value.length > 0 && paginated.value.every(j => selectedIds.value.has(j.id)))
const someSelected = computed(() => !allSelected.value && paginated.value.some(j => selectedIds.value.has(j.id)))
const memoDirty    = computed(() => memoEdit.value !== memoOriginal.value)

watchEffect(() => {
  if (allCheckboxRef.value) allCheckboxRef.value.indeterminate = someSelected.value
})

function toggleAll() {
  const s = new Set(selectedIds.value)
  if (allSelected.value) paginated.value.forEach(j => s.delete(j.id))
  else paginated.value.forEach(j => s.add(j.id))
  selectedIds.value = s
}

function toggleRow(jobId) {
  const s = new Set(selectedIds.value)
  if (s.has(jobId)) s.delete(jobId); else s.add(jobId)
  selectedIds.value = s
}

async function deleteSelected() {
  const ids = [...selectedIds.value]
  if (!ids.length) return
  if (!confirm(`선택한 ${ids.length}개 작업을 삭제하시겠습니까?`)) return
  try {
    await deleteJobs(ids)
    jobs.value = jobs.value.filter(j => !ids.includes(j.id))
    if (selectedJob.value && ids.includes(selectedJob.value.id)) selectedJob.value = null
    selectedIds.value = new Set()
    ElMessage.success(`${ids.length}개 작업 삭제됨`)
  } catch { ElMessage.error('삭제 실패') }
}

function startEdit(job, field) {
  editingCell.value = { jobId: job.id, field, value: job[field] || '' }
}

function cancelEdit() { editingCell.value = null }

async function saveEdit() {
  if (!editingCell.value) return
  const { jobId, field, value } = editingCell.value
  editingCell.value = null
  const job = jobs.value.find(j => j.id === jobId)
  if (!job || value === job[field]) return
  const old = job[field]
  job[field] = value
  if (selectedJob.value?.id === jobId) selectedJob.value[field] = value
  try {
    await updateJobMeta(jobId, { [field]: value })
  } catch {
    job[field] = old
    if (selectedJob.value?.id === jobId) selectedJob.value[field] = old
    ElMessage.error('저장 실패')
  }
}

async function saveMemo() {
  if (!selectedJob.value) return
  isSavingMemo.value = true
  try {
    await updateJobMeta(selectedJob.value.id, { memo: memoEdit.value })
    selectedJob.value.memo = memoEdit.value
    memoOriginal.value = memoEdit.value
    const job = jobs.value.find(j => j.id === selectedJob.value.id)
    if (job) job.memo = memoEdit.value
    ElMessage.success('메모 저장됨')
  } catch { ElMessage.error('저장 실패') }
  finally { isSavingMemo.value = false }
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

let pollTimer = null

function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
}

function startPollingIfNeeded() {
  const hasActive = jobs.value.some(j => j.status === 'processing' || j.status === 'pending')
  if (hasActive && !pollTimer) {
    pollTimer = setInterval(async () => {
      try {
        const { data } = await listJobs()
        jobs.value = data.sort((a, b) => new Date(b.createdAt) - new Date(a.createdAt))
        if (!jobs.value.some(j => j.status === 'processing' || j.status === 'pending')) {
          stopPolling()
        }
      } catch { /* 폴링 실패는 무시 */ }
    }, 5000)
  }
}

async function load() {
  loading.value = true
  try {
    const { data } = await listJobs()
    jobs.value = data.sort((a, b) => new Date(b.createdAt) - new Date(a.createdAt))
    startPollingIfNeeded()
  } catch { ElMessage.error('목록 로딩 실패') }
  finally { loading.value = false }
}

onUnmounted(stopPolling)

async function download(row) {
  try {
    const { data } = await downloadZip(row.id)
    const url = URL.createObjectURL(data)
    const a = document.createElement('a')
    a.href = url; a.download = `${row.id}.zip`; a.click()
    URL.revokeObjectURL(url)
  } catch { ElMessage.error('다운로드 실패') }
}

onMounted(load)
</script>

<style scoped>
.page-wrap { padding: 32px 40px 60px; }

/* header */
.page-top { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 24px; }
.page-title { font-size: 23px; font-weight: 800; letter-spacing: -0.5px; color: #191F28; }
.title-star { font-size: 15px; color: #7C3AED; margin-left: 4px; }
.page-desc  { margin-top: 5px; font-size: 14px; color: #8B95A1; }
.refresh-btn {
  display: flex; align-items: center; gap: 6px;
  padding: 9px 18px; border-radius: 10px; border: 1.5px solid #E5E8EB;
  background: #fff; font-size: 14px; font-weight: 600; color: #4E5968;
  cursor: pointer; font-family: inherit; white-space: nowrap; transition: all 0.12s;
}
.refresh-btn:hover { border-color: #C4CAD4; color: #191F28; }
.refresh-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.refresh-ico { font-size: 15px; }

/* stats */
.stats-row { display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px; margin-bottom: 20px; }

.stat-card {
  background: #fff; border-radius: 14px; padding: 18px 20px;
  box-shadow: 0 1px 4px rgba(0,0,0,0.05); display: flex; gap: 14px; align-items: flex-start;
  border: 1px solid #F0F2F4;
  transition: transform 0.18s ease, box-shadow 0.18s ease;
}
.stat-card:hover {
  transform: translateY(-3px);
  box-shadow: 0 6px 20px rgba(0,0,0,0.09);
}
.stat-ico {
  width: 38px; height: 38px; border-radius: 10px; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center; font-size: 19px;
}
.stat-label { font-size: 13px; color: #8B95A1; font-weight: 500; margin-bottom: 4px; }
.stat-num   { font-size: 23px; font-weight: 800; color: #191F28; letter-spacing: -0.5px; line-height: 1.1; margin-bottom: 3px; }
.stat-sub   { font-size: 12px; color: #B0B8C1; }

/* AI stat card */
.ai-card {
  background: linear-gradient(135deg, #6D28D9, #3B82F6) !important;
  border: none !important; color: #fff; justify-content: space-between; align-items: center;
}
.ai-card-inner { flex: 1; }
.ai-card-head { font-size: 13px; font-weight: 700; color: rgba(255,255,255,0.85); margin-bottom: 8px; display: flex; gap: 5px; align-items: center; }
.ai-card-msg  { font-size: 14px; font-weight: 600; color: #fff; line-height: 1.4; }
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
  font-size: 14px; font-family: inherit; outline: none; color: #191F28;
  transition: border-color 0.12s;
}
.search-input:focus { border-color: #7C3AED; box-shadow: 0 0 0 3px rgba(124,58,237,0.08); }
.search-input::placeholder { color: #C4CAD0; }
.filter-select {
  padding: 9px 12px; border: 1.5px solid #EAEDF0; border-radius: 10px;
  font-size: 14px; font-family: inherit; color: #4E5968; background: #fff;
  outline: none; cursor: pointer;
}
.filter-select:focus { border-color: #7C3AED; }
.reset-btn {
  padding: 9px 14px; border-radius: 10px; border: 1.5px solid #EAEDF0; background: #fff;
  font-size: 14px; color: #6B7684; cursor: pointer; font-family: inherit; white-space: nowrap;
}
.reset-btn:hover { color: #7C3AED; border-color: #7C3AED; }
.filter-right { margin-left: auto; }
.result-cnt { font-size: 14px; color: #8B95A1; font-weight: 500; }

/* table */
.table-wrap { background: #fff; border-radius: 14px; border: 1px solid #EAEDF0; overflow: hidden; margin-bottom: 16px; }
.tbl-empty  { padding: 60px; text-align: center; color: #B0B8C1; font-size: 15px; }

.tbl-head, .tbl-row {
  display: grid;
  grid-template-columns: 40px 260px 90px 1fr 180px 80px 140px 130px;
  align-items: center; padding: 0 20px; gap: 8px;
}
.tbl-head {
  background: #F8F9FA; border-bottom: 1px solid #EAEDF0;
  height: 42px; font-size: 13px; font-weight: 600; color: #8B95A1;
}
.tbl-head span { cursor: pointer; user-select: none; display: flex; align-items: center; gap: 3px; }
.tbl-head span:hover { color: #4E5968; }
.sort-ico { font-size: 11px; opacity: 0.5; }
.tbl-row {
  height: 52px; border-bottom: 1px solid #F5F6F8; font-size: 14px; color: #191F28;
  border-left: 3px solid transparent;
  transition: background 0.15s ease, border-color 0.15s ease;
}
.tbl-row:last-child { border-bottom: none; }
.tbl-row:hover { background: #FAFBFF; }
.c-dl { display: flex; align-items: center; gap: 6px; }
.preview-btn {
  width: 30px; height: 28px; border-radius: 7px; border: 1.5px solid #DDD6FE;
  background: #F5F3FF; color: #7C3AED; font-size: 14px;
  cursor: pointer; display: inline-flex; align-items: center; justify-content: center;
  transition: all 0.12s; flex-shrink: 0;
}
.preview-btn:hover { background: #7C3AED; border-color: #7C3AED; }

/* 상태별 좌측 accent bar */
.tbl-row.status-done       { border-left-color: #059669; }
.tbl-row.status-fail       { border-left-color: #DC2626; }
.tbl-row.status-processing { border-left-color: #D97706; }
.tbl-row.status-pending    { border-left-color: #D1D8E0; }

.job-id { font-family: monospace; font-size: 13px; color: #6B7684; }
.fw { font-weight: 500; }
.c-ad, .c-camp { user-select: none; }
.gray { color: #8B95A1; font-size: 13px; }

.media-tag {
  display: inline-block; padding: 2px 8px; border-radius: 100px;
  font-size: 12px; font-weight: 600; margin: 1px;
}
.media-tag.naver   { background: #E6F9EE; color: #03C75A; }
.media-tag.google  { background: #EAF1FE; color: #4285F4; }
.media-tag.meta    { background: #E8F0FD; color: #1877F2; }
.media-tag.kakao   { background: #FEF9E7; color: #B8960C; }
.media-tag         { background: #F2F4F6; color: #6B7684; }

.badge {
  display: inline-flex; align-items: center; gap: 5px;
  padding: 4px 10px; border-radius: 100px; font-size: 13px; font-weight: 600;
}
.badge.done       { background: #D1FAE5; color: #059669; }
.badge.fail       { background: #FEE2E2; color: #DC2626; }
.badge.pending    { background: #F3F4F6; color: #6B7684; }
.badge.processing { background: #FFF8E6; color: #D97706; }

.badge-spinner {
  display: inline-block;
  width: 10px; height: 10px; flex-shrink: 0;
  border: 1.5px solid currentColor;
  border-top-color: transparent;
  border-radius: 50%;
  animation: badge-spin 0.75s linear infinite;
}
@keyframes badge-spin { to { transform: rotate(360deg); } }

.dl-btn {
  padding: 5px 12px; border-radius: 7px; border: 1.5px solid #7C3AED;
  background: #fff; color: #7C3AED; font-size: 13px; font-weight: 700;
  cursor: pointer; font-family: inherit; transition: all 0.12s;
}
.dl-btn:hover { background: #7C3AED; color: #fff; }
.err-txt { font-size: 13px; color: #DC2626; font-weight: 600; cursor: pointer; text-decoration: underline; }
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
  font-size: 16px; font-weight: 700; color: #191F28;
}
.modal-close {
  background: none; border: none; font-size: 17px; color: #8B95A1;
  cursor: pointer; line-height: 1; padding: 2px 4px;
}
.modal-close:hover { color: #191F28; }
.modal-body { padding: 20px; display: flex; flex-direction: column; gap: 14px; }
.error-tbl { width: 100%; border-collapse: collapse; font-size: 14px; }
.error-tbl th {
  text-align: left; width: 90px; padding: 7px 0; color: #8B95A1;
  font-weight: 600; vertical-align: top; white-space: nowrap;
}
.error-tbl td { padding: 7px 0; color: #191F28; word-break: break-all; }
.error-tbl tr { border-bottom: 1px solid #F5F6F8; }
.error-tbl tr:last-child { border-bottom: none; }
.mono { font-family: monospace; font-size: 13px; }
.err-cell { color: #DC2626; }
.invalid-section-title { font-size: 14px; font-weight: 700; color: #374151; margin-bottom: 6px; }
.invalid-row { background: #FFF5F5; border-radius: 7px; padding: 8px 12px; margin-bottom: 4px; }
.invalid-name { font-size: 14px; font-weight: 600; color: #374151; display: block; margin-bottom: 2px; }
.invalid-msg  { font-size: 13px; color: #EF4444; }
.dash    { color: #D1D8E0; }

/* 체크박스 */
.c-chk { display: flex; align-items: center; justify-content: center; }
.chk   { width: 16px; height: 16px; cursor: pointer; accent-color: #7C3AED; }
.row-checked { background: #F5F0FF !important; }
.row-selected { background: #EDE9FF !important; border-left-color: #7C3AED !important; }

/* 선택삭제 버튼 */
.del-sel-btn {
  padding: 9px 16px; border-radius: 10px; border: 1.5px solid #DC2626;
  background: #fff; color: #DC2626; font-size: 14px; font-weight: 700;
  cursor: pointer; font-family: inherit; white-space: nowrap; transition: all 0.12s;
}
.del-sel-btn:hover { background: #DC2626; color: #fff; }

/* 인라인 편집 */
.tbl-row.row-editing { height: auto; align-items: start; padding-top: 12px; padding-bottom: 10px; }
.inline-edit {
  width: 100%; padding: 4px 8px; border: 1.5px solid #7C3AED;
  border-radius: 6px; font-size: 14px; font-family: inherit;
  outline: none; box-shadow: 0 0 0 3px rgba(124,58,237,0.1);
}
.edit-btns {
  display: flex; gap: 6px; margin-top: 7px; justify-content: flex-end;
}
.edit-cancel-btn {
  padding: 4px 12px; border-radius: 5px; border: 1.5px solid #D1D8E0;
  background: #fff; color: #6B7684; font-size: 12px; font-weight: 700;
  cursor: pointer; font-family: inherit; letter-spacing: 0.3px;
  transition: all 0.1s;
}
.edit-cancel-btn:hover { border-color: #B0B8C1; color: #191F28; }
.edit-update-btn {
  padding: 4px 12px; border-radius: 5px; border: none;
  background: #7C3AED; color: #fff; font-size: 12px; font-weight: 700;
  cursor: pointer; font-family: inherit; letter-spacing: 0.3px;
  transition: background 0.1s;
}
.edit-update-btn:hover { background: #6D28D9; }

/* 메모 섹션 */
.dp-memo-section {
  margin: 0 16px 14px; display: flex; flex-direction: column; gap: 8px; flex-shrink: 0;
}
.dp-memo-head {
  font-size: 13px; font-weight: 700; color: #9B8EC4;
  text-transform: uppercase; letter-spacing: 0.4px;
}
.dp-memo-area {
  width: 100%; min-height: 80px; padding: 10px 12px;
  border: 1.5px solid #EDE9FF; border-radius: 10px;
  font-size: 14px; font-family: inherit; color: #191F28;
  resize: vertical; outline: none; transition: border-color 0.12s;
  background: #fff;
}
.dp-memo-area:focus { border-color: #7C3AED; box-shadow: 0 0 0 3px rgba(124,58,237,0.08); }
.dp-memo-area::placeholder { color: #C4B5FD; }
.dp-memo-save {
  align-self: flex-end; padding: 7px 18px; border-radius: 8px;
  border: none; background: #7C3AED; color: #fff;
  font-size: 14px; font-weight: 600; cursor: pointer; font-family: inherit;
  transition: background 0.12s;
}
.dp-memo-save:hover:not(:disabled) { background: #6D28D9; }
.dp-memo-save:disabled { opacity: 0.6; cursor: not-allowed; }
.more-btn {
  width: 28px; height: 28px; border-radius: 6px; border: 1.5px solid #EAEDF0;
  background: #fff; color: #B0B8C1; cursor: pointer; font-size: 17px;
  display: inline-flex; align-items: center; justify-content: center;
  margin-left: 6px; font-family: inherit;
}
.more-btn:hover { border-color: #C4CAD4; color: #6B7684; }

/* 라이트박스 */
.lightbox-overlay {
  position: fixed; inset: 0; background: rgba(0,0,0,0.82);
  display: flex; align-items: center; justify-content: center; z-index: 2000;
}
.lightbox-box {
  position: relative; max-width: 90vw; max-height: 90vh;
  display: flex; flex-direction: column; align-items: center; gap: 12px;
}
.lightbox-close {
  position: absolute; top: -36px; right: 0;
  background: none; border: none; color: #fff; font-size: 21px;
  cursor: pointer; padding: 4px 8px; opacity: 0.7;
}
.lightbox-close:hover { opacity: 1; }
.lightbox-img {
  max-width: 85vw; max-height: 80vh; object-fit: contain;
  border-radius: 10px; display: block;
}
.lightbox-info { display: flex; align-items: center; gap: 12px; }
.lightbox-name { font-size: 14px; color: rgba(255,255,255,0.85); font-weight: 600; }
.lightbox-size { font-size: 13px; color: rgba(255,255,255,0.5); }

/* 선택된 행 강조 */
.row-selected { background: #F0EBFF !important; border-left-color: #7C3AED !important; }

/* 우측 디테일 패널 */
/* ── 우측 패널 ─────────────────────────────── */
.panel-overlay {
  position: fixed; inset: 0; background: rgba(15,10,30,0.35);
  backdrop-filter: blur(3px); z-index: 1000;
}
.detail-panel {
  position: fixed; top: 56px; right: 0; height: calc(100vh - 56px); width: 440px;
  background: #F8F7FF;
  box-shadow: -6px 0 40px rgba(124,58,237,0.12), -1px 0 0 rgba(124,58,237,0.08);
  display: flex; flex-direction: column; z-index: 1001;
  transform: translateX(100%); transition: transform 0.26s cubic-bezier(0.4,0,0.2,1);
  overflow: hidden;
}
.detail-panel.open { transform: translateX(0); }

/* 상단 그라디언트 바 */
.dp-accent-strip {
  height: 4px; flex-shrink: 0;
  background: linear-gradient(90deg, #7C3AED 0%, #3B82F6 60%, #06B6D4 100%);
}

/* 헤더 */
.dp-head {
  display: flex; align-items: flex-start; justify-content: space-between;
  padding: 16px 20px 14px; background: #fff;
  border-bottom: 1px solid #EDE9FF; flex-shrink: 0; gap: 12px;
}
.dp-head-left { display: flex; flex-direction: row; align-items: center; gap: 8px; min-width: 0; flex: 1; }
.dp-job-id {
  font-family: monospace; font-size: 12px; color: #7C3AED;
  background: #F3EEFF; border: 1px solid #DDD6FE;
  padding: 4px 9px; border-radius: 6px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  display: block; max-width: 340px;
}
.dp-close {
  background: none; border: 1.5px solid #EAEDF0; font-size: 15px; color: #8B95A1;
  cursor: pointer; padding: 5px 8px; border-radius: 8px; flex-shrink: 0;
  line-height: 1; transition: all 0.12s;
}
.dp-close:hover { background: #F3EEFF; border-color: #7C3AED; color: #7C3AED; }

/* 메타 */
.dp-meta {
  margin: 14px 16px; padding: 14px 16px; flex-shrink: 0;
  background: #fff; border: 1px solid #EDE9FF; border-radius: 14px;
  display: flex; flex-direction: column; gap: 10px;
}
.dp-meta-row { display: flex; align-items: center; gap: 10px; }
.dp-meta-label {
  font-size: 12px; color: #9B8EC4; font-weight: 600;
  text-transform: uppercase; letter-spacing: 0.4px;
  width: 44px; flex-shrink: 0;
}
.dp-meta-val { font-size: 14px; color: #191F28; font-weight: 600; word-break: break-all; }
.dp-meta-val.gray { color: #8B95A1; font-weight: 400; }

/* 섹션 타이틀 */
.dp-section-title {
  padding: 4px 20px 10px; font-size: 14px; font-weight: 700;
  color: #191F28; flex-shrink: 0; display: flex; align-items: center; gap: 6px;
}
.dp-cnt {
  font-size: 12px; font-weight: 600; color: #fff;
  background: linear-gradient(135deg, #7C3AED, #3B82F6);
  padding: 2px 7px; border-radius: 20px; margin-left: 2px;
}

/* 결과 그리드 */
.dp-results {
  flex: 1; overflow-y: auto; padding: 0 16px 16px;
  display: grid; grid-template-columns: 1fr 1fr; gap: 12px; align-content: start;
  scrollbar-width: thin; scrollbar-color: #DDD6FE transparent;
}
.dp-results::-webkit-scrollbar { width: 4px; }
.dp-results::-webkit-scrollbar-track { background: transparent; }
.dp-results::-webkit-scrollbar-thumb { background: #DDD6FE; border-radius: 4px; }

.dp-empty { grid-column: 1/-1; padding: 40px 0; text-align: center; color: #C4B5FD; font-size: 14px; }

.dp-result-card {
  border: 1.5px solid #EDE9FF; border-radius: 14px; overflow: hidden;
  background: #fff; box-shadow: 0 2px 8px rgba(124,58,237,0.06);
  transition: border-color 0.15s, box-shadow 0.15s;
}
.dp-result-card:hover {
  border-color: #7C3AED;
  box-shadow: 0 4px 16px rgba(124,58,237,0.15);
}

.dp-thumb-wrap {
  position: relative; width: 100%; aspect-ratio: 3/2; overflow: hidden;
  background: #F0EDFF; display: flex; align-items: center; justify-content: center;
}
.dp-thumb { width: 100%; height: 100%; object-fit: contain; display: block; }

.dp-card-hover {
  position: absolute; inset: 0; background: rgba(20,10,50,0.55);
  display: flex; align-items: center; justify-content: center; gap: 6px;
  opacity: 0; transition: opacity 0.16s;
}
.dp-thumb-wrap:hover .dp-card-hover { opacity: 1; }

.dp-card-action {
  width: 34px; height: 34px; border-radius: 9px;
  background: rgba(255,255,255,0.15); border: 1.5px solid rgba(255,255,255,0.55);
  color: #fff; font-size: 16px; cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  transition: background 0.12s, transform 0.1s;
}
.dp-card-action:hover { background: rgba(255,255,255,0.3); transform: scale(1.08); }

.dp-result-label {
  padding: 8px 10px 2px; font-size: 11.5px; font-weight: 700;
  color: #3D2D6E; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.dp-result-size {
  padding: 0 10px 4px; font-size: 10.5px; color: #9B8EC4; font-weight: 500;
}
.dp-result-file {
  padding: 0 10px 8px; font-size: 10px; color: #C4B5FD; font-family: monospace;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}

/* 액션 영역 */
.dp-actions {
  padding: 14px 16px 18px; border-top: 1px solid #EDE9FF; flex-shrink: 0;
}
.dp-btn {
  width: 100%; padding: 13px; border-radius: 12px; font-size: 15px; font-weight: 700;
  cursor: pointer; font-family: inherit; transition: all 0.14s; border: none;
  letter-spacing: 0.2px;
}
.dp-btn-primary {
  background: linear-gradient(135deg, #7C3AED 0%, #3B82F6 100%);
  color: #fff; box-shadow: 0 4px 16px rgba(124,58,237,0.35);
}
.dp-btn-primary:hover { opacity: 0.9; box-shadow: 0 6px 20px rgba(124,58,237,0.45); transform: translateY(-1px); }
.dp-btn-ghost { background: #fff; border: 1.5px solid #EAEDF0; color: #4E5968; }
.dp-btn-ghost:hover { border-color: #7C3AED; color: #7C3AED; }

/* pagination */
.pagination { display: flex; align-items: center; gap: 8px; justify-content: center; flex-wrap: wrap; }
.pg-info    { font-size: 14px; color: #8B95A1; margin-right: 8px; }
.pg-btns    { display: flex; gap: 4px; align-items: center; }
.pg-btn {
  width: 32px; height: 32px; border-radius: 8px; border: 1.5px solid #EAEDF0;
  background: #fff; font-size: 14px; color: #4E5968; cursor: pointer; font-family: inherit;
  display: flex; align-items: center; justify-content: center; transition: all 0.1s;
}
.pg-btn:hover  { border-color: #7C3AED; color: #7C3AED; }
.pg-btn.active { background: #7C3AED; border-color: #7C3AED; color: #fff; font-weight: 700; }
.pg-arrow {
  width: 32px; height: 32px; border-radius: 8px; border: 1.5px solid #EAEDF0;
  background: #fff; font-size: 17px; color: #6B7684; cursor: pointer;
  display: flex; align-items: center; justify-content: center;
}
.pg-arrow:hover:not(:disabled) { border-color: #7C3AED; color: #7C3AED; }
.pg-arrow:disabled { opacity: 0.35; cursor: not-allowed; }
.pg-ellipsis { color: #B0B8C1; font-size: 15px; padding: 0 4px; }
.pg-size {
  margin-left: 8px; padding: 7px 10px; border: 1.5px solid #EAEDF0; border-radius: 8px;
  font-size: 13px; font-family: inherit; color: #6B7684; background: #fff; outline: none; cursor: pointer;
}

/* date filter */
.date-preset-group { display: flex; gap: 4px; flex-shrink: 0; }
.preset-btn {
  padding: 7px 12px; border-radius: 8px; border: 1.5px solid #EAEDF0;
  background: #fff; font-size: 13px; font-weight: 600; color: #6B7684;
  cursor: pointer; font-family: inherit; white-space: nowrap; transition: all 0.12s;
}
.preset-btn:hover { border-color: #7C3AED; color: #7C3AED; }
.preset-btn.active { border-color: #7C3AED; background: #7C3AED; color: #fff; }

.date-range-group { display: flex; align-items: center; gap: 4px; flex-shrink: 0; }
.date-input {
  padding: 7px 10px; border: 1.5px solid #EAEDF0; border-radius: 8px;
  font-size: 13px; font-family: inherit; color: #4E5968; background: #fff;
  outline: none; cursor: pointer; transition: border-color 0.12s;
}
.date-input:focus { border-color: #7C3AED; }
.date-sep { font-size: 13px; color: #B0B8C1; }
</style>
