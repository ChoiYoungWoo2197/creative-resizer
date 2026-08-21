<template>
  <div class="editor-wrap">
    <!-- ── 상단 툴바 ─────────────────────────────────────────────────────── -->
    <div class="editor-bar">
      <button class="back-btn" @click="router.push(`/job/${jobId}`)">← 상세</button>
      <span class="editor-title">{{ specFileName }}</span>
      <div class="editor-actions">
        <button class="btn-tool" @click="undo" :disabled="!canUndo" title="실행 취소 (Ctrl+Z)">↩</button>
        <button class="btn-tool" @click="redo" :disabled="!canRedo" title="다시 실행 (Ctrl+Y)">↪</button>
        <div class="bar-sep" />
        <span class="zoom-display">{{ Math.round(stageScale * 100) }}%</span>
        <button class="btn-tool" @click="resetZoom" :disabled="stageScale === 1 && stagePosX === 0 && stagePosY === 0" title="줌 리셋">⊙</button>
        <div class="bar-sep" />
        <button class="btn-reset" @click="resetLayers" :disabled="saving">초기화</button>
        <button class="btn-save" @click="saveAndRecomposite" :disabled="saving || !isDirty">
          <span v-if="saving" class="spin" />
          {{ saving ? '재합성 중...' : '재합성' }}
        </button>
      </div>
    </div>

    <!-- ── 로딩 ─────────────────────────────────────────────────────────── -->
    <div v-if="loading" class="center-state">
      <div class="spinner" />
      <p>레이아웃 데이터 로딩 중...</p>
    </div>

    <!-- ── 오류 ─────────────────────────────────────────────────────────── -->
    <div v-else-if="error" class="error-box">
      <div class="error-title">로딩 실패</div>
      <div class="error-msg">{{ error }}</div>
    </div>

    <!-- ── 3단 에디터 ─────────────────────────────────────────────────────── -->
    <div v-else-if="layout" class="canvas-area">

      <!-- 좌측: Layers 패널 (PSD 전체 트리) -->
      <div class="left-panel">
        <div class="panel-section-title">Layers</div>

        <!-- PSD 트리 뷰 (layers_merged 로드 성공 시) -->
        <template v-if="mergedLayers.length">
          <div
            v-for="(lyr, i) in mergedLayers"
            :key="i"
            class="tree-item"
            :class="{
              'tree-rendered': !!lyr.rendered,
              'tree-dim':      !lyr.rendered,
              active: !!lyr.rendered && lyr.name === selectedLayerName,
            }"
            :style="{ paddingLeft: (10 + lyr.depth * 14) + 'px' }"
            @click="lyr.rendered && selectByName(lyr.name)"
          >
            <span class="tree-chevron">{{ lyr.bbox === null ? '▸' : '  ' }}</span>
            <span class="layer-dot" :class="lyr.rendered ? 'role-' + lyr.role : ''" />
            <span class="tree-name">{{ lyr.name }}</span>
            <div v-if="lyr.rendered" class="layer-order-btns" @click.stop>
              <button class="layer-order-btn" @click="moveLayerUpByName(lyr.name)"   :disabled="editLayerIdx(lyr.name) === 0">▲</button>
              <button class="layer-order-btn" @click="moveLayerDownByName(lyr.name)" :disabled="editLayerIdx(lyr.name) === editLayers.length - 1">▼</button>
            </div>
          </div>
        </template>

        <!-- fallback: mergedLayers 없으면 기존 editLayers flat list -->
        <template v-else>
          <div
            v-for="(lyr, idx) in editLayers"
            :key="lyr.name"
            class="layer-item"
            :class="{ active: selectedIdx === idx }"
            @click="selectLayer(idx)"
          >
            <span class="layer-dot" :class="'role-' + lyr.role" />
            <span class="layer-name">{{ lyr.name }}</span>
            <div class="layer-order-btns" @click.stop>
              <button class="layer-order-btn" @click="moveLayerUp(idx)"   :disabled="idx === 0">▲</button>
              <button class="layer-order-btn" @click="moveLayerDown(idx)" :disabled="idx === editLayers.length - 1">▼</button>
            </div>
          </div>
        </template>
      </div>

      <!-- 중앙: Konva 캔버스 워크스페이스 -->
      <div class="konva-wrap" ref="konvaWrapRef" :style="{ cursor: spacebarDown ? 'grab' : 'default' }">
        <v-stage
          :config="stageConfig"
          @click="onStageClick"
          @wheel="onStageWheel"
          @dragmove="onStagePan"
          @dragend="onStagePan"
        >
          <!-- 배경 레이어 (선택/드래그 불가) -->
          <v-layer>
            <v-image :config="bgConfig" />
          </v-layer>

          <!-- 요소 레이어 -->
          <v-layer ref="elemLayerRef">
            <v-image
              v-for="(lyr, idx) in editLayers"
              :key="lyr.name"
              :config="layerConfig(lyr, idx)"
              @click="selectLayer(idx)"
              @dragmove="onDragMove($event, idx)"
              @dragend="onDragEnd($event, idx)"
              @transform="onTransform($event, idx)"
              @transformend="onTransformEnd($event, idx)"
            />
            <v-transformer ref="transformerRef" :config="transformerConfig" />
          </v-layer>

          <!-- 가이드선 레이어 (이벤트 비활성) -->
          <v-layer :config="{ listening: false }">
            <v-line v-for="(gl, i) in guideLines" :key="i" :config="gl" />
          </v-layer>
        </v-stage>
      </div>

      <!-- 우측: Design 패널 -->
      <div class="right-panel">
        <div class="panel-section-title">Design</div>

        <template v-if="selectedIdx !== null">
          <!-- 선택된 레이어 정보 -->
          <div class="design-meta">
            <span class="layer-dot" :class="'role-' + editLayers[selectedIdx].role" />
            <span class="design-meta-name">{{ editLayers[selectedIdx].name }}</span>
          </div>
          <span class="design-role-badge">{{ editLayers[selectedIdx].role }}</span>

          <div class="design-group-label">Position</div>
          <div class="design-row">
            <div class="design-field">
              <label>X</label>
              <input type="number" :value="editLayers[selectedIdx].render_x"
                @change="updateField(selectedIdx, 'render_x', +$event.target.value)" />
            </div>
            <div class="design-field">
              <label>Y</label>
              <input type="number" :value="editLayers[selectedIdx].render_y"
                @change="updateField(selectedIdx, 'render_y', +$event.target.value)" />
            </div>
          </div>

          <div class="design-group-label">Size</div>
          <div class="design-row">
            <div class="design-field">
              <label>W</label>
              <input type="number" :value="editLayers[selectedIdx].render_w"
                @change="updateField(selectedIdx, 'render_w', Math.max(1, +$event.target.value))" />
            </div>
            <div class="design-field">
              <label>H</label>
              <input type="number" :value="editLayers[selectedIdx].render_h"
                @change="updateField(selectedIdx, 'render_h', Math.max(1, +$event.target.value))" />
            </div>
          </div>
        </template>

        <div v-else class="design-empty">레이어를 선택하세요</div>
      </div>
    </div>

    <!-- 재합성 완료 토스트 -->
    <div v-if="savedMsg" class="toast">{{ savedMsg }}</div>
  </div>
</template>

<script setup>
import { ref, computed, nextTick, onMounted, onBeforeUnmount } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { getLayoutResult, getLayersMerged, layerFileUrl, recomposite } from '../api/banner.js'

const route  = useRoute()
const router = useRouter()

const jobId        = route.params.id
const fileName     = route.query.fileName
const specFileName = ref(fileName || '')

// ── 상태 ──────────────────────────────────────────────────────────────────────
const loading      = ref(true)
const error        = ref('')
const layout       = ref(null)
const editLayers   = ref([])
const origLayers   = ref([])
const mergedLayers = ref([])   // PSD 전체 트리 (P2 + P4 merged)
const selectedIdx  = ref(null)
const saving       = ref(false)
const savedMsg     = ref('')

// 현재 선택된 레이어명 (mergedLayers 트리에서 active 판별용)
const selectedLayerName = computed(() =>
  selectedIdx.value !== null ? editLayers.value[selectedIdx.value]?.name : null
)

// konva refs
const transformerRef = ref(null)
const elemLayerRef   = ref(null)

// 캔버스 표시 스케일
const DISPLAY_MAX_W = 900
const DISPLAY_MAX_H = 600
const displayScale  = ref(1)

// ── Zoom/Pan 상태 ─────────────────────────────────────────────────────────────
const ZOOM_MIN    = 0.5
const ZOOM_MAX    = 3.0
const ZOOM_FACTOR = 1.1

const stageScale   = ref(1)
const stagePosX    = ref(0)
const stagePosY    = ref(0)
const spacebarDown = ref(false)

// ── 스냅/Nudge 상수 ───────────────────────────────────────────────────────────
const SNAP_THRESHOLD   = 5
const NUDGE_STEP       = 1
const NUDGE_STEP_SHIFT = 10

const guideLines = ref([])

// ── Figma 스타일 Transformer 설정 ─────────────────────────────────────────────
const transformerConfig = {
  keepRatio: false,   // 8방향 자유 리사이즈
  anchorSize: 8,
  anchorCornerRadius: 50,
  anchorFill: '#FFFFFF',
  anchorStroke: '#0D99FF',
  anchorStrokeWidth: 1.5,
  borderStroke: '#0D99FF',
  borderStrokeWidth: 1.5,
}

// ── Undo/Redo 히스토리 ─────────────────────────────────────────────────────────
const MAX_HISTORY = 30
const history     = ref([])
const historyStep = ref(-1)

const canUndo = computed(() => historyStep.value > 0)
const canRedo = computed(() => historyStep.value < history.value.length - 1)

function saveHistory() {
  const snapshot = JSON.parse(JSON.stringify(editLayers.value))
  history.value = history.value.slice(0, historyStep.value + 1)
  history.value.push(snapshot)
  if (history.value.length > MAX_HISTORY) history.value.shift()
  else historyStep.value++
}

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

// ── 키보드 이벤트 ─────────────────────────────────────────────────────────────
function handleKeyUp(e) {
  if (e.code === 'Space') spacebarDown.value = false
}

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
    origLayers.value  = JSON.parse(JSON.stringify(res.data.layers))

    const tw = res.data.target_w
    const th = res.data.target_h
    displayScale.value = Math.min(1, DISPLAY_MAX_W / tw, DISPLAY_MAX_H / th)

    await loadLayerImages()
    saveHistory()
    await nextTick()
    cacheAllLayerNodes()

    // PSD 전체 트리 로드 (실패해도 에디터는 동작)
    try {
      const merged = await getLayersMerged(jobId, fileName)
      mergedLayers.value = merged.data.layers || []
    } catch { /* silent fallback */ }
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
const imgCache = ref({})

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
  width:     (layout.value?.target_w ?? 800) * displayScale.value,
  height:    (layout.value?.target_h ?? 400) * displayScale.value,
  listening: false,
}))

function layerConfig(lyr, idx) {
  const sc = displayScale.value
  return {
    image:     imgCache.value[lyr.name] ?? null,
    x:         lyr.render_x * sc,
    y:         lyr.render_y * sc,
    width:     lyr.render_w * sc,
    height:    lyr.render_h * sc,
    draggable: !spacebarDown.value,
    name:      `layer-${idx}`,
  }
}

async function loadLayerImages() {
  if (layout.value?.bg_file) {
    bgImage.value = await loadImg(layerFileUrl(jobId, layout.value.bg_file))
  }
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
  if (spacebarDown.value) return
  if (e.target === e.target.getStage()) {
    selectedIdx.value = null
    const tr = transformerRef.value?.getNode()
    if (tr) tr.nodes([])
  }
}

// ── 이벤트 핸들러 ─────────────────────────────────────────────────────────────
function onDragEnd(e, idx) {
  guideLines.value = []
  const sc = displayScale.value
  editLayers.value[idx].render_x = Math.round(e.target.x() / sc)
  editLayers.value[idx].render_y = Math.round(e.target.y() / sc)
  saveHistory()
}

// 리사이즈 중 실시간으로 scale을 width/height에 반영 — 핸들 드래그 중 부드러운 조작감
function onTransform(e, idx) {
  const node = e.target
  node.width(node.width()   * node.scaleX())
  node.height(node.height() * node.scaleY())
  node.scaleX(1)
  node.scaleY(1)
}

function onTransformEnd(e, idx) {
  const node = e.target
  const sc   = displayScale.value
  editLayers.value[idx].render_x = Math.round(node.x()      / sc)
  editLayers.value[idx].render_y = Math.round(node.y()      / sc)
  editLayers.value[idx].render_w = Math.round(node.width()  * node.scaleX() / sc)
  editLayers.value[idx].render_h = Math.round(node.height() * node.scaleY() / sc)
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

  const contentX = (pointer.x - stagePosX.value) / oldScale
  const contentY = (pointer.y - stagePosY.value) / oldScale

  const direction = e.evt.deltaY < 0 ? 1 : -1
  const newScale  = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN,
    oldScale * (direction > 0 ? ZOOM_FACTOR : 1 / ZOOM_FACTOR)
  ))

  stagePosX.value  = pointer.x - contentX * newScale
  stagePosY.value  = pointer.y - contentY * newScale
  stageScale.value = newScale
}

function onStagePan(e) {
  // 노드 drag 이벤트가 stage로 버블링되는 경우 무시 — stage 자체 드래그만 처리
  if (e.target !== e.target.getStage()) return
  stagePosX.value = e.target.x()
  stagePosY.value = e.target.y()
}

function resetZoom() {
  stageScale.value = 1
  stagePosX.value  = 0
  stagePosY.value  = 0
}

function cacheAllLayerNodes() {
  const konvaLayer = elemLayerRef.value?.getNode()
  if (!konvaLayer) return
  editLayers.value.forEach((lyr, idx) => {
    const node = konvaLayer.findOne(`.layer-${idx}`)
    if (node) node.cache()
  })
}

// ── 스냅 핸들러 ───────────────────────────────────────────────────────────────
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

  const xTargets = [{ pos: stageW / 2 }]
  const yTargets = [{ pos: stageH / 2 }]
  editLayers.value.forEach((lyr, i) => {
    if (i === idx) return
    const lx = lyr.render_x * sc, lw = lyr.render_w * sc
    const ly = lyr.render_y * sc, lh = lyr.render_h * sc
    xTargets.push({ pos: lx }, { pos: lx + lw / 2 }, { pos: lx + lw })
    yTargets.push({ pos: ly }, { pos: ly + lh / 2 }, { pos: ly + lh })
  })

  const newGuideLines = []

  const snapX = findSnap([nx, nx + nw / 2, nx + nw], xTargets)
  if (snapX) {
    node.x(nx + snapX.delta)
    newGuideLines.push({
      points: [snapX.snapPos, 0, snapX.snapPos, stageH],
      stroke: '#FF007A', strokeWidth: 1, dash: [4, 3], listening: false,
    })
  }

  const snapY = findSnap([ny, ny + nh / 2, ny + nh], yTargets)
  if (snapY) {
    node.y(ny + snapY.delta)
    newGuideLines.push({
      points: [0, snapY.snapPos, stageW, snapY.snapPos],
      stroke: '#FF007A', strokeWidth: 1, dash: [4, 3], listening: false,
    })
  }

  guideLines.value = newGuideLines
}

// ── 키보드 Nudge ──────────────────────────────────────────────────────────────
function handleKeyDown(e) {
  const activeTag = document.activeElement?.tagName
  const isInputFocused = activeTag === 'INPUT' || activeTag === 'TEXTAREA' || activeTag === 'SELECT'

  if ((e.ctrlKey || e.metaKey) && !isInputFocused) {
    if (e.key === 'z' && !e.shiftKey) { e.preventDefault(); undo(); return }
    if (e.key === 'y' || (e.key === 'z' && e.shiftKey)) { e.preventDefault(); redo(); return }
  }

  if (e.code === 'Space' && !isInputFocused) {
    e.preventDefault()
    spacebarDown.value = true
    return
  }

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

// ── 트리 뷰 헬퍼 (mergedLayers → editLayers 연결) ──────────────────────────────
function editLayerIdx(name) {
  return editLayers.value.findIndex(l => l.name === name)
}
function selectByName(name) {
  const idx = editLayerIdx(name)
  if (idx !== -1) selectLayer(idx)
}
function moveLayerUpByName(name) {
  const idx = editLayerIdx(name)
  if (idx > 0) moveLayerUp(idx)
}
function moveLayerDownByName(name) {
  const idx = editLayerIdx(name)
  if (idx < editLayers.value.length - 1) moveLayerDown(idx)
}

// ── Z-Index 변경 ──────────────────────────────────────────────────────────────
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

// ── 더티 체크 / 초기화 ─────────────────────────────────────────────────────────
const isDirty = computed(() =>
  JSON.stringify(editLayers.value) !== JSON.stringify(origLayers.value)
)

function resetLayers() {
  editLayers.value  = JSON.parse(JSON.stringify(origLayers.value))
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
/* ── 전체 레이아웃 ───────────────────────────────────────────────────────────── */
.editor-wrap { display: flex; flex-direction: column; height: 100vh; background: #E5E5E5; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }

/* ── 상단 툴바 ──────────────────────────────────────────────────────────────── */
.editor-bar {
  display: flex; align-items: center; gap: 10px;
  padding: 0 16px; height: 48px;
  background: #FFFFFF; border-bottom: 1px solid #E5E5E5;
  flex-shrink: 0; box-shadow: 0 1px 0 #E5E5E5;
}
.back-btn {
  background: none; border: 1px solid #E5E5E5; border-radius: 6px;
  padding: 5px 10px; cursor: pointer; font-size: 12px; color: #333;
  white-space: nowrap;
}
.back-btn:hover { background: #F5F5F5; }
.editor-title {
  flex: 1; font-size: 12px; font-weight: 600; color: #333;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.editor-actions { display: flex; align-items: center; gap: 6px; }
.bar-sep { width: 1px; height: 20px; background: #E5E5E5; margin: 0 2px; }

.btn-tool {
  background: none; border: 1px solid transparent; border-radius: 6px;
  padding: 5px 9px; cursor: pointer; font-size: 14px; color: #333;
  transition: background 0.1s;
}
.btn-tool:hover:not(:disabled) { background: #F0F0F0; border-color: #E5E5E5; }
.btn-tool:disabled { opacity: 0.35; cursor: default; }

.zoom-display {
  font-size: 12px; color: #888; min-width: 38px;
  text-align: center; flex-shrink: 0;
}

.btn-reset {
  background: none; border: 1px solid #E5E5E5; border-radius: 6px;
  padding: 5px 12px; cursor: pointer; font-size: 12px; color: #333;
}
.btn-reset:hover:not(:disabled) { background: #F5F5F5; }
.btn-reset:disabled { opacity: 0.4; cursor: default; }

.btn-save {
  background: #0D99FF; color: #fff; border: none; border-radius: 6px;
  padding: 6px 14px; cursor: pointer; font-size: 12px; font-weight: 600;
  display: flex; align-items: center; gap: 6px; white-space: nowrap;
  transition: background 0.15s;
}
.btn-save:hover:not(:disabled) { background: #0B87E0; }
.btn-save:disabled { opacity: 0.45; cursor: default; }
.spin {
  width: 12px; height: 12px;
  border: 2px solid rgba(255,255,255,0.4); border-top-color: #fff;
  border-radius: 50%; animation: spin 0.7s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }

/* ── 로딩 / 오류 ─────────────────────────────────────────────────────────────── */
.center-state {
  display: flex; flex-direction: column; align-items: center;
  justify-content: center; flex: 1; gap: 12px; color: #888; font-size: 13px;
}
.spinner {
  width: 28px; height: 28px;
  border: 2.5px solid #E5E5E5; border-top-color: #0D99FF;
  border-radius: 50%; animation: spin 0.8s linear infinite;
}
.error-box {
  margin: 40px auto; max-width: 500px;
  background: #FFF2F2; border: 1px solid #FFCDD2; border-radius: 8px; padding: 24px;
}
.error-title { font-weight: 700; color: #C62828; margin-bottom: 8px; font-size: 14px; }
.error-msg   { font-size: 13px; color: #7F1D1D; }

/* ── 3단 레이아웃 ────────────────────────────────────────────────────────────── */
.canvas-area { display: flex; flex: 1; overflow: hidden; }

/* 좌측 Layers 패널 */
.left-panel {
  width: 220px; flex-shrink: 0; background: #FFFFFF;
  border-right: 1px solid #E5E5E5; overflow-y: auto; padding: 0;
}

/* 중앙 캔버스 워크스페이스 */
.konva-wrap {
  flex: 1; display: flex; align-items: center; justify-content: center;
  background: #E5E5E5; overflow: auto;
}
.konva-wrap :deep(canvas) {
  box-shadow: 0 2px 16px rgba(0,0,0,0.15), 0 0 0 1px rgba(0,0,0,0.06);
}

/* 우측 Design 패널 */
.right-panel {
  width: 220px; flex-shrink: 0; background: #FFFFFF;
  border-left: 1px solid #E5E5E5; overflow-y: auto; padding: 0;
}

/* ── 패널 공통 ───────────────────────────────────────────────────────────────── */
.panel-section-title {
  font-size: 11px; font-weight: 700; color: #888;
  text-transform: uppercase; letter-spacing: 0.06em;
  padding: 12px 14px 8px; border-bottom: 1px solid #E5E5E5;
  position: sticky; top: 0; background: #FFFFFF; z-index: 1;
}

/* ── Layers 패널 아이템 ──────────────────────────────────────────────────────── */
.layer-item {
  display: flex; align-items: center; gap: 8px;
  padding: 7px 12px; cursor: pointer; font-size: 12px; color: #333;
  border-bottom: 1px solid #F5F5F5; user-select: none;
}
.layer-item:hover  { background: #F5F5F5; }
.layer-item.active { background: #E5F2FF; }

.layer-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; background: #CCC; }
.layer-dot.role-title        { background: #0D99FF; }
.layer-dot.role-product      { background: #10B981; }
.layer-dot.role-logo         { background: #F59E0B; }
.layer-dot.role-person       { background: #EC4899; }
.layer-dot.role-badge        { background: #8B5CF6; }
.layer-dot.role-body         { background: #888; }
.layer-dot.role-title-group  { background: #0D99FF; }
.layer-dot.role-product-group{ background: #10B981; }

.layer-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 12px; }

/* ── PSD 트리 뷰 ─────────────────────────────────────────────────────────────── */
.tree-item {
  display: flex; align-items: center; gap: 5px;
  padding-top: 5px; padding-bottom: 5px; padding-right: 10px;
  font-size: 12px; border-bottom: 1px solid #F5F5F5; user-select: none;
}
.tree-item.tree-rendered { cursor: pointer; color: #333; }
.tree-item.tree-rendered:hover { background: #F5F5F5; }
.tree-item.tree-rendered.active { background: #E5F2FF; }
.tree-item.tree-dim { cursor: default; color: #BBB; }
.tree-chevron { font-size: 9px; width: 12px; flex-shrink: 0; color: #AAA; }
.tree-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.layer-order-btns { display: flex; flex-direction: column; gap: 1px; flex-shrink: 0; }
.layer-order-btn {
  background: none; border: none; padding: 0 2px; cursor: pointer;
  font-size: 9px; color: #CCC; line-height: 1;
}
.layer-order-btn:hover:not(:disabled) { color: #333; }
.layer-order-btn:disabled { opacity: 0.25; cursor: default; }

/* ── Design 패널 ─────────────────────────────────────────────────────────────── */
.design-meta {
  display: flex; align-items: center; gap: 8px;
  padding: 10px 14px 4px; font-size: 12px; color: #333;
}
.design-meta-name { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.design-role-badge {
  display: inline-block; margin: 0 14px 10px;
  padding: 2px 8px; border-radius: 10px;
  background: #F0F0F0; color: #888; font-size: 10px; font-weight: 600;
  text-transform: uppercase; letter-spacing: 0.04em;
}

.design-group-label {
  font-size: 10px; font-weight: 700; color: #888;
  text-transform: uppercase; letter-spacing: 0.06em;
  padding: 10px 14px 4px;
}
.design-row { display: flex; gap: 8px; padding: 0 14px 6px; }
.design-field { flex: 1; display: flex; flex-direction: column; gap: 3px; }
.design-field label {
  font-size: 10px; color: #888; font-weight: 600;
  text-transform: uppercase; letter-spacing: 0.04em;
}
.design-field input {
  border: 1px solid #E5E5E5; border-radius: 5px;
  padding: 5px 8px; font-size: 12px; color: #333; width: 100%; box-sizing: border-box;
  background: #FAFAFA;
}
.design-field input:focus { outline: none; border-color: #0D99FF; background: #FFFFFF; }

.design-empty {
  padding: 24px 14px; font-size: 12px; color: #888; text-align: center;
}

/* ── 토스트 ──────────────────────────────────────────────────────────────────── */
.toast {
  position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%);
  background: #1A1A1A; color: #fff; padding: 10px 20px;
  border-radius: 8px; font-size: 13px; z-index: 999;
  box-shadow: 0 4px 16px rgba(0,0,0,0.25); white-space: nowrap;
}
</style>
