<template>
  <div>
    <div class="page-header">
      <h1 class="page-title">배너 생성</h1>
      <p class="page-desc">PSD 파일을 업로드하면 매체별 규격에 맞는 배너를 자동으로 생성합니다.</p>
    </div>

    <el-form :model="form" :rules="rules" ref="formRef">

      <!-- PSD 업로드 -->
      <div class="card">
        <div class="card-title">PSD 파일</div>
        <el-form-item prop="psdFile" style="margin:0;">
          <el-upload
            class="upload-zone"
            drag
            :auto-upload="false"
            accept=".psd"
            :limit="1"
            :on-change="onFileChange"
            :on-remove="() => form.psdFile = null"
            :file-list="fileList"
          >
            <el-icon class="upload-icon"><UploadFilled /></el-icon>
            <div class="upload-text">PSD 파일을 드래그하거나 클릭하여 선택</div>
            <div class="upload-hint">.psd 파일만 가능합니다</div>
          </el-upload>
        </el-form-item>
      </div>

      <!-- 캠페인 정보 -->
      <div class="card">
        <div class="card-title">캠페인 정보</div>
        <div class="input-row">
          <el-form-item prop="advertiser" style="flex:1; margin:0;">
            <div class="field-label">광고주명</div>
            <el-input v-model="form.advertiser" placeholder="예: 삼성전자" />
          </el-form-item>
          <el-form-item prop="campaignName" style="flex:1; margin:0;">
            <div class="field-label">캠페인명</div>
            <el-input v-model="form.campaignName" placeholder="예: 2024_summer" />
          </el-form-item>
        </div>
      </div>

      <!-- 생성 옵션 -->
      <div class="card">
        <div class="card-title">생성 옵션</div>

        <!-- 매체 -->
        <div class="option-block">
          <div class="option-label">매체 선택</div>
          <el-form-item prop="targetMedia" style="margin:0;">
            <div class="chip-row">
              <button
                v-for="m in mediaOptions" :key="m.value"
                type="button"
                class="chip"
                :class="{ on: form.targetMedia.includes(m.value) }"
                @click="toggleMedia(m.value)"
              >{{ m.label }}</button>
            </div>
          </el-form-item>
        </div>

        <!-- 리사이즈 방식 -->
        <div class="option-block">
          <div class="option-label">리사이즈 방식</div>
          <div class="mode-row">
            <div
              v-for="r in resizeOptions" :key="r.value"
              class="mode-card"
              :class="{ on: form.resizeMode === r.value }"
              @click="form.resizeMode = r.value"
            >
              <div class="mode-name">{{ r.label }}</div>
              <div class="mode-desc">{{ r.desc }}</div>
            </div>
          </div>
        </div>

        <!-- 출력 포맷 -->
        <div class="option-block" style="margin-bottom:0;">
          <div class="option-label">출력 포맷</div>
          <div class="chip-row">
            <button
              v-for="f in formatOptions" :key="f.value"
              type="button"
              class="chip"
              :class="{ on: form.outputFormat === f.value }"
              @click="form.outputFormat = f.value"
            >{{ f.label }}</button>
          </div>
        </div>
      </div>

      <!-- 생성 버튼 -->
      <button class="submit-btn" :class="{ loading }" :disabled="loading" @click="submit">
        {{ loading ? '처리 중...' : '배너 생성하기' }}
      </button>

    </el-form>

    <!-- 접수 완료 -->
    <div v-if="result" class="result-card">
      <div class="result-head">
        <span class="result-check">✓</span>
        <span class="result-title">접수 완료</span>
      </div>
      <div class="result-rows">
        <div class="result-row"><span class="rk">작업 ID</span><span class="rv mono">{{ result.id }}</span></div>
        <div class="result-row"><span class="rk">광고주</span><span class="rv">{{ result.advertiser }}</span></div>
        <div class="result-row"><span class="rk">캠페인</span><span class="rv">{{ result.campaignName }}</span></div>
        <div class="result-row"><span class="rk">매체</span><span class="rv">{{ result.targetMedia?.join(', ') }}</span></div>
      </div>
      <button class="link-btn" @click="$router.push('/jobs')">작업 목록에서 확인 →</button>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { ElMessage } from 'element-plus'
import { UploadFilled } from '@element-plus/icons-vue'
import { uploadPsd } from '../api/banner.js'

const formRef = ref()
const loading = ref(false)
const result  = ref(null)
const fileList = ref([])

const form = reactive({
  psdFile:     null,
  advertiser:  '',
  campaignName:'',
  targetMedia: ['google', 'meta'],
  resizeMode:  'cover',
  outputFormat:'png',
})

const mediaOptions = [
  { label: 'Google',   value: 'google' },
  { label: 'Meta',     value: 'meta' },
  { label: 'Naver',    value: 'naver' },
  { label: 'Kakao',    value: 'kakao' },
  { label: 'LinkedIn', value: 'linkedin' },
  { label: 'TikTok',   value: 'tiktok' },
]

const resizeOptions = [
  { value: 'cover',   label: '꽉 채우기',   desc: '잘릴 수 있음' },
  { value: 'contain', label: '전체 보이기', desc: '여백 생길 수 있음' },
  { value: 'blur-bg', label: '블러 배경',   desc: '원본 비율 유지' },
]

const formatOptions = [
  { label: 'PNG',  value: 'png' },
  { label: 'JPG',  value: 'jpg' },
  { label: 'WebP', value: 'webp' },
]

const rules = {
  psdFile:     [{ required: true, message: 'PSD 파일을 선택해주세요' }],
  advertiser:  [{ required: true, message: '광고주명을 입력해주세요' }],
  campaignName:[{ required: true, message: '캠페인명을 입력해주세요' }],
  targetMedia: [{ type: 'array', min: 1, message: '매체를 1개 이상 선택해주세요' }],
}

function toggleMedia(val) {
  const idx = form.targetMedia.indexOf(val)
  if (idx >= 0) {
    if (form.targetMedia.length > 1) form.targetMedia.splice(idx, 1)
  } else {
    form.targetMedia.push(val)
  }
}

function onFileChange(file) {
  form.psdFile = file.raw
  fileList.value = [file]
}

async function submit() {
  await formRef.value.validate()
  const fd = new FormData()
  fd.append('psdFile', form.psdFile)
  fd.append('advertiser', form.advertiser)
  fd.append('campaignName', form.campaignName)
  form.targetMedia.forEach(m => fd.append('targetMedia', m))
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
</script>

<style scoped>
.page-header { margin-bottom: 24px; }
.page-title { font-size: 22px; font-weight: 700; letter-spacing: -0.5px; }
.page-desc { margin-top: 6px; font-size: 14px; color: #8B95A1; }

/* Cards */
.card {
  background: #fff;
  border-radius: 16px;
  padding: 24px;
  margin-bottom: 10px;
  box-shadow: 0 1px 4px rgba(0,0,0,0.06);
}
.card-title {
  font-size: 15px;
  font-weight: 700;
  color: #191F28;
  margin-bottom: 16px;
}

/* Upload */
.upload-zone :deep(.el-upload) { width: 100%; }
.upload-zone :deep(.el-upload-dragger) {
  width: 100%; height: 136px;
  border: 1.5px dashed #D1D8E0;
  border-radius: 12px;
  background: #FAFBFC;
  display: flex; flex-direction: column;
  align-items: center; justify-content: center;
  gap: 6px;
  transition: all 0.15s;
}
.upload-zone :deep(.el-upload-dragger:hover) {
  border-color: #3182F6;
  background: #EEF4FF;
}
.upload-icon { font-size: 32px; color: #B0B8C1; }
.upload-text { font-size: 14px; font-weight: 500; color: #4E5968; }
.upload-hint { font-size: 12px; color: #B0B8C1; }

/* Inputs */
.input-row { display: flex; gap: 12px; }
.field-label { font-size: 13px; font-weight: 500; color: #6B7684; margin-bottom: 6px; }

:deep(.el-input__wrapper) {
  border-radius: 10px !important;
  border: 1.5px solid #E5E8EB !important;
  box-shadow: none !important;
  padding: 10px 14px !important;
}
:deep(.el-input__wrapper:hover) { border-color: #C4CAD4 !important; }
:deep(.el-input__wrapper.is-focus) { border-color: #3182F6 !important; }
:deep(.el-input__inner) { font-size: 14px; color: #191F28; font-family: inherit; }
:deep(.el-form-item__label) { display: none; }
:deep(.el-form-item__error) { font-size: 12px; color: #FF3B30; }

/* Options */
.option-block { margin-bottom: 24px; }
.option-label { font-size: 13px; font-weight: 600; color: #4E5968; margin-bottom: 10px; }

/* Chips */
.chip-row { display: flex; flex-wrap: wrap; gap: 8px; }
.chip {
  padding: 7px 16px;
  border-radius: 100px;
  border: 1.5px solid #E5E8EB;
  background: #fff;
  font-size: 13px;
  font-weight: 500;
  color: #4E5968;
  cursor: pointer;
  transition: all 0.12s;
  font-family: inherit;
}
.chip:hover { border-color: #C4CAD4; color: #191F28; }
.chip.on { background: #3182F6; border-color: #3182F6; color: #fff; font-weight: 600; }

/* Resize mode cards */
.mode-row { display: flex; gap: 10px; }
.mode-card {
  flex: 1;
  padding: 16px 12px;
  border-radius: 12px;
  border: 1.5px solid #E5E8EB;
  background: #fff;
  cursor: pointer;
  transition: all 0.12s;
  text-align: center;
}
.mode-card:hover { border-color: #C4CAD4; background: #FAFBFC; }
.mode-card.on { border-color: #3182F6; background: #EEF4FF; }
.mode-name { font-size: 13px; font-weight: 600; color: #191F28; margin-bottom: 4px; }
.mode-desc { font-size: 11px; color: #8B95A1; }

/* Submit */
.submit-btn {
  width: 100%;
  height: 52px;
  background: #3182F6;
  color: #fff;
  border: none;
  border-radius: 12px;
  font-size: 15px;
  font-weight: 700;
  cursor: pointer;
  transition: background 0.12s;
  margin-top: 6px;
  font-family: inherit;
  letter-spacing: -0.2px;
}
.submit-btn:hover { background: #1B6EF3; }
.submit-btn:active { background: #1459D4; }
.submit-btn.loading,
.submit-btn:disabled { background: #A0C4FB; cursor: not-allowed; }

/* Result */
.result-card {
  background: #fff;
  border-radius: 16px;
  padding: 24px;
  margin-top: 10px;
  border: 1.5px solid #0DC780;
  box-shadow: 0 1px 4px rgba(0,0,0,0.06);
}
.result-head { display: flex; align-items: center; gap: 8px; margin-bottom: 16px; }
.result-check {
  width: 22px; height: 22px;
  background: #0DC780; color: #fff;
  border-radius: 50%;
  display: inline-flex; align-items: center; justify-content: center;
  font-size: 12px; font-weight: 700;
}
.result-title { font-size: 15px; font-weight: 700; }
.result-rows { display: flex; flex-direction: column; gap: 10px; margin-bottom: 16px; }
.result-row { display: flex; justify-content: space-between; font-size: 14px; }
.rk { color: #8B95A1; }
.rv { color: #191F28; font-weight: 500; }
.mono { font-family: monospace; font-size: 12px; }
.link-btn {
  background: none; border: none; color: #3182F6;
  font-size: 14px; font-weight: 600; cursor: pointer;
  padding: 0; font-family: inherit;
}
.link-btn:hover { text-decoration: underline; }
</style>
