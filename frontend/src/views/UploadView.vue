<template>
  <div style="max-width:720px; margin:0 auto;">
    <el-card shadow="never">
      <template #header>
        <span style="font-size:16px; font-weight:600;">PSD 업로드 &amp; 배너 생성</span>
      </template>

      <el-form :model="form" :rules="rules" ref="formRef" label-width="110px" label-position="top">

        <!-- PSD 업로드 -->
        <el-form-item label="PSD 파일" prop="psdFile">
          <el-upload
            class="psd-uploader"
            drag
            :auto-upload="false"
            accept=".psd"
            :limit="1"
            :on-change="onFileChange"
            :on-remove="() => form.psdFile = null"
            :file-list="fileList"
          >
            <el-icon style="font-size:40px; color:#aaa;"><UploadFilled /></el-icon>
            <div style="margin-top:8px; color:#666;">PSD 파일을 드래그하거나 클릭하여 선택</div>
            <template #tip>
              <div style="color:#999; font-size:12px; margin-top:4px;">.psd 파일만 가능합니다</div>
            </template>
          </el-upload>
        </el-form-item>

        <!-- 광고주 / 캠페인 -->
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="광고주명" prop="advertiser">
              <el-input v-model="form.advertiser" placeholder="예: 삼성전자" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="캠페인명" prop="campaignName">
              <el-input v-model="form.campaignName" placeholder="예: 2024_summer" />
            </el-form-item>
          </el-col>
        </el-row>

        <!-- 매체 선택 -->
        <el-form-item label="생성할 매체" prop="targetMedia">
          <el-checkbox-group v-model="form.targetMedia">
            <el-checkbox v-for="m in mediaOptions" :key="m.value" :label="m.value">
              {{ m.label }}
            </el-checkbox>
          </el-checkbox-group>
        </el-form-item>

        <!-- 리사이즈 모드 -->
        <el-form-item label="리사이즈 방식">
          <el-radio-group v-model="form.resizeMode">
            <el-radio label="cover">꽉 채우기 (잘릴 수 있음)</el-radio>
            <el-radio label="contain">전체 보이기 (여백 생길 수 있음)</el-radio>
            <el-radio label="blur-bg">블러 배경 (원본 비율 유지)</el-radio>
          </el-radio-group>
        </el-form-item>

        <!-- 출력 포맷 -->
        <el-form-item label="출력 포맷">
          <el-radio-group v-model="form.outputFormat">
            <el-radio label="png">PNG</el-radio>
            <el-radio label="jpg">JPG</el-radio>
            <el-radio label="webp">WebP</el-radio>
          </el-radio-group>
        </el-form-item>

        <!-- 생성 버튼 -->
        <el-form-item>
          <el-button
            type="primary"
            size="large"
            style="width:100%;"
            :loading="loading"
            @click="submit"
          >
            배너 생성하기
          </el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <!-- 결과 -->
    <el-card v-if="result" shadow="never" style="margin-top:20px;">
      <template #header>
        <span style="font-size:15px; font-weight:600;">접수 완료</span>
      </template>
      <el-descriptions :column="2" border>
        <el-descriptions-item label="작업 ID">{{ result.id }}</el-descriptions-item>
        <el-descriptions-item label="상태">
          <el-tag type="warning">{{ result.status }}</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="광고주">{{ result.advertiser }}</el-descriptions-item>
        <el-descriptions-item label="캠페인">{{ result.campaignName }}</el-descriptions-item>
        <el-descriptions-item label="매체" :span="2">{{ result.targetMedia?.join(', ') }}</el-descriptions-item>
      </el-descriptions>
      <div style="margin-top:16px; text-align:right;">
        <el-button @click="$router.push('/jobs')">작업 목록에서 확인 →</el-button>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { ElMessage } from 'element-plus'
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

const rules = {
  psdFile:     [{ required: true, message: 'PSD 파일을 선택해주세요' }],
  advertiser:  [{ required: true, message: '광고주명을 입력해주세요' }],
  campaignName:[{ required: true, message: '캠페인명을 입력해주세요' }],
  targetMedia: [{ type: 'array', min: 1, message: '매체를 1개 이상 선택해주세요' }],
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
.psd-uploader :deep(.el-upload-dragger) { width: 100%; }
</style>
