<template>
  <div>
    <div class="page-header">
      <div>
        <h1 class="page-title">규격 관리</h1>
        <p class="page-desc">매체별 배너 규격을 관리합니다.</p>
      </div>
      <div class="header-actions">
        <select class="filter-select" v-model="filterMedia" @change="load">
          <option value="">매체 전체</option>
          <option v-for="m in mediaList" :key="m" :value="m">{{ m }}</option>
        </select>
        <button class="btn-outline" @click="initSpecs(false)">기본 규격 삽입</button>
        <button class="btn-danger" @click="initSpecs(true)">초기화 후 재삽입</button>
        <button class="btn-primary" @click="openAdd">+ 규격 추가</button>
      </div>
    </div>

    <div class="card" v-loading="loading">
      <div class="table-head">
        <span class="tc-media">매체</span>
        <span class="tc-name">지면명</span>
        <span class="tc-size">가로</span>
        <span class="tc-size">세로</span>
        <span class="tc-ratio">비율</span>
        <span class="tc-active">활성</span>
        <span class="tc-del">삭제</span>
      </div>
      <div v-if="specs.length === 0 && !loading" class="empty">규격 데이터가 없습니다.</div>
      <div v-for="row in specs" :key="row.id" class="table-row">
        <span class="tc-media">
          <span class="media-tag">{{ row.media }}</span>
        </span>
        <span class="tc-name">{{ row.placementName }}</span>
        <span class="tc-size num">{{ row.width }}</span>
        <span class="tc-size num">{{ row.height }}</span>
        <span class="tc-ratio">{{ row.aspectRatio }}</span>
        <span class="tc-active">
          <span class="badge" :class="row.active ? 'on' : 'off'">{{ row.active ? 'ON' : 'OFF' }}</span>
        </span>
        <span class="tc-del">
          <button class="del-btn" @click="remove(row.id)">✕</button>
        </span>
      </div>
    </div>

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
        <button class="btn-outline" @click="dialog = false">취소</button>
        <button class="btn-primary" style="margin-left:8px;" @click="addSpec">추가</button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
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

<style scoped>
.page-header {
  display: flex; justify-content: space-between; align-items: flex-start;
  margin-bottom: 24px; flex-wrap: wrap; gap: 12px;
}
.page-title { font-size: 22px; font-weight: 700; letter-spacing: -0.5px; }
.page-desc { margin-top: 6px; font-size: 14px; color: #8B95A1; }

.header-actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }

.filter-select {
  padding: 8px 12px;
  border-radius: 10px;
  border: 1.5px solid #E5E8EB;
  background: #fff;
  font-size: 13px;
  color: #4E5968;
  font-family: inherit;
  cursor: pointer;
  outline: none;
}
.filter-select:focus { border-color: #3182F6; }

.btn-primary, .btn-outline, .btn-danger {
  padding: 9px 16px;
  border-radius: 10px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  font-family: inherit;
  transition: all 0.12s;
  border: 1.5px solid transparent;
}
.btn-primary { background: #3182F6; color: #fff; }
.btn-primary:hover { background: #1B6EF3; }
.btn-outline { background: #fff; color: #4E5968; border-color: #E5E8EB; }
.btn-outline:hover { border-color: #C4CAD4; color: #191F28; }
.btn-danger { background: #fff; color: #FF3B30; border-color: #FFD0CC; }
.btn-danger:hover { background: #FFF0EE; }

.card {
  background: #fff;
  border-radius: 16px;
  box-shadow: 0 1px 4px rgba(0,0,0,0.06);
  overflow: hidden;
}

.table-head, .table-row {
  display: grid;
  grid-template-columns: 80px 1fr 60px 60px 72px 58px 52px;
  align-items: center;
  padding: 12px 20px;
  gap: 8px;
}
.table-head {
  background: #F8F9FA;
  font-size: 12px;
  font-weight: 600;
  color: #8B95A1;
  border-bottom: 1px solid #F0F2F4;
}
.table-row {
  border-bottom: 1px solid #F0F2F4;
  font-size: 13px;
  color: #191F28;
  transition: background 0.1s;
}
.table-row:last-child { border-bottom: none; }
.table-row:hover { background: #FAFBFC; }

.empty {
  padding: 60px;
  text-align: center;
  color: #8B95A1;
  font-size: 14px;
}

.media-tag {
  display: inline-block;
  padding: 2px 10px;
  border-radius: 100px;
  background: #F2F4F6;
  font-size: 12px;
  font-weight: 500;
  color: #4E5968;
}
.num { text-align: right; color: #4E5968; }

.badge {
  display: inline-block;
  padding: 3px 10px;
  border-radius: 100px;
  font-size: 12px;
  font-weight: 600;
}
.badge.on  { background: #E8FBF3; color: #0DC780; }
.badge.off { background: #F0F2F4; color: #8B95A1; }

.del-btn {
  width: 28px; height: 28px;
  border-radius: 8px;
  border: 1.5px solid #FFD0CC;
  background: #fff;
  color: #FF3B30;
  font-size: 12px;
  cursor: pointer;
  transition: all 0.12s;
  display: flex; align-items: center; justify-content: center;
}
.del-btn:hover { background: #FFF0EE; }
</style>
