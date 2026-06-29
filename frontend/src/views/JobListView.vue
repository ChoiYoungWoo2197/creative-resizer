<template>
  <el-card shadow="never">
    <template #header>
      <div style="display:flex; justify-content:space-between; align-items:center;">
        <span style="font-size:16px; font-weight:600;">작업 목록</span>
        <el-button :icon="Refresh" @click="load" :loading="loading">새로고침</el-button>
      </div>
    </template>

    <el-table :data="jobs" v-loading="loading" stripe border style="width:100%;">
      <el-table-column label="작업 ID" prop="id" width="220" />
      <el-table-column label="광고주" prop="advertiser" width="120" />
      <el-table-column label="캠페인" prop="campaignName" />
      <el-table-column label="매체" width="200">
        <template #default="{ row }">
          <el-tag
            v-for="m in row.targetMedia" :key="m"
            size="small" style="margin:2px;"
          >{{ m }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="방식" prop="resizeMode" width="90" />
      <el-table-column label="상태" width="110" align="center">
        <template #default="{ row }">
          <el-tag :type="statusType(row.status)">{{ row.status }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="생성일" width="160">
        <template #default="{ row }">{{ formatDate(row.createdAt) }}</template>
      </el-table-column>
      <el-table-column label="다운로드" width="100" align="center">
        <template #default="{ row }">
          <el-button
            v-if="row.status === 'done'"
            type="primary" size="small" :icon="Download"
            @click="download(row)"
          />
          <el-tooltip v-else-if="row.status === 'fail'" :content="row.errorMessage" placement="top">
            <el-tag type="danger" size="small">오류</el-tag>
          </el-tooltip>
        </template>
      </el-table-column>
    </el-table>

    <div v-if="!loading && jobs.length === 0" style="text-align:center; padding:40px; color:#aaa;">
      작업 내역이 없습니다.
    </div>
  </el-card>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { Refresh, Download } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { listJobs, downloadZip } from '../api/banner.js'

const jobs    = ref([])
const loading = ref(false)

const statusType = (s) => ({ pending: 'info', processing: 'warning', done: 'success', fail: 'danger' }[s] ?? '')

const formatDate = (d) => d ? new Date(d).toLocaleString('ko-KR') : '-'

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
