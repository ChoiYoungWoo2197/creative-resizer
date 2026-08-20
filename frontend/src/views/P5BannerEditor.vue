<template>
  <div class="editor-wrap">
    <!-- 헤더 -->
    <div class="editor-bar">
      <button class="back-btn" @click="router.push(`/job/${jobId}`)">← 상세 보기</button>
      <span class="editor-title">{{ specFileName }}</span>
      <div class="editor-actions">
        <button class="btn-reset" @click="resetLayers" :disabled="saving">초기화</button>
        <button class="btn-save" @click="saveAndRecomposite" :disabled="saving || !isDirty">
          <span v-if="saving" class="spin" />
          {{ saving ? '재합성 중...' : '수정 완료 및 재합성' }}
        </button>
      </div>
    </div>

    <!-- 로딩 -->
    <div v-if="loading" class="center-state">
      <div class="spinner" />
      <p>레이아웃 데이터 로딩 중...</p>
    </div>

    <!-- 오류 -->
    <div v-else-if="error" class="error-box">
      <div class="error-title">로딩 실패</div>
      <div class="error-msg">{{ error }}</div>
    </div>

    <!-- 캔버스 에디터 -->
    <div v-else-if="layout" class="canvas-area">
      <!-- 좌측: Konva 캔버스 -->
      <div class="konva-wrap" ref="konvaWrapRef">
        <v-stage
          :config="stageConfig"
          @click="onStageClick"
        >
          <!-- 배경 이미지 레이어 (선택/드래그 불가) -->
          <v-layer>
            <v-image :config="bgConfig" />
          </v-layer>

          <!-- 요소 레이어 -->
          <v-layer ref="elemLayerRef">
            <template v-for="(lyr, idx) in editLayers" :key="lyr.name">
              <v-image
                :config="layerConfig(lyr, idx)"
                @click="selectLayer(idx)"
                @dragend="onDragEnd($event, idx)"
                @transformend="onTransformEnd($event, idx)"
              />
            </template>
            <v-transformer
              ref="transformerRef"
              :config="{ keepRatio: true, enabledAnchors: ['top-left','top-right','bottom-left','bottom-right'] }"
            />
          </v-layer>
        </v-stage>
      </div>

      <!-- 우측: 레이어 목록 + 수치 패널 -->
      <div class="side-panel">
        <div class="panel-title">레이어 목록</div>
        <div
          v-for="(lyr, idx) in editLayers"
          :key="lyr.name"
          class="layer-item"
          :class="{ active: selectedIdx === idx }"
          @click="selectLayer(idx)"
        >
          <span class="layer-dot" :class="'role-' + lyr.role" />
          <span class="layer-name">{{ lyr.name }}</span>
          <span class="layer-role">{{ lyr.role }}</span>
        </div>

        <template v-if="selectedIdx !== null">
          <div class="panel-title mt">수치 편집</div>
          <div class="num-row">
            <label>X</label>
            <input type="number" :value="editLayers[selectedIdx].render_x"
              @change="updateField(selectedIdx, 'render_x', +$event.target.value)" />
          </div>
          <div class="num-row">
            <label>Y</label>
            <input type="number" :value="editLayers[selectedIdx].render_y"
              @change="updateField(selectedIdx, 'render_y', +$event.target.value)" />
          </div>
          <div class="num-row">
            <label>W</label>
            <input type="number" :value="editLayers[selectedIdx].render_w"
              @change="updateField(selectedIdx, 'render_w', Math.max(1, +$event.target.value))" />
          </div>
          <div class="num-row">
            <label>H</label>
            <input type="number" :value="editLayers[selectedIdx].render_h"
              @change="updateField(selectedIdx, 'render_h', Math.max(1, +$event.target.value))" />
          </div>
        </template>
      </div>
    </div>

    <!-- 재합성 완료 토스트 -->
    <div v-if="savedMsg" class="toast">{{ savedMsg }}</div>
  </div>
</template>

<script setup>
import { ref, computed, watch, nextTick, onMounted, onBeforeUnmount } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { getLayoutResult, layerFileUrl, recomposite } from '../api/banner.js'

const route  = useRoute()
const router = useRouter()

const jobId       = route.params.id
const fileName    = route.query.fileName  // ?fileName=01_Naver_Mobile_DA_1250x560.png
const specFileName = ref(fileName || '')

// ── 상태 ──────────────────────────────────────────────────────────────────────
const loading    = ref(true)
const error      = ref('')
const layout     = ref(null)       // layout_result.json 내용
const editLayers = ref([])         // 편집 중인 레이어 배열 (deep copy)
const origLayers = ref([])         // 초기화용 원본 복사본
const selectedIdx = ref(null)
const saving     = ref(false)
const savedMsg   = ref('')

// konva refs
const transformerRef = ref(null)
const elemLayerRef   = ref(null)

// 스케일: 캔버스가 화면에 맞도록 축소
const DISPLAY_MAX_W = 900
const DISPLAY_MAX_H = 600
const displayScale = ref(1)

// ── 초기 데이터 로드 ──────────────────────────────────────────────────────────
onMounted(async () => {
  if (!fileName) {
    error.value = 'fileName 파라미터가 없습니다.'
    loading.value = false
    return
  }
  try {
    const res = await getLayoutResult(jobId, fileName)
    layout.value = res.data
    editLayers.value = JSON.parse(JSON.stringify(res.data.layers))
    origLayers.value = JSON.parse(JSON.stringify(res.data.layers))

    const tw = res.data.target_w
    const th = res.data.target_h
    displayScale.value = Math.min(1, DISPLAY_MAX_W / tw, DISPLAY_MAX_H / th)

    // 레이어 이미지 로드
    await loadLayerImages()
  } catch (e) {
    error.value = e.response?.status === 404
      ? 'layout_result.json 없음 — TYPE G 파이프라인 결과에서만 사용 가능합니다.'
      : (e.message || '알 수 없는 오류')
  } finally {
    loading.value = false
  }
})

// ── Konva 설정 ────────────────────────────────────────────────────────────────
const bgImage  = ref(null)
const imgCache = ref({})   // name → HTMLImageElement

const stageConfig = computed(() => ({
  width:  (layout.value?.target_w  ?? 800) * displayScale.value,
  height: (layout.value?.target_h  ?? 400) * displayScale.value,
}))

const bgConfig = computed(() => ({
  image:  bgImage.value,
  x: 0, y: 0,
  width:  (layout.value?.target_w ?? 800) * displayScale.value,
  height: (layout.value?.target_h ?? 400) * displayScale.value,
  listening: false,
}))

function layerConfig(lyr, idx) {
  const sc = displayScale.value
  return {
    image:      imgCache.value[lyr.name] ?? null,
    x:          lyr.render_x * sc,
    y:          lyr.render_y * sc,
    width:      lyr.render_w * sc,
    height:     lyr.render_h * sc,
    draggable:  true,
    name:       `layer-${idx}`,
  }
}

async function loadLayerImages() {
  // 배경 이미지
  if (layout.value?.bg_file) {
    bgImage.value = await loadImg(layerFileUrl(jobId, layout.value.bg_file))
  }
  // 레이어 이미지
  for (const lyr of editLayers.value) {
    if (lyr.layer_file) {
      imgCache.value[lyr.name] = await loadImg(layerFileUrl(jobId, lyr.layer_file))
    }
  }
}

function loadImg(src) {
  return new Promise((resolve) => {
    const img = new window.Image()
    img.onload  = () => resolve(img)
    img.onerror = () => resolve(null)
    img.src = src
  })
}

// ── 선택 / 트랜스포머 ─────────────────────────────────────────────────────────
function selectLayer(idx) {
  selectedIdx.value = idx
  nextTick(() => {
    const layer = elemLayerRef.value?.getNode()
    if (!layer) return
    const node = layer.findOne(`.layer-${idx}`)
    const tr   = transformerRef.value?.getNode()
    if (tr && node) tr.nodes([node])
    layer.batchDraw()
  })
}

function onStageClick(e) {
  // 빈 배경 클릭 시 선택 해제
  if (e.target === e.target.getStage()) {
    selectedIdx.value = null
    const tr = transformerRef.value?.getNode()
    if (tr) tr.nodes([])
  }
}

// ── 이벤트 핸들러 ─────────────────────────────────────────────────────────────
function onDragEnd(e, idx) {
  const sc = displayScale.value
  editLayers.value[idx].render_x = Math.round(e.target.x() / sc)
  editLayers.value[idx].render_y = Math.round(e.target.y() / sc)
}

function onTransformEnd(e, idx) {
  const node = e.target
  const sc   = displayScale.value
  editLayers.value[idx].render_x = Math.round(node.x()      / sc)
  editLayers.value[idx].render_y = Math.round(node.y()      / sc)
  editLayers.value[idx].render_w = Math.round(node.width()  * node.scaleX() / sc)
  editLayers.value[idx].render_h = Math.round(node.height() * node.scaleY() / sc)
  // 트랜스폼 스케일 리셋 (크기를 직접 반영했으므로)
  node.scaleX(1)
  node.scaleY(1)
}

function updateField(idx, field, value) {
  editLayers.value[idx][field] = value
  // 캔버스 노드 수동 갱신
  nextTick(() => {
    const layer = elemLayerRef.value?.getNode()
    if (layer) layer.batchDraw()
  })
}

// ── 더티 체크 ─────────────────────────────────────────────────────────────────
const isDirty = computed(() =>
  JSON.stringify(editLayers.value) !== JSON.stringify(origLayers.value)
)

function resetLayers() {
  editLayers.value = JSON.parse(JSON.stringify(origLayers.value))
  selectedIdx.value = null
  const tr = transformerRef.value?.getNode()
  if (tr) tr.nodes([])
}

// ── 재합성 요청 ───────────────────────────────────────────────────────────────
async function saveAndRecomposite() {
  if (!isDirty.value || saving.value) return
  saving.value = true
  try {
    const payload = editLayers.value.map(l => ({
      name:     l.name,
      render_x: l.render_x,
      render_y: l.render_y,
      render_w: l.render_w,
      render_h: l.render_h,
    }))
    await recomposite(jobId, fileName, payload)
    origLayers.value = JSON.parse(JSON.stringify(editLayers.value))
    savedMsg.value = '재합성 완료! 상세 페이지에서 결과를 확인하세요.'
    setTimeout(() => { savedMsg.value = '' }, 4000)
  } catch (e) {
    savedMsg.value = '재합성 실패: ' + (e.response?.data?.error || e.message)
    setTimeout(() => { savedMsg.value = '' }, 5000)
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.editor-wrap { display: flex; flex-direction: column; height: 100vh; background: #F3F4F6; }

.editor-bar {
  display: flex; align-items: center; gap: 12px;
  padding: 10px 20px; background: #fff;
  border-bottom: 1px solid #E5E7EB; flex-shrink: 0;
}
.back-btn { background: none; border: 1px solid #D1D5DB; border-radius: 6px; padding: 5px 12px; cursor: pointer; font-size: 13px; }
.editor-title { flex: 1; font-size: 13px; font-weight: 600; color: #374151; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.editor-actions { display: flex; gap: 8px; }
.btn-reset { background: #F9FAFB; border: 1px solid #D1D5DB; border-radius: 6px; padding: 6px 14px; cursor: pointer; font-size: 13px; }
.btn-save  { background: #7C3AED; color: #fff; border: none; border-radius: 6px; padding: 6px 14px; cursor: pointer; font-size: 13px; display: flex; align-items: center; gap: 6px; }
.btn-save:disabled { opacity: 0.5; cursor: default; }
.spin { width: 12px; height: 12px; border: 2px solid rgba(255,255,255,0.4); border-top-color: #fff; border-radius: 50%; animation: spin 0.7s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

.center-state { display: flex; flex-direction: column; align-items: center; justify-content: center; flex: 1; gap: 12px; color: #6B7280; }
.spinner { width: 32px; height: 32px; border: 3px solid #E5E7EB; border-top-color: #7C3AED; border-radius: 50%; animation: spin 0.8s linear infinite; }

.error-box { margin: 40px auto; max-width: 500px; background: #FEF2F2; border: 1px solid #FECACA; border-radius: 10px; padding: 24px; }
.error-title { font-weight: 700; color: #B91C1C; margin-bottom: 8px; }
.error-msg { font-size: 13px; color: #7F1D1D; }

.canvas-area { display: flex; flex: 1; overflow: hidden; }

.konva-wrap {
  flex: 1; display: flex; align-items: center; justify-content: center;
  padding: 24px; overflow: auto; background: #E5E7EB;
}
/* Konva stage 그림자 */
.konva-wrap :deep(canvas) { box-shadow: 0 4px 20px rgba(0,0,0,0.18); }

.side-panel {
  width: 240px; flex-shrink: 0; background: #fff;
  border-left: 1px solid #E5E7EB; overflow-y: auto;
  padding: 16px 12px;
}
.panel-title { font-size: 11px; font-weight: 700; color: #6B7280; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 8px; }
.panel-title.mt { margin-top: 20px; }

.layer-item {
  display: flex; align-items: center; gap: 8px;
  padding: 7px 8px; border-radius: 6px; cursor: pointer;
  font-size: 12px; color: #374151; margin-bottom: 2px;
}
.layer-item:hover { background: #F3F4F6; }
.layer-item.active { background: #EDE9FE; }
.layer-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; background: #9CA3AF; }
.layer-dot.role-title  { background: #3B82F6; }
.layer-dot.role-product{ background: #10B981; }
.layer-dot.role-logo   { background: #F59E0B; }
.layer-dot.role-person { background: #EC4899; }
.layer-dot.role-badge  { background: #8B5CF6; }
.layer-dot.role-body   { background: #6B7280; }
.layer-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.layer-role { font-size: 10px; color: #9CA3AF; flex-shrink: 0; }

.num-row { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.num-row label { width: 16px; font-size: 12px; font-weight: 600; color: #6B7280; flex-shrink: 0; }
.num-row input { flex: 1; border: 1px solid #D1D5DB; border-radius: 5px; padding: 4px 8px; font-size: 12px; }
.num-row input:focus { outline: none; border-color: #7C3AED; }

.toast {
  position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%);
  background: #1F2937; color: #fff; padding: 10px 20px;
  border-radius: 8px; font-size: 13px; z-index: 999;
  box-shadow: 0 4px 12px rgba(0,0,0,0.3);
}
</style>
