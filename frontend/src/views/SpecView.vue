<template>
  <el-card shadow="never">
    <template #header>
      <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px;">
        <span style="font-size:16px; font-weight:600;">규격 관리</span>
        <div style="display:flex; gap:8px;">
          <el-select v-model="filterMedia" placeholder="매체 필터" clearable style="width:140px;" @change="load">
            <el-option v-for="m in mediaList" :key="m" :label="m" :value="m" />
          </el-select>
          <el-button type="warning" @click="initSpecs(false)">기본 규격 삽입</el-button>
          <el-button type="danger"  @click="initSpecs(true)">전체 초기화 후 재삽입</el-button>
          <el-button type="primary" :icon="Plus" @click="openAdd">규격 추가</el-button>
        </div>
      </div>
    </template>

    <el-table :data="specs" v-loading="loading" stripe border>
      <el-table-column label="매체" prop="media" width="100" />
      <el-table-column label="지면명" prop="placementName" />
      <el-table-column label="가로" prop="width"  width="70" align="right" />
      <el-table-column label="세로" prop="height" width="70" align="right" />
      <el-table-column label="비율" prop="aspectRatio" width="90" />
      <el-table-column label="활성" width="70" align="center">
        <template #default="{ row }">
          <el-tag :type="row.active ? 'success' : 'info'" size="small">
            {{ row.active ? 'ON' : 'OFF' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="삭제" width="70" align="center">
        <template #default="{ row }">
          <el-button type="danger" size="small" :icon="Delete" circle @click="remove(row.id)" />
        </template>
      </el-table-column>
    </el-table>
  </el-card>

  <!-- 규격 추가 다이얼로그 -->
  <el-dialog v-model="dialog" title="규격 추가" width="400px">
    <el-form :model="newSpec" label-width="80px">
      <el-form-item label="매체">
        <el-select v-model="newSpec.media" style="width:100%;">
          <el-option v-for="m in mediaList" :key="m" :label="m" :value="m" />
        </el-select>
      </el-form-item>
      <el-form-item label="지면명">
        <el-input v-model="newSpec.placementName" />
      </el-form-item>
      <el-form-item label="가로(px)">
        <el-input-number v-model="newSpec.width" :min="1" style="width:100%;" />
      </el-form-item>
      <el-form-item label="세로(px)">
        <el-input-number v-model="newSpec.height" :min="1" style="width:100%;" />
      </el-form-item>
      <el-form-item label="비율">
        <el-input v-model="newSpec.aspectRatio" placeholder="예: 1:1" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="dialog = false">취소</el-button>
      <el-button type="primary" @click="addSpec">추가</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { Plus, Delete } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listSpecs, saveSpec, deleteSpec, initSpecs as apiInitSpecs } from '../api/banner.js'

const specs       = ref([])
const loading     = ref(false)
const filterMedia = ref('')
const dialog      = ref(false)
const mediaList   = ['google', 'meta', 'naver', 'kakao', 'linkedin', 'tiktok']

const newSpec = reactive({ media: 'google', placementName: '', width: 1200, height: 628, aspectRatio: '' })

async function load() {
  loading.value = true
  try {
    const { data } = await listSpecs(filterMedia.value || undefined)
    specs.value = data
  } catch {
    ElMessage.error('규격 로딩 실패')
  } finally {
    loading.value = false
  }
}

async function remove(id) {
  await ElMessageBox.confirm('삭제하시겠습니까?', '확인', { type: 'warning' })
  await deleteSpec(id)
  ElMessage.success('삭제됐습니다.')
  load()
}

async function initSpecs(reset) {
  const msg = reset ? '전체 삭제 후 기본 규격을 다시 삽입합니다.' : '기본 규격을 삽입합니다.'
  await ElMessageBox.confirm(msg, '확인', { type: reset ? 'warning' : 'info' })
  const { data } = await apiInitSpecs(reset)
  ElMessage.success(`${data.inserted}개 삽입 완료`)
  load()
}

function openAdd() {
  Object.assign(newSpec, { media: 'google', placementName: '', width: 1200, height: 628, aspectRatio: '' })
  dialog.value = true
}

async function addSpec() {
  await saveSpec({ ...newSpec, active: true })
  ElMessage.success('추가됐습니다.')
  dialog.value = false
  load()
}

onMounted(load)
</script>
