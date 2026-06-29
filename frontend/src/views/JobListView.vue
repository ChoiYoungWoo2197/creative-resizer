<template>
  <div class="page-wrap">
    <div class="page-header">
      <div>
        <h1 class="page-title">작업 목록</h1>
        <p class="page-desc">생성된 배너 작업을 확인하고 다운로드할 수 있습니다.</p>
      </div>
      <button class="refresh-btn" @click="load" :disabled="loading">
        {{ loading ? '로딩 중...' : '새로고침' }}
      </button>
    </div>

    <div class="card">
      <div v-if="loading && jobs.length === 0" class="empty">불러오는 중...</div>
      <div v-else-if="jobs.length === 0" class="empty">작업 내역이 없습니다.</div>
      <template v-else>
        <div class="job-head">
          <span class="col-id">작업 ID</span>
          <span class="col-ad">광고주</span>
          <span class="col-camp">캠페인</span>
          <span class="col-media">매체</span>
          <span class="col-status">상태</span>
          <span class="col-date">생성일</span>
          <span class="col-dl">다운로드</span>
        </div>
        <div v-for="job in jobs" :key="job.id" class="job-row">
          <span class="col-id mono">{{ job.id.slice(0,8) }}…</span>
          <span class="col-ad">{{ job.advertiser }}</span>
          <span class="col-camp">{{ job.campaignName }}</span>
          <span class="col-media">
            <span v-for="m in job.targetMedia" :key="m" class="media-tag">{{ m }}</span>
          </span>
          <span class="col-status">
            <span class="badge" :class="job.status">{{ statusLabel(job.status) }}</span>
          </span>
          <span class="col-date">{{ formatDate(job.createdAt) }}</span>
          <span class="col-dl">
            <button v-if="job.status === 'done'" class="dl-btn" @click="download(job)">ZIP ↓</button>
            <span v-else-if="job.status === 'fail'" class="fail-tip" :title="job.errorMessage">오류</span>
            <span v-else class="dash">—</span>
          </span>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { listJobs, downloadZip } from '../api/banner.js'

const jobs    = ref([])
const loading = ref(false)

const statusLabel = (s) => ({ pending: '대기', processing: '처리중', done: '완료', fail: '실패' }[s] ?? s)
const formatDate  = (d) => d ? new Date(d).toLocaleString('ko-KR', { month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit' }) : '-'

async function load() {
  loading.value = true
  try {
    const { data } = await listJobs()
    jobs.value = data.sort((a, b) => new Date(b.createdAt) - new Date(a.createdAt))
  } catch {
    ElMessage.error('목록 로딩 실패')
  } finally {
    loading.value = false
  }
}

async function download(row) {
  try {
    const { data } = await downloadZip(row.id)
    const url = URL.createObjectURL(data)
    const a = document.createElement('a')
    a.href = url
    a.download = `${row.advertiser}_${row.campaignName}.zip`
    a.click()
    URL.revokeObjectURL(url)
  } catch {
    ElMessage.error('다운로드 실패')
  }
}

onMounted(load)
</script>

<style scoped>
.page-header {
  display: flex; justify-content: space-between; align-items: flex-start;
  margin-bottom: 24px;
}
.page-title { font-size: 22px; font-weight: 700; letter-spacing: -0.5px; }
.page-desc { margin-top: 6px; font-size: 14px; color: #8B95A1; }

.refresh-btn {
  padding: 9px 18px;
  border-radius: 10px;
  border: 1.5px solid #E5E8EB;
  background: #fff;
  font-size: 13px;
  font-weight: 600;
  color: #4E5968;
  cursor: pointer;
  font-family: inherit;
  transition: all 0.12s;
  white-space: nowrap;
}
.refresh-btn:hover { border-color: #C4CAD4; color: #191F28; }
.refresh-btn:disabled { opacity: 0.5; cursor: not-allowed; }

.card {
  background: #fff;
  border-radius: 16px;
  box-shadow: 0 1px 4px rgba(0,0,0,0.06);
  overflow: hidden;
}

.empty {
  padding: 60px;
  text-align: center;
  color: #8B95A1;
  font-size: 14px;
}

.job-head, .job-row {
  display: grid;
  grid-template-columns: 110px 90px 1fr 180px 70px 120px 72px;
  align-items: center;
  padding: 12px 20px;
  gap: 8px;
}
.job-head {
  background: #F8F9FA;
  font-size: 12px;
  font-weight: 600;
  color: #8B95A1;
  border-bottom: 1px solid #F0F2F4;
}
.job-row {
  border-bottom: 1px solid #F0F2F4;
  font-size: 13px;
  color: #191F28;
  transition: background 0.1s;
}
.job-row:last-child { border-bottom: none; }
.job-row:hover { background: #FAFBFC; }

.mono { font-family: monospace; font-size: 12px; color: #6B7684; }

.media-tag {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 100px;
  background: #F2F4F6;
  font-size: 11px;
  font-weight: 500;
  color: #4E5968;
  margin: 2px 2px 2px 0;
}

.badge {
  display: inline-block;
  padding: 3px 10px;
  border-radius: 100px;
  font-size: 12px;
  font-weight: 600;
}
.badge.pending  { background: #F0F2F4; color: #6B7684; }
.badge.processing { background: #FFF8E6; color: #FF9F0A; }
.badge.done     { background: #E8FBF3; color: #0DC780; }
.badge.fail     { background: #FFF0EE; color: #FF3B30; }

.dl-btn {
  padding: 6px 12px;
  border-radius: 8px;
  border: 1.5px solid #3182F6;
  background: #fff;
  color: #3182F6;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  font-family: inherit;
  transition: all 0.12s;
}
.dl-btn:hover { background: #3182F6; color: #fff; }
.fail-tip { font-size: 12px; color: #FF3B30; font-weight: 600; cursor: help; }
.dash { color: #D1D8E0; }
.page-wrap { max-width: 1100px; margin: 0 auto; padding: 32px 28px 60px; }
</style>
