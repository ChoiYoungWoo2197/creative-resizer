<template>
  <div class="page">
    <div class="page-header">
      <h1 class="page-title">배너 생성</h1>
      <p class="page-desc">PSD 파일을 업로드하고 원하는 사이즈를 선택하면 배너를 자동 생성합니다.</p>
    </div>

    <el-form :model="form" ref="formRef" :rules="rules">

      <!-- 업로드 + 캠페인 정보 -->
      <div class="card row-card">
        <el-form-item prop="psdFile" style="margin:0; flex:1;">
          <el-upload
            class="upload-zone"
            drag :auto-upload="false" accept=".psd" :limit="1"
            :on-change="onFileChange"
            :on-remove="() => { form.psdFile = null; fileList = [] }"
            :file-list="fileList"
          >
            <div class="upload-inner">
              <el-icon class="upload-ico"><UploadFilled /></el-icon>
              <div class="upload-label">PSD 파일 업로드</div>
              <div class="upload-sub">클릭하거나 드래그하세요 · .psd만 가능</div>
            </div>
          </el-upload>
        </el-form-item>

        <div class="info-fields">
          <el-form-item prop="advertiser" style="margin-bottom:12px;">
            <div class="field-label">광고주명</div>
            <el-input v-model="form.advertiser" placeholder="예: 삼성전자" />
          </el-form-item>
          <el-form-item prop="campaignName" style="margin-bottom:0;">
            <div class="field-label">캠페인명</div>
            <el-input v-model="form.campaignName" placeholder="예: 2024_summer" />
          </el-form-item>
        </div>
      </div>

      <!-- 사이즈 선택 -->
      <div class="card">
        <div class="section-top">
          <div class="card-title">사이즈 선택</div>
          <div class="sel-summary">
            <span v-if="selectedSpecIds.length === 0" class="sel-none">선택된 사이즈 없음</span>
            <span v-else class="sel-count"><b>{{ selectedSpecIds.length }}</b>개 선택됨</span>
            <button v-if="selectedSpecIds.length > 0" class="clear-btn" type="button" @click="selectedSpecIds = []">전체 해제</button>
          </div>
        </div>

        <div v-if="specsLoading" class="loading-msg">규격 불러오는 중...</div>
        <div v-else-if="Object.keys(groupedSpecs).length === 0" class="loading-msg">
          등록된 규격이 없습니다. 규격 관리에서 기본 규격을 삽입하세요.
        </div>
        <div v-else class="platforms">
          <div v-for="platform in platformOrder.filter(p => groupedSpecs[p])" :key="platform" class="platform-block">
            <div class="platform-head">
              <div class="platform-dot" :style="{ background: platformCfg[platform]?.color }" />
              <span class="platform-name">{{ platformCfg[platform]?.label ?? platform }}</span>
              <span class="platform-cnt">{{ groupedSpecs[platform].length }}종</span>
              <button
                type="button"
                class="toggle-all"
                :class="{ on: isPlatformAllSelected(platform) }"
                @click="togglePlatform(platform)"
              >{{ isPlatformAllSelected(platform) ? '전체 해제' : '전체 선택' }}</button>
            </div>

            <div class="spec-grid">
              <div
                v-for="spec in groupedSpecs[platform]"
                :key="spec.id"
                class="spec-card"
                :class="{ selected: selectedSpecIds.includes(spec.id) }"
                @click="toggleSpec(spec.id)"
              >
                <div class="ratio-wrap">
                  <div class="ratio-box" :style="getRatioBox(spec.width, spec.height)"
                    :class="{ 'box-selected': selectedSpecIds.includes(spec.id) }" />
                </div>
                <div class="spec-name">{{ spec.placementName }}</div>
                <div class="spec-dim">{{ spec.width }}×{{ spec.height }}</div>
                <div v-if="selectedSpecIds.includes(spec.id)" class="spec-check">✓</div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- 옵션 + 생성 -->
      <div class="card options-bar">
        <div class="opt-group">
          <span class="opt-label">리사이즈</span>
          <div class="opt-chips">
            <button v-for="r in resizeOptions" :key="r.value" type="button"
              class="opt-chip" :class="{ on: form.resizeMode === r.value }"
              @click="form.resizeMode = r.value">{{ r.label }}</button>
          </div>
        </div>
        <div class="opt-group">
          <span class="opt-label">포맷</span>
          <div class="opt-chips">
            <button v-for="f in formatOptions" :key="f.value" type="button"
              class="opt-chip" :class="{ on: form.outputFormat === f.value }"
              @click="form.outputFormat = f.value">{{ f.label }}</button>
          </div>
        </div>
        <button
          class="gen-btn"
          :class="{ loading }"
          :disabled="loading || selectedSpecIds.length === 0 || !form.psdFile"
          @click="submit"
        >
          {{ loading ? '생성 중...' : selectedSpecIds.length === 0 ? '사이즈를 선택하세요' : `배너 생성 (${selectedSpecIds.length}개)` }}
        </button>
      </div>

    </el-form>

    <!-- 완료 결과 -->
    <div v-if="result" class="result-card">
      <div class="result-head">
        <span class="result-check">✓</span>
        <span class="result-title">접수 완료 — 잠시 후 작업 목록에서 확인하세요</span>
      </div>
      <div class="result-meta">
        <span>{{ result.advertiser }} · {{ result.campaignName }}</span>
        <span class="result-media">{{ result.targetMedia?.join(', ') }}</span>
      </div>
      <button class="link-btn" @click="$router.push('/jobs')">작업 목록 →</button>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { UploadFilled } from '@element-plus/icons-vue'
import { uploadPsd, listSpecs } from '../api/banner.js'

const formRef = ref()
const loading    = ref(false)
const result     = ref(null)
const fileList   = ref([])
const allSpecs   = ref([])
const specsLoading = ref(true)
const selectedSpecIds = ref([])

const form = reactive({
  psdFile:     null,
  advertiser:  '',
  campaignName:'',
  resizeMode:  'cover',
  outputFormat:'png',
})

const rules = {
  psdFile:     [{ required: true, message: 'PSD 파일을 선택해주세요' }],
  advertiser:  [{ required: true, message: '광고주명을 입력해주세요' }],
  campaignName:[{ required: true, message: '캠페인명을 입력해주세요' }],
}

const platformOrder = ['google', 'meta', 'naver', 'kakao', 'linkedin', 'tiktok']

const platformCfg = {
  google:   { label: 'Google',   color: '#4285F4' },
  meta:     { label: 'Meta',     color: '#1877F2' },
  naver:    { label: 'Naver',    color: '#03C75A' },
  kakao:    { label: 'Kakao',    color: '#FFCD00' },
  linkedin: { label: 'LinkedIn', color: '#0A66C2' },
  tiktok:   { label: 'TikTok',  color: '#191F28' },
}

const resizeOptions = [
  { value: 'cover',   label: '꽉 채우기' },
  { value: 'contain', label: '전체 보이기' },
  { value: 'blur-bg', label: '블러 배경' },
]

const formatOptions = [
  { value: 'png',  label: 'PNG' },
  { value: 'jpg',  label: 'JPG' },
  { value: 'webp', label: 'WebP' },
]

const groupedSpecs = computed(() => {
  const map = {}
  for (const spec of allSpecs.value) {
    if (!map[spec.media]) map[spec.media] = []
    map[spec.media].push(spec)
  }
  return map
})

function getRatioBox(w, h) {
  const MAX = 44, MIN = 6
  const ratio = w / h
  let bw, bh
  if (ratio >= 1) {
    bw = MAX; bh = Math.max(MIN, Math.round(MAX / ratio))
  } else {
    bh = MAX; bw = Math.max(MIN, Math.round(MAX * ratio))
  }
  return { width: bw + 'px', height: bh + 'px' }
}

function toggleSpec(id) {
  const idx = selectedSpecIds.value.indexOf(id)
  if (idx >= 0) selectedSpecIds.value.splice(idx, 1)
  else selectedSpecIds.value.push(id)
}

function isPlatformAllSelected(platform) {
  const ids = (groupedSpecs.value[platform] ?? []).map(s => s.id)
  return ids.length > 0 && ids.every(id => selectedSpecIds.value.includes(id))
}

function togglePlatform(platform) {
  const ids = (groupedSpecs.value[platform] ?? []).map(s => s.id)
  if (isPlatformAllSelected(platform)) {
    selectedSpecIds.value = selectedSpecIds.value.filter(id => !ids.includes(id))
  } else {
    ids.forEach(id => { if (!selectedSpecIds.value.includes(id)) selectedSpecIds.value.push(id) })
  }
}

function onFileChange(file) {
  form.psdFile = file.raw
  fileList.value = [file]
}

async function submit() {
  await formRef.value.validate()
  if (selectedSpecIds.value.length === 0) {
    ElMessage.warning('사이즈를 1개 이상 선택해주세요.')
    return
  }

  const fd = new FormData()
  fd.append('psdFile', form.psdFile)
  fd.append('advertiser', form.advertiser)
  fd.append('campaignName', form.campaignName)
  selectedSpecIds.value.forEach(id => fd.append('specIds', id))
  fd.append('resizeMode', form.resizeMode)
  fd.append('outputFormat', form.outputFormat)

  loading.value = true
  try {
    const { data } = await uploadPsd(fd)
    result.value = data
    ElMessage.success('작업이 접수됐습니다.')
  } catch (e) {
    ElMessage.error('업로드 실패: ' + (e.response?.data?.message ?? e.message))
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  try {
    const { data } = await listSpecs()
    allSpecs.value = data
  } catch {
    ElMessage.error('규격 로딩 실패')
  } finally {
    specsLoading.value = false
  }
})
</script>

<style scoped>
.page-header { margin-bottom: 24px; }
.page-title { font-size: 22px; font-weight: 700; letter-spacing: -0.5px; }
.page-desc  { margin-top: 6px; font-size: 14px; color: #8B95A1; }

.card {
  background: #fff;
  border-radius: 16px;
  padding: 24px;
  margin-bottom: 10px;
  box-shadow: 0 1px 4px rgba(0,0,0,0.06);
}

/* Upload + info */
.row-card { display: flex; gap: 20px; align-items: flex-start; }
.upload-zone { flex: 1; }
.upload-zone :deep(.el-upload) { width: 100%; }
.upload-zone :deep(.el-upload-dragger) {
  width: 100%; height: 130px;
  border: 1.5px dashed #D1D8E0;
  border-radius: 12px;
  background: #FAFBFC;
  display: flex; align-items: center; justify-content: center;
  transition: all 0.15s;
}
.upload-zone :deep(.el-upload-dragger:hover) { border-color: #3182F6; background: #EEF4FF; }
.upload-inner { text-align: center; }
.upload-ico  { font-size: 30px; color: #B0B8C1; }
.upload-label { font-size: 14px; font-weight: 600; color: #4E5968; margin-top: 6px; }
.upload-sub  { font-size: 12px; color: #B0B8C1; margin-top: 3px; }

.info-fields { flex: 1; display: flex; flex-direction: column; justify-content: center; }
.field-label { font-size: 13px; font-weight: 500; color: #6B7684; margin-bottom: 6px; }
:deep(.el-input__wrapper) {
  border-radius: 10px !important; border: 1.5px solid #E5E8EB !important;
  box-shadow: none !important; padding: 10px 14px !important;
}
:deep(.el-input__wrapper:hover) { border-color: #C4CAD4 !important; }
:deep(.el-input__wrapper.is-focus) { border-color: #3182F6 !important; }
:deep(.el-input__inner) { font-size: 14px; font-family: inherit; }
:deep(.el-form-item__label) { display: none; }
:deep(.el-form-item__error) { font-size: 12px; color: #FF3B30; }

/* Size selection */
.section-top { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
.card-title  { font-size: 15px; font-weight: 700; }
.sel-summary { display: flex; align-items: center; gap: 10px; }
.sel-none    { font-size: 13px; color: #B0B8C1; }
.sel-count   { font-size: 13px; color: #3182F6; }
.sel-count b { font-weight: 700; }
.clear-btn   { background: none; border: none; color: #8B95A1; font-size: 13px; cursor: pointer; font-family: inherit; padding: 0; }
.clear-btn:hover { color: #FF3B30; }

.loading-msg { text-align: center; padding: 40px; color: #8B95A1; font-size: 14px; }

.platforms   { display: flex; flex-direction: column; gap: 24px; }

.platform-block {}

.platform-head {
  display: flex; align-items: center; gap: 8px;
  margin-bottom: 12px;
}
.platform-dot  { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.platform-name { font-size: 14px; font-weight: 700; color: #191F28; }
.platform-cnt  { font-size: 12px; color: #8B95A1; margin-right: auto; }
.toggle-all {
  padding: 4px 12px; border-radius: 100px;
  border: 1.5px solid #E5E8EB; background: #fff;
  font-size: 12px; font-weight: 500; color: #6B7684;
  cursor: pointer; font-family: inherit; transition: all 0.12s;
}
.toggle-all:hover { border-color: #3182F6; color: #3182F6; }
.toggle-all.on    { background: #3182F6; border-color: #3182F6; color: #fff; }

.spec-grid {
  display: flex; flex-wrap: wrap; gap: 8px;
}

.spec-card {
  position: relative;
  width: 100px;
  padding: 12px 10px 10px;
  border-radius: 12px;
  border: 1.5px solid #E5E8EB;
  background: #fff;
  cursor: pointer;
  transition: all 0.12s;
  text-align: center;
  user-select: none;
}
.spec-card:hover { border-color: #C4CAD4; background: #FAFBFC; }
.spec-card.selected { border-color: #3182F6; background: #EEF4FF; }

.ratio-wrap { display: flex; justify-content: center; align-items: center; height: 50px; margin-bottom: 8px; }
.ratio-box  { background: #D1D8E0; border-radius: 3px; transition: background 0.12s; }
.box-selected { background: #3182F6; }

.spec-name  { font-size: 11px; font-weight: 600; color: #4E5968; margin-bottom: 2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.spec-dim   { font-size: 10px; color: #8B95A1; }

.spec-check {
  position: absolute; top: 6px; right: 6px;
  width: 16px; height: 16px;
  background: #3182F6; color: #fff;
  border-radius: 50%; font-size: 10px; font-weight: 700;
  display: flex; align-items: center; justify-content: center;
}

/* Options bar */
.options-bar { display: flex; align-items: center; gap: 20px; flex-wrap: wrap; }
.opt-group   { display: flex; align-items: center; gap: 8px; }
.opt-label   { font-size: 13px; font-weight: 600; color: #6B7684; white-space: nowrap; }
.opt-chips   { display: flex; gap: 6px; }
.opt-chip {
  padding: 6px 14px; border-radius: 100px;
  border: 1.5px solid #E5E8EB; background: #fff;
  font-size: 13px; font-weight: 500; color: #4E5968;
  cursor: pointer; font-family: inherit; transition: all 0.12s;
}
.opt-chip:hover { border-color: #C4CAD4; color: #191F28; }
.opt-chip.on    { background: #191F28; border-color: #191F28; color: #fff; font-weight: 600; }

.gen-btn {
  margin-left: auto;
  padding: 12px 28px;
  border-radius: 12px; border: none;
  background: #3182F6; color: #fff;
  font-size: 14px; font-weight: 700; font-family: inherit;
  cursor: pointer; transition: background 0.12s; white-space: nowrap;
  letter-spacing: -0.2px;
}
.gen-btn:hover   { background: #1B6EF3; }
.gen-btn:disabled { background: #A0C4FB; cursor: not-allowed; }
.gen-btn.loading  { background: #A0C4FB; cursor: not-allowed; }

/* Result */
.result-card {
  background: #fff; border-radius: 16px; padding: 20px 24px;
  margin-top: 10px; border: 1.5px solid #0DC780;
  box-shadow: 0 1px 4px rgba(0,0,0,0.06);
  display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
}
.result-head { display: flex; align-items: center; gap: 8px; }
.result-check {
  width: 22px; height: 22px; background: #0DC780; color: #fff;
  border-radius: 50%; display: inline-flex; align-items: center; justify-content: center;
  font-size: 12px; font-weight: 700; flex-shrink: 0;
}
.result-title { font-size: 14px; font-weight: 600; color: #191F28; }
.result-meta  { font-size: 13px; color: #8B95A1; display: flex; gap: 8px; }
.result-media { color: #3182F6; }
.link-btn {
  margin-left: auto; background: none; border: none;
  color: #3182F6; font-size: 14px; font-weight: 600;
  cursor: pointer; padding: 0; font-family: inherit;
}
.link-btn:hover { text-decoration: underline; }
</style>
