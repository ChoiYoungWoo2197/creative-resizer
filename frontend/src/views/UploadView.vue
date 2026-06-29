<template>
  <div class="two-panel">

    <!-- ======== LEFT SIDEBAR ======== -->
    <aside class="sidebar">
      <div class="sidebar-scroll">

        <!-- Upload section -->
        <div class="sec">
          <div class="sec-head" @click="uploadOpen = !uploadOpen">
            <span class="sec-title">배너 업로드 <span class="sec-hint">(PSD)</span></span>
            <span class="chevron" :class="{ up: uploadOpen }">›</span>
          </div>
          <div v-show="uploadOpen" class="sec-body">
            <div v-if="!form.psdFile"
              class="drop-zone"
              :class="{ dragover }"
              @click="$refs.fileInput.click()"
              @dragover.prevent="dragover = true"
              @dragleave.prevent="dragover = false"
              @drop.prevent="onDrop"
            >
              <input ref="fileInput" type="file" accept=".psd" style="display:none" @change="onInputChange" />
              <div class="drop-icon">↑</div>
              <div class="drop-label">클릭 또는 드래그</div>
              <div class="drop-hint">.psd 파일만</div>
            </div>
            <div v-else class="file-row">
              <div class="file-badge">PSD</div>
              <div class="file-meta">
                <div class="file-name">{{ form.psdFile.name }}</div>
                <div class="file-size">{{ (form.psdFile.size / 1024 / 1024).toFixed(1) }} MB</div>
              </div>
              <button class="file-change" @click="form.psdFile = null">변경</button>
            </div>

            <div class="field-stack">
              <input v-model="form.advertiser" class="side-input" placeholder="광고주명" />
              <input v-model="form.campaignName" class="side-input" placeholder="캠페인명" />
            </div>
          </div>
        </div>

        <!-- Media guide section -->
        <div class="sec">
          <div class="sec-head" @click="mediaOpen = !mediaOpen">
            <span class="sec-title">매체 가이드</span>
            <span class="chevron" :class="{ up: mediaOpen }">›</span>
          </div>
          <div v-show="mediaOpen">
            <div v-if="specsLoading" class="side-loading">불러오는 중...</div>
            <div v-else-if="Object.keys(groupedSpecs).length === 0" class="side-loading">규격 없음</div>
            <template v-else>
              <div v-for="platform in platformOrder.filter(p => groupedSpecs[p])" :key="platform">
                <!-- Platform row -->
                <div class="pf-row" @click="toggleExpand(platform)">
                  <input type="checkbox"
                    class="check"
                    :checked="isPlatformAllSelected(platform)"
                    :indeterminate.prop="isPlatformPartial(platform)"
                    @change.stop="togglePlatform(platform)"
                    @click.stop
                  />
                  <span class="pf-dot" :style="{ background: platformCfg[platform]?.color }" />
                  <span class="pf-name">{{ platformCfg[platform]?.label ?? platform }}</span>
                  <span class="pf-region">{{ platformCfg[platform]?.region }}</span>
                  <span class="pf-cnt">({{ platformSelectedCount(platform) }}/{{ groupedSpecs[platform].length }})</span>
                  <span class="pf-chevron" :class="{ open: expandedPlatforms[platform] }">›</span>
                </div>
                <!-- Spec items -->
                <div v-show="expandedPlatforms[platform]" class="spec-items">
                  <div
                    v-for="spec in groupedSpecs[platform]" :key="spec.id"
                    class="spec-item" :class="{ on: selectedSpecIds.includes(spec.id) }"
                    @click="toggleSpec(spec.id)"
                  >
                    <input type="checkbox" class="check sm" :checked="selectedSpecIds.includes(spec.id)" @click.stop @change="toggleSpec(spec.id)" />
                    <span class="sp-name">{{ spec.placementName }}</span>
                    <span class="sp-dim">{{ spec.width }}×{{ spec.height }}</span>
                  </div>
                </div>
              </div>
            </template>
          </div>
        </div>

        <!-- Advanced options -->
        <div class="sec">
          <div class="sec-head" @click="advOpen = !advOpen">
            <span class="sec-title">고급 옵션</span>
            <span class="chevron" :class="{ up: advOpen }">›</span>
          </div>
          <div v-show="advOpen" class="sec-body adv-body">
            <div class="adv-row">
              <span class="adv-label">리사이즈</span>
              <div class="adv-chips">
                <button v-for="r in resizeOptions" :key="r.value" type="button"
                  class="adv-chip" :class="{ on: form.resizeMode === r.value }"
                  @click="form.resizeMode = r.value">{{ r.label }}</button>
              </div>
            </div>
            <div class="adv-row">
              <span class="adv-label">포맷</span>
              <div class="adv-chips">
                <button v-for="f in formatOptions" :key="f.value" type="button"
                  class="adv-chip" :class="{ on: form.outputFormat === f.value }"
                  @click="form.outputFormat = f.value">{{ f.label }}</button>
              </div>
            </div>
          </div>
        </div>

      </div><!-- /sidebar-scroll -->

      <!-- Sidebar footer (fixed) -->
      <div class="sidebar-foot">
        <div class="foot-info">
          선택된 사이즈 <b>{{ selectedSpecIds.length }}</b>개
        </div>
        <button
          class="gen-btn"
          :disabled="loading || selectedSpecIds.length === 0 || !form.psdFile || !form.advertiser || !form.campaignName"
          @click="submit"
        >
          <span v-if="loading" class="spinner" />
          {{ loading ? '생성 중...' : '배너 생성' }}
        </button>
      </div>
    </aside>

    <!-- ======== RIGHT PANEL ======== -->
    <main class="right-panel">
      <!-- Empty -->
      <div v-if="selectedSpecIds.length === 0" class="empty">
        <div class="empty-icon">☰</div>
        <div class="empty-title">사이즈를 선택하세요</div>
        <div class="empty-desc">왼쪽 매체 가이드에서 원하는 사이즈를 선택하면 여기에 표시됩니다.</div>
      </div>

      <template v-else>
        <div class="right-top">
          <span class="right-cnt">선택된 사이즈 {{ selectedSpecIds.length }}개</span>
          <button class="del-all" @click="selectedSpecIds = []">🗑 일괄삭제</button>
        </div>

        <div v-for="platform in activePlatforms" :key="platform" class="right-group">
          <div class="group-head">
            <span class="group-dot" :style="{ background: platformCfg[platform]?.color }" />
            <span class="group-name">{{ platformCfg[platform]?.label ?? platform }}</span>
            <span class="group-cnt">({{ selectedByPlatform(platform).length }}개)</span>
          </div>
          <div class="cards-grid">
            <div v-for="spec in selectedByPlatform(platform)" :key="spec.id" class="spec-card">
              <button class="card-x" @click="removeSpec(spec.id)" title="제거">×</button>
              <div class="card-name">{{ platformCfg[platform]?.label }}_{{ spec.placementName }} - {{ spec.width }}×{{ spec.height }}</div>
              <div class="card-dim">{{ spec.width }}×{{ spec.height }}px</div>
              <span class="card-tag" :style="tagStyle(platform)">{{ platformCfg[platform]?.label }}</span>
            </div>
          </div>
        </div>
      </template>

      <!-- Toast result -->
      <transition name="toast">
        <div v-if="result" class="toast">
          <span class="toast-check">✓</span>
          <span>작업 접수 완료</span>
          <button class="toast-link" @click="$router.push('/jobs')">작업 목록 →</button>
        </div>
      </transition>
    </main>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { uploadPsd, listSpecs } from '../api/banner.js'

const loading      = ref(false)
const result       = ref(null)
const allSpecs     = ref([])
const specsLoading = ref(true)
const selectedSpecIds = ref([])
const dragover     = ref(false)

const uploadOpen = ref(true)
const mediaOpen  = ref(true)
const advOpen    = ref(false)
const expandedPlatforms = reactive({})

const form = reactive({
  psdFile:      null,
  advertiser:   '',
  campaignName: '',
  resizeMode:   'cover',
  outputFormat: 'png',
})

const platformOrder = ['google', 'meta', 'naver', 'kakao', 'linkedin', 'tiktok', 'line']

const platformCfg = {
  google:   { label: 'Google',   region: 'global', color: '#4285F4', tagBg: '#EAF1FE' },
  meta:     { label: 'Meta',     region: 'global', color: '#1877F2', tagBg: '#E8F0FD' },
  naver:    { label: 'Naver',    region: 'korea',  color: '#03C75A', tagBg: '#E6F9EE' },
  kakao:    { label: 'Kakao',    region: 'korea',  color: '#FACC15', tagBg: '#FEF9E7' },
  linkedin: { label: 'LinkedIn', region: 'global', color: '#0A66C2', tagBg: '#E8F0FA' },
  tiktok:   { label: 'TikTok',  region: 'global', color: '#191F28', tagBg: '#F2F4F6' },
  line:     { label: 'LINE',     region: 'japan',  color: '#06C755', tagBg: '#E6F9EE' },
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

const activePlatforms = computed(() =>
  platformOrder.filter(p => selectedByPlatform(p).length > 0)
)

function selectedByPlatform(platform) {
  const ids = new Set(selectedSpecIds.value)
  return (groupedSpecs.value[platform] ?? []).filter(s => ids.has(s.id))
}

function platformSelectedCount(platform) {
  return selectedByPlatform(platform).length
}

function isPlatformAllSelected(platform) {
  const specs = groupedSpecs.value[platform] ?? []
  return specs.length > 0 && specs.every(s => selectedSpecIds.value.includes(s.id))
}

function isPlatformPartial(platform) {
  const specs = groupedSpecs.value[platform] ?? []
  const n = specs.filter(s => selectedSpecIds.value.includes(s.id)).length
  return n > 0 && n < specs.length
}

function togglePlatform(platform) {
  const ids = (groupedSpecs.value[platform] ?? []).map(s => s.id)
  if (isPlatformAllSelected(platform)) {
    selectedSpecIds.value = selectedSpecIds.value.filter(id => !ids.includes(id))
  } else {
    ids.forEach(id => { if (!selectedSpecIds.value.includes(id)) selectedSpecIds.value.push(id) })
  }
}

function toggleExpand(platform) {
  expandedPlatforms[platform] = !expandedPlatforms[platform]
}

function toggleSpec(id) {
  const idx = selectedSpecIds.value.indexOf(id)
  if (idx >= 0) selectedSpecIds.value.splice(idx, 1)
  else selectedSpecIds.value.push(id)
}

function removeSpec(id) {
  selectedSpecIds.value = selectedSpecIds.value.filter(x => x !== id)
}

function tagStyle(platform) {
  const cfg = platformCfg[platform] ?? {}
  return { background: cfg.tagBg ?? '#F2F4F6', color: cfg.color ?? '#6B7684' }
}

function onInputChange(e) {
  const file = e.target.files?.[0]
  if (file) form.psdFile = file
}

function onDrop(e) {
  dragover.value = false
  const file = e.dataTransfer.files?.[0]
  if (file && file.name.endsWith('.psd')) form.psdFile = file
  else ElMessage.warning('PSD 파일만 업로드 가능합니다.')
}

async function submit() {
  if (!form.psdFile)               return ElMessage.warning('PSD 파일을 선택해주세요.')
  if (!form.advertiser)            return ElMessage.warning('광고주명을 입력해주세요.')
  if (!form.campaignName)          return ElMessage.warning('캠페인명을 입력해주세요.')
  if (!selectedSpecIds.value.length) return ElMessage.warning('사이즈를 1개 이상 선택해주세요.')

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
    setTimeout(() => { result.value = null }, 5000)
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
    for (const spec of data) {
      if (!(spec.media in expandedPlatforms)) expandedPlatforms[spec.media] = false
    }
  } catch {
    ElMessage.error('규격 로딩 실패')
  } finally {
    specsLoading.value = false
  }
})
</script>

<style scoped>
/* ===== layout ===== */
.two-panel {
  display: flex;
  height: calc(100vh - 56px);
  overflow: hidden;
}

/* ===== sidebar ===== */
.sidebar {
  width: 310px;
  flex-shrink: 0;
  background: #fff;
  border-right: 1px solid #E5E8EB;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.sidebar-scroll {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
}
.sidebar-scroll::-webkit-scrollbar { width: 4px; }
.sidebar-scroll::-webkit-scrollbar-thumb { background: #E5E8EB; border-radius: 2px; }

/* section */
.sec { border-bottom: 1px solid #F2F4F6; }
.sec-head {
  display: flex; justify-content: space-between; align-items: center;
  padding: 13px 16px; cursor: pointer; user-select: none;
}
.sec-head:hover { background: #FAFBFC; }
.sec-title { font-size: 13px; font-weight: 600; color: #333D4B; }
.sec-hint  { font-weight: 400; color: #8B95A1; font-size: 12px; }
.chevron {
  font-size: 16px; color: #B0B8C1;
  transform: rotate(90deg); display: inline-block; transition: transform 0.2s;
}
.chevron.up { transform: rotate(-90deg); }

.sec-body { padding: 0 14px 14px; }

/* drop zone */
.drop-zone {
  border: 1.5px dashed #D1D8E0; border-radius: 10px;
  background: #FAFBFC; padding: 24px 16px; text-align: center;
  cursor: pointer; transition: all 0.12s; margin-bottom: 12px;
}
.drop-zone:hover, .drop-zone.dragover { border-color: #3182F6; background: #EEF4FF; }
.drop-icon  { font-size: 22px; color: #B0B8C1; margin-bottom: 6px; }
.drop-label { font-size: 13px; font-weight: 600; color: #4E5968; }
.drop-hint  { font-size: 11px; color: #B0B8C1; margin-top: 2px; }

/* file row */
.file-row { display: flex; align-items: center; gap: 10px; padding: 10px 0 12px; }
.file-badge { background: #4285F4; color: #fff; font-size: 10px; font-weight: 700; padding: 3px 6px; border-radius: 4px; flex-shrink: 0; }
.file-meta  { flex: 1; min-width: 0; }
.file-name  { font-size: 12px; font-weight: 600; color: #333D4B; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.file-size  { font-size: 11px; color: #8B95A1; margin-top: 1px; }
.file-change {
  background: none; border: 1px solid #E5E8EB; border-radius: 6px;
  font-size: 11px; color: #6B7684; padding: 3px 8px;
  cursor: pointer; font-family: inherit; flex-shrink: 0;
}
.file-change:hover { border-color: #3182F6; color: #3182F6; }

/* inputs */
.field-stack { display: flex; flex-direction: column; gap: 8px; }
.side-input {
  width: 100%; padding: 9px 12px;
  border: 1.5px solid #E5E8EB; border-radius: 8px;
  font-size: 13px; font-family: inherit; color: #191F28;
  outline: none; transition: border-color 0.12s; background: #fff;
}
.side-input:focus { border-color: #3182F6; }
.side-input::placeholder { color: #B0B8C1; }

/* platform list */
.side-loading { padding: 14px 16px; font-size: 13px; color: #B0B8C1; }

.pf-row {
  display: flex; align-items: center; gap: 7px;
  padding: 9px 16px; cursor: pointer; transition: background 0.1s;
}
.pf-row:hover { background: #FAFBFC; }
.pf-dot    { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }
.pf-name   { font-size: 13px; font-weight: 600; color: #333D4B; }
.pf-region { font-size: 11px; color: #B0B8C1; margin-left: 2px; }
.pf-cnt    { font-size: 11px; color: #8B95A1; margin-left: auto; }
.pf-chevron {
  font-size: 14px; color: #B0B8C1;
  transform: rotate(90deg); transition: transform 0.2s; margin-left: 4px;
}
.pf-chevron.open { transform: rotate(-90deg); }

/* spec items */
.spec-items { background: #FAFBFC; padding: 2px 0 4px 28px; }
.spec-item {
  display: flex; align-items: center; gap: 7px;
  padding: 7px 14px 7px 0; cursor: pointer;
  border-radius: 6px; transition: background 0.1s;
}
.spec-item:hover { background: #F0F4FF; }
.spec-item.on    { background: #EEF4FF; }
.sp-name { font-size: 12px; color: #4E5968; flex: 1; min-width: 0; }
.sp-dim  { font-size: 11px; color: #B0B8C1; flex-shrink: 0; }

/* checkbox */
.check { accent-color: #3182F6; width: 14px; height: 14px; cursor: pointer; flex-shrink: 0; }
.check.sm { width: 13px; height: 13px; }

/* advanced */
.adv-body  { display: flex; flex-direction: column; gap: 12px; }
.adv-row   { display: flex; align-items: center; gap: 10px; }
.adv-label { font-size: 12px; color: #6B7684; font-weight: 500; width: 46px; flex-shrink: 0; }
.adv-chips { display: flex; gap: 5px; flex-wrap: wrap; }
.adv-chip  {
  padding: 4px 10px; border-radius: 100px;
  border: 1px solid #E5E8EB; background: #fff;
  font-size: 11px; color: #6B7684; cursor: pointer; font-family: inherit; transition: all 0.1s;
}
.adv-chip:hover { border-color: #3182F6; color: #3182F6; }
.adv-chip.on    { background: #333D4B; border-color: #333D4B; color: #fff; font-weight: 600; }

/* sidebar footer */
.sidebar-foot {
  padding: 14px 16px; border-top: 1px solid #E5E8EB;
  background: #fff; flex-shrink: 0;
}
.foot-info { font-size: 12px; color: #6B7684; margin-bottom: 8px; }
.foot-info b { color: #3182F6; }
.gen-btn {
  width: 100%; padding: 12px;
  background: #3182F6; color: #fff;
  border: none; border-radius: 10px;
  font-size: 14px; font-weight: 700; font-family: inherit;
  cursor: pointer; transition: background 0.12s;
  display: flex; align-items: center; justify-content: center; gap: 8px;
}
.gen-btn:hover:not(:disabled)  { background: #1B6EF3; }
.gen-btn:disabled { background: #A0C4FB; cursor: not-allowed; }
.spinner {
  width: 14px; height: 14px;
  border: 2px solid rgba(255,255,255,0.3);
  border-top-color: #fff; border-radius: 50%;
  animation: spin 0.7s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }

/* ===== right panel ===== */
.right-panel {
  flex: 1; overflow-y: auto;
  padding: 28px 28px 60px;
  position: relative;
}

/* empty */
.empty {
  display: flex; flex-direction: column; align-items: center;
  justify-content: center; height: 70%;
}
.empty-icon  { font-size: 40px; color: #D1D8E0; margin-bottom: 16px; }
.empty-title { font-size: 16px; font-weight: 700; color: #6B7684; margin-bottom: 8px; }
.empty-desc  { font-size: 13px; color: #B0B8C1; text-align: center; line-height: 1.6; }

/* right header */
.right-top {
  display: flex; justify-content: space-between; align-items: center;
  margin-bottom: 24px;
}
.right-cnt { font-size: 15px; font-weight: 700; color: #191F28; }
.del-all   {
  background: none; border: none; color: #FF3B30;
  font-size: 13px; font-weight: 500; cursor: pointer; font-family: inherit;
  display: flex; align-items: center; gap: 4px;
}
.del-all:hover { opacity: 0.7; }

/* group */
.right-group   { margin-bottom: 28px; }
.group-head    { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; }
.group-dot     { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.group-name    { font-size: 14px; font-weight: 700; color: #191F28; }
.group-cnt     { font-size: 12px; color: #8B95A1; }

/* cards grid */
.cards-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 10px;
}

.spec-card {
  position: relative; background: #fff;
  border: 1px solid #E5E8EB; border-radius: 12px;
  padding: 16px 14px 14px; transition: box-shadow 0.12s;
}
.spec-card:hover { box-shadow: 0 2px 8px rgba(0,0,0,0.08); }

.card-x {
  position: absolute; top: 10px; right: 10px;
  width: 20px; height: 20px;
  background: #F2F4F6; border: none; border-radius: 50%;
  font-size: 14px; color: #8B95A1; cursor: pointer;
  display: flex; align-items: center; justify-content: center; padding: 0;
}
.card-x:hover { background: #FFE5E5; color: #FF3B30; }

.card-name { font-size: 12px; font-weight: 600; color: #333D4B; margin-bottom: 6px; padding-right: 20px; line-height: 1.4; }
.card-dim  { font-size: 11px; color: #8B95A1; margin-bottom: 10px; }
.card-tag  { display: inline-block; font-size: 11px; font-weight: 600; padding: 3px 8px; border-radius: 5px; }

/* toast */
.toast {
  position: fixed; bottom: 28px; right: 28px;
  background: #191F28; color: #fff;
  padding: 12px 18px; border-radius: 12px;
  display: flex; align-items: center; gap: 10px;
  font-size: 13px; font-weight: 500;
  box-shadow: 0 4px 20px rgba(0,0,0,0.2);
  z-index: 100;
}
.toast-check { color: #0DC780; font-weight: 700; }
.toast-link  {
  color: #A0C4FB; background: none; border: none;
  cursor: pointer; font-family: inherit; font-size: 13px; font-weight: 600; padding: 0;
}
.toast-link:hover { text-decoration: underline; }

.toast-enter-active, .toast-leave-active { transition: all 0.3s; }
.toast-enter-from { opacity: 0; transform: translateY(16px); }
.toast-leave-to   { opacity: 0; transform: translateY(16px); }
</style>
