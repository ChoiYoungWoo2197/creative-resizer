<template>
  <div class="editor-wrap">
    <!-- 헤더 -->
    <div class="editor-bar">
      <button class="back-btn" @click="router.push(`/job/${jobId}`)">← 상세 보기</button>
      <span class="editor-title">{{ specFileName }}</span>
      <div class="editor-actions">
        <button class="btn-undo" @click="undo" :disabled="!canUndo" title="실행 취소 (Ctrl+Z)">↩</button>
        <button class="btn-undo" @click="redo" :disabled="!canRedo" title="다시 실행 (Ctrl+Y)">↪</button>
        <span class="zoom-display">{{ Math.round(stageScale * 100) }}%</span>
        <button class="btn-undo" @click="resetZoom" :disabled="stageScale === 1 && stagePosX === 0 && stagePosY === 0" title="줌 리셋 (Ctrl+휠)">⊙</button>
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
      <div class="konva-wrap" ref="konvaWrapRef" :style="{ cursor: spacebarDown ? 'grab' : '' }">
        <v-stage
          :config="stageConfig"
          @click="onStageClick"
          @wheel="onStageWheel"
          @dragmove="onStagePan"
          @dragend="onStagePan"
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
                @dragmove="onDragMove($event, idx)"
                @dragend="onDragEnd($event, idx)"
                @transformend="onTransformEnd($event, idx)"
              />
            </template>
            <v-transformer
              ref="transformerRef"
              :config="{ keepRatio: true, enabledAnchors: ['top-left','top-right','bottom-left','bottom-right'] }"
            />
          </v-layer>

          <!-- 가이드선 레이어: 요소 위에 렌더링, 이벤트 비활성 -->
          <v-layer :config="{ listening: false }">
            <v-line
              v-for="(gl, i) in guideLines"
              :key="i"
              :config="gl"
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
          <div class="layer-order-btns" @click.stop>
            <button class="layer-order-btn" @click="moveLayerUp(idx)" :disabled="idx === 0">▲</button>
            <button class="layer-order-btn" @click="moveLayerDown(idx)" :disabled="idx === editLayers.length - 1">▼</button>
          </div>
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

// ── Zoom/Pan 상태 ─────────────────────────────────────────────────────────────
const ZOOM_MIN    = 0.5   // 최소 배율
const ZOOM_MAX    = 3.0   // 최대 배율
const ZOOM_FACTOR = 1.1   // 휠 1회당 배율 변화

const stageScale   = ref(1)     // 줌 배율 (1 = fit view)
const stagePosX    = ref(0)     // 팬 오프셋 X
const stagePosY    = ref(0)     // 팬 오프셋 Y
const spacebarDown = ref(false) // 스페이스바 팬 모드 활성 여부

// ── 스냅/Nudge 상수 ───────────────────────────────────────────────────────────
const SNAP_THRESHOLD    = 5   // 스냅 인식 거리 (화면 px)
const NUDGE_STEP        = 1   // 방향키 이동 단위 (원본 좌표 px)
const NUDGE_STEP_SHIFT  = 10  // Shift+방향키 이동 단위

const guideLines = ref([])    // 드래그 중 표시할 가이드선 배열

// ── Undo/Redo 히스토리 ─────────────────────────────────────────────────────────
const MAX_HISTORY = 30
const history     = ref([])   // 상태 스냅샷 배열 (deep copy)
const historyStep = ref(-1)   // 현재 포인터

const canUndo = computed(() => historyStep.value > 0)
const canRedo = computed(() => historyStep.value < history.value.length - 1)

// 현재 editLayers를 스냅샷으로 히스토리에 기록.
// 포인터 이후의 Redo 분기는 제거 (새 행동 발생 시 미래 취소).
function saveHistory() {
  const snapshot = JSON.parse(JSON.stringify(editLayers.value))
  history.value = history.value.slice(0, historyStep.value + 1)
  history.value.push(snapshot)
  if (history.value.length > MAX_HISTORY) history.value.shift()
  else historyStep.value++
}

// undo/redo 후 transformer 해제 + Konva 강제 갱신
function syncKonvaAfterRestore() {
  selectedIdx.value = null
  nextTick(() => {
    const tr = transformerRef.value?.getNode()
    if (tr) tr.nodes([])
    const konvaLayer = elemLayerRef.value?.getNode()
    if (konvaLayer) konvaLayer.batchDraw()
  })
}

function undo() {
  if (!canUndo.value) return
  historyStep.value--
  editLayers.value = JSON.parse(JSON.stringify(history.value[historyStep.value]))
  syncKonvaAfterRestore()
}

function redo() {
  if (!canRedo.value) return
  historyStep.value++
  editLayers.value = JSON.parse(JSON.stringify(history.value[historyStep.value]))
  syncKonvaAfterRestore()
}

function handleKeyUp(e) {
  if (e.code === 'Space') spacebarDown.value = false
}

// ── 키보드 이벤트 등록/해제 ───────────────────────────────────────────────────
onMounted(async () => {
  window.addEventListener('keydown', handleKeyDown)
  window.addEventListener('keyup', handleKeyUp)
})
onBeforeUnmount(() => {
  window.removeEventListener('keydown', handleKeyDown)
  window.removeEventListener('keyup', handleKeyUp)
})

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
    saveHistory()  // 초기 상태를 히스토리 기점으로 저장
    await nextTick()
    cacheAllLayerNodes()  // 투명 픽셀 hit 제외
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
  width:     (layout.value?.target_w  ?? 800) * displayScale.value,
  height:    (layout.value?.target_h  ?? 400) * displayScale.value,
  scaleX:    stageScale.value,
  scaleY:    stageScale.value,
  x:         stagePosX.value,
  y:         stagePosY.value,
  draggable: spacebarDown.value,
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
    draggable:  !spacebarDown.value,
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
  if (spacebarDown.value) return  // 스페이스바 팬 중 클릭 무시
  // 빈 배경 클릭 시 선택 해제
  if (e.target === e.target.getStage()) {
    selectedIdx.value = null
    const tr = transformerRef.value?.getNode()
    if (tr) tr.nodes([])
  }
}

// ── 이벤트 핸들러 ─────────────────────────────────────────────────────────────
function onDragEnd(e, idx) {
  guideLines.value = []  // 드래그 종료 시 가이드선 제거
  const sc = displayScale.value
  editLayers.value[idx].render_x = Math.round(e.target.x() / sc)
  editLayers.value[idx].render_y = Math.round(e.target.y() / sc)
  saveHistory()
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
  saveHistory()
}

function updateField(idx, field, value) {
  editLayers.value[idx][field] = value
  saveHistory()
  nextTick(() => {
    const layer = elemLayerRef.value?.getNode()
    if (layer) layer.batchDraw()
  })
}

// ── Zoom/Pan 핸들러 ───────────────────────────────────────────────────────────
function onStageWheel(e) {
  e.evt.preventDefault()
  const stage    = e.target.getStage()
  const oldScale = stageScale.value
  const pointer  = stage.getPointerPosition()

  // 포인터 아래의 stage content 좌표 — 이 지점을 줌 기준점으로 고정
  const contentX = (pointer.x - stagePosX.value) / oldScale
  const contentY = (pointer.y - stagePosY.value) / oldScale

  const direction = e.evt.deltaY < 0 ? 1 : -1
  const newScale  = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN,
    oldScale * (direction > 0 ? ZOOM_FACTOR : 1 / ZOOM_FACTOR)
  ))

  // 기준점이 화면에서 같은 위치를 유지하도록 stagePosX/Y 재계산
  stagePosX.value  = pointer.x - contentX * newScale
  stagePosY.value  = pointer.y - contentY * newScale
  stageScale.value = newScale
}

function onStagePan(e) {
  // stage draggable=true 일 때 drag 위치를 상태에 동기화
  stagePosX.value = e.target.x()
  stagePosY.value = e.target.y()
}

function resetZoom() {
  stageScale.value = 1
  stagePosX.value  = 0
  stagePosY.value  = 0
}

// 투명 픽셀을 히트 영역에서 제외 (누끼 PNG 선택 정확도 향상)
function cacheAllLayerNodes() {
  const konvaLayer = elemLayerRef.value?.getNode()
  if (!konvaLayer) return
  editLayers.value.forEach((lyr, idx) => {
    const node = konvaLayer.findOne(`.layer-${idx}`)
    if (node) node.cache()
  })
}

// ── 스냅 헬퍼 ─────────────────────────────────────────────────────────────────
// nodeEdges: 드래그 중인 노드의 기준점 배열, targets: 스냅 대상 위치 배열
// 가장 먼저 발견된 SNAP_THRESHOLD 이내 매칭 반환
function findSnap(nodeEdges, targets) {
  for (const { pos } of targets) {
    for (const edge of nodeEdges) {
      if (Math.abs(edge - pos) < SNAP_THRESHOLD) {
        return { delta: pos - edge, snapPos: pos }
      }
    }
  }
  return null
}

function onDragMove(e, idx) {
  const node   = e.target
  const nx     = node.x()
  const ny     = node.y()
  const nw     = node.width()  * node.scaleX()
  const nh     = node.height() * node.scaleY()
  const stageW = stageConfig.value.width
  const stageH = stageConfig.value.height
  const sc     = displayScale.value

  // 스냅 대상: 캔버스 중앙축 + 타 레이어 bbox 3점(좌·중·우 / 상·중·하)
  const xTargets = [{ pos: stageW / 2 }]
  const yTargets = [{ pos: stageH / 2 }]
  editLayers.value.forEach((lyr, i) => {
    if (i === idx) return
    const lx = lyr.render_x * sc,  lw = lyr.render_w * sc
    const ly = lyr.render_y * sc,  lh = lyr.render_h * sc
    xTargets.push({ pos: lx }, { pos: lx + lw / 2 }, { pos: lx + lw })
    yTargets.push({ pos: ly }, { pos: ly + lh / 2 }, { pos: ly + lh })
  })

  const newGuideLines = []

  // X축 스냅 (노드 좌·중·우 기준점)
  const snapX = findSnap([nx, nx + nw / 2, nx + nw], xTargets)
  if (snapX) {
    node.x(nx + snapX.delta)
    newGuideLines.push({
      points: [snapX.snapPos, 0, snapX.snapPos, stageH],
      stroke: '#FF006B', strokeWidth: 1, dash: [4, 3], listening: false,
    })
  }

  // Y축 스냅 (노드 상·중·하 기준점)
  const snapY = findSnap([ny, ny + nh / 2, ny + nh], yTargets)
  if (snapY) {
    node.y(ny + snapY.delta)
    newGuideLines.push({
      points: [0, snapY.snapPos, stageW, snapY.snapPos],
      stroke: '#FF006B', strokeWidth: 1, dash: [4, 3], listening: false,
    })
  }

  guideLines.value = newGuideLines
}

// ── 키보드 Nudge ──────────────────────────────────────────────────────────────
function handleKeyDown(e) {
  const activeTag = document.activeElement?.tagName
  const isInputFocused = activeTag === 'INPUT' || activeTag === 'TEXTAREA' || activeTag === 'SELECT'

  // Ctrl+Z (Undo) / Ctrl+Y / Ctrl+Shift+Z (Redo) — 입력창 포커스 중에는 브라우저 기본 동작 유지
  if ((e.ctrlKey || e.metaKey) && !isInputFocused) {
    if (e.key === 'z' && !e.shiftKey) { e.preventDefault(); undo(); return }
    if (e.key === 'y' || (e.key === 'z' && e.shiftKey)) { e.preventDefault(); redo(); return }
  }

  // Spacebar: 팬 모드 활성화 (입력창 포커스 중 제외)
  if (e.code === 'Space' && !isInputFocused) {
    e.preventDefault()
    spacebarDown.value = true
    return
  }

  // 수치 입력창 포커스 중일 때는 방향키가 입력란에서 동작해야 하므로 제외
  if (isInputFocused) return
  if (selectedIdx.value === null) return

  const isArrow = ['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(e.key)
  if (!isArrow) return
  e.preventDefault()

  const step = e.shiftKey ? NUDGE_STEP_SHIFT : NUDGE_STEP
  const lyr  = editLayers.value[selectedIdx.value]

  if (e.key === 'ArrowLeft')  lyr.render_x -= step
  if (e.key === 'ArrowRight') lyr.render_x += step
  if (e.key === 'ArrowUp')    lyr.render_y -= step
  if (e.key === 'ArrowDown')  lyr.render_y += step

  // Konva 노드 + transformer 즉시 동기화
  nextTick(() => {
    const konvaLayer = elemLayerRef.value?.getNode()
    if (!konvaLayer) return
    const sc   = displayScale.value
    const node = konvaLayer.findOne(`.layer-${selectedIdx.value}`)
    if (node) {
      node.x(lyr.render_x * sc)
      node.y(lyr.render_y * sc)
      const tr = transformerRef.value?.getNode()
      if (tr) tr.forceUpdate()
      konvaLayer.batchDraw()
    }
  })
}

// ── Z-Index (레이어 순서 변경) ────────────────────────────────────────────────
// splice로 배열 원소를 이동 → Vue 반응형으로 Konva v-for 순서 자동 갱신
function moveLayerUp(idx) {
  if (idx === 0) return
  const layers = editLayers.value
  const item = layers.splice(idx, 1)[0]
  layers.splice(idx - 1, 0, item)
  selectedIdx.value = idx - 1
  saveHistory()
}

function moveLayerDown(idx) {
  if (idx === editLayers.value.length - 1) return
  const layers = editLayers.value
  const item = layers.splice(idx, 1)[0]
  layers.splice(idx + 1, 0, item)
  selectedIdx.value = idx + 1
  saveHistory()
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
.btn-undo  { background: #F9FAFB; border: 1px solid #D1D5DB; border-radius: 6px; padding: 6px 10px; cursor: pointer; font-size: 14px; }
.btn-undo:disabled { opacity: 0.35; cursor: default; }
.zoom-display { font-size: 12px; color: #6B7280; min-width: 36px; text-align: center; flex-shrink: 0; }
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
.layer-order-btns { display: flex; flex-direction: column; gap: 1px; flex-shrink: 0; }
.layer-order-btn { background: none; border: none; padding: 0 2px; cursor: pointer; font-size: 9px; color: #9CA3AF; line-height: 1; }
.layer-order-btn:hover:not(:disabled) { color: #374151; }
.layer-order-btn:disabled { opacity: 0.25; cursor: default; }

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
