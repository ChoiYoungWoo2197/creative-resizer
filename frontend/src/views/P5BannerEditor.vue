<template>
  <div class="editor-wrap">
    <!-- ── 상단 툴바 ─────────────────────────────────────────────────────── -->
    <div class="editor-bar">
      <button class="back-btn" @click="router.push('/jobs')">← 상세</button>
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
            <button v-if="lyr.rendered" class="vis-btn" :class="{ 'vis-hidden': hiddenNames.has(lyr.name) }"
              @click.stop="toggleVis(lyr.name)" :title="hiddenNames.has(lyr.name) ? '표시' : '숨기기'">
              <svg v-if="!hiddenNames.has(lyr.name)" width="14" height="10" viewBox="0 0 14 10" fill="none" xmlns="http://www.w3.org/2000/svg">
                <path d="M7 1C4 1 1.5 5 1.5 5C1.5 5 4 9 7 9C10 9 12.5 5 12.5 5C12.5 5 10 1 7 1Z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/>
                <circle cx="7" cy="5" r="1.8" fill="currentColor"/>
              </svg>
              <svg v-else width="14" height="10" viewBox="0 0 14 10" fill="none" xmlns="http://www.w3.org/2000/svg">
                <path d="M7 1C4 1 1.5 5 1.5 5C1.5 5 4 9 7 9C10 9 12.5 5 12.5 5C12.5 5 10 1 7 1Z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round" opacity="0.35"/>
                <line x1="1.5" y1="9" x2="12.5" y2="1" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>
              </svg>
            </button>
            <span v-else class="vis-placeholder" />
            <span class="tree-chevron">{{ lyr.bbox === null ? '▸' : '  ' }}</span>
            <span class="layer-dot" :class="lyr.rendered ? 'role-' + lyr.role : ''" />
            <span class="tree-name">{{ lyr.name }}</span>
            <span v-if="isTypeLayer(lyr.name) && textOverrides[lyr.name]" class="text-badge">T</span>
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

          <!-- 텍스트 오버레이 레이어 (이벤트 비활성, 순수 시각) -->
          <v-layer :config="{ listening: false }">
            <v-text
              v-for="lyr in editLayers"
              :key="'txt-' + lyr.name"
              v-show="isTypeLayer(lyr.name) && !!textOverrides[lyr.name] && !hiddenNames.has(lyr.name)"
              :config="textOverlayConfig(lyr)"
            />
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
          <!-- Text + Font 섹션 (badge 제외 type 레이어에만 표시) -->
          <template v-if="isTypeLayer(editLayers[selectedIdx].name)">
            <div class="design-group-label">Text</div>
            <div class="design-text-area">
              <textarea
                class="text-override-input"
                :value="textOverrides[editLayers[selectedIdx].name] ?? ''"
                @input="setTextOverride(editLayers[selectedIdx].name, $event.target.value)"
                placeholder="텍스트를 입력하세요"
                rows="4"
              />
              <div v-if="textOverrides[editLayers[selectedIdx].name]" class="text-override-hint">
                ✏ 오버라이드 적용 중
              </div>
            </div>

            <div class="design-group-label">Font</div>

            <!-- 크기 + 색상 -->
            <div class="design-row">
              <div class="design-field">
                <label>Size</label>
                <input type="number" min="6" max="300"
                  :value="getFontSetting(selectedLayerName, 'fontSize') ?? Math.round(editLayers[selectedIdx].render_h * 0.62)"
                  @change="setFontSetting(selectedLayerName, 'fontSize', Math.max(6, +$event.target.value))"
                />
              </div>
              <div class="design-field">
                <label>Color</label>
                <input type="color" class="color-input"
                  :value="getFontSetting(selectedLayerName, 'color') ?? '#111111'"
                  @input="setFontSetting(selectedLayerName, 'color', $event.target.value)"
                />
              </div>
            </div>

            <!-- 폰트 패밀리 -->
            <div class="design-row-full">
              <label class="design-field-label">Family</label>
              <select class="font-select"
                :value="getFontSetting(selectedLayerName, 'fontFamily') ?? 'Apple SD Gothic Neo'"
                @change="setFontSetting(selectedLayerName, 'fontFamily', $event.target.value)"
              >
                <option value="Apple SD Gothic Neo">Apple SD Gothic Neo</option>
                <option value="Malgun Gothic">맑은 고딕 (Malgun Gothic)</option>
                <option value="Noto Sans KR">Noto Sans KR</option>
                <option value="Arial">Arial</option>
              </select>
            </div>

            <!-- 스타일 + 정렬 -->
            <div class="design-row-full">
              <label class="design-field-label">Style</label>
              <div class="btn-group">
                <button class="btn-fs"
                  :class="{ active: !(getFontSetting(selectedLayerName, 'fontStyle') ?? 'normal').includes('bold') && !(getFontSetting(selectedLayerName, 'fontStyle') ?? 'normal').includes('italic') }"
                  @click="setFontSetting(selectedLayerName, 'fontStyle', 'normal')">N</button>
                <button class="btn-fs" style="font-weight:700"
                  :class="{ active: (getFontSetting(selectedLayerName, 'fontStyle') ?? '').includes('bold') }"
                  @click="toggleFontStyle(selectedLayerName, 'bold')">B</button>
                <button class="btn-fs" style="font-style:italic"
                  :class="{ active: (getFontSetting(selectedLayerName, 'fontStyle') ?? '').includes('italic') }"
                  @click="toggleFontStyle(selectedLayerName, 'italic')">I</button>
              </div>
              <label class="design-field-label" style="margin-left:8px">Align</label>
              <div class="btn-group">
                <button class="btn-fs"
                  :class="{ active: (getFontSetting(selectedLayerName, 'align') ?? 'left') === 'left' }"
                  @click="setFontSetting(selectedLayerName, 'align', 'left')">⬛L</button>
                <button class="btn-fs"
                  :class="{ active: getFontSetting(selectedLayerName, 'align') === 'center' }"
                  @click="setFontSetting(selectedLayerName, 'align', 'center')">⬛C</button>
                <button class="btn-fs"
                  :class="{ active: getFontSetting(selectedLayerName, 'align') === 'right' }"
                  @click="setFontSetting(selectedLayerName, 'align', 'right')">⬛R</button>
              </div>
            </div>

            <!-- 행간 + 자간 -->
            <div class="design-row">
              <div class="design-field">
                <label>행간</label>
                <input type="number" min="0.5" max="4" step="0.1"
                  :value="getFontSetting(selectedLayerName, 'lineHeight') ?? 1"
                  @change="setFontSetting(selectedLayerName, 'lineHeight', Math.max(0.5, +$event.target.value))"
                />
              </div>
              <div class="design-field">
                <label>자간(px)</label>
                <input type="number" min="-10" max="50" step="0.5"
                  :value="getFontSetting(selectedLayerName, 'letterSpacing') ?? 0"
                  @change="setFontSetting(selectedLayerName, 'letterSpacing', +$event.target.value)"
                />
              </div>
            </div>
          </template>
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
const hiddenNames    = ref(new Set())  // 숨긴 레이어 name 집합
const selectedIdx    = ref(null)
const textOverrides  = ref({})         // { layerName: 'override text' } — type 레이어 텍스트 수정
const fontOverrides  = ref({})         // { layerName: { fontSize, fontFamily, fontStyle, align, lineHeight, letterSpacing, color } }
const saving       = ref(false)
const savedMsg     = ref('')

// 현재 선택된 레이어명 (mergedLayers 트리에서 active 판별용)
const selectedLayerName = computed(() =>
  selectedIdx.value !== null ? editLayers.value[selectedIdx.value]?.name : null
)

// badge 제외한 type 레이어 이름 집합 (mergedLayers 기반)
const typeLayerNames = computed(() => {
  const s = new Set()
  mergedLayers.value.forEach(l => {
    if (l.kind === 'type' && l.role !== 'badge') {
      s.add(l.name.replace(/^\[|\]$/g, '').trim())
    }
  })
  return s
})

function isTypeLayer(name) {
  return typeLayerNames.value.has(name)
}

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
  // text override 있으면 이미지 숨김 (투명 상호작용 영역만 유지)
  const hasTextOverride = isTypeLayer(lyr.name) && !!textOverrides.value[lyr.name]
  return {
    image:     hasTextOverride ? null : (imgCache.value[lyr.name] ?? null),
    x:         lyr.render_x * sc,
    y:         lyr.render_y * sc,
    width:     lyr.render_w * sc,
    height:    lyr.render_h * sc,
    opacity:   hiddenNames.value.has(lyr.name) ? 0 : 1,
    draggable: !spacebarDown.value,
    name:      `layer-${idx}`,
  }
}

function textOverlayConfig(lyr) {
  const sc = displayScale.value
  const fo = fontOverrides.value[lyr.name] || {}
  return {
    text:          textOverrides.value[lyr.name] || '',
    x:             lyr.render_x * sc,
    y:             lyr.render_y * sc,
    width:         lyr.render_w * sc,
    fontSize:      (fo.fontSize ?? Math.max(10, Math.round(lyr.render_h * 0.62))) * sc,
    fontFamily:    fo.fontFamily ?? '"Apple SD Gothic Neo", "Malgun Gothic", sans-serif',
    fontStyle:     fo.fontStyle ?? 'normal',
    align:         fo.align ?? 'left',
    lineHeight:    fo.lineHeight ?? 1,
    letterSpacing: (fo.letterSpacing ?? 0) * sc,
    fill:          fo.color ?? '#111111',
    wrap:          'word',
    listening:     false,
  }
}

function setTextOverride(name, text) {
  textOverrides.value = { ...textOverrides.value, [name]: text }
  nextTick(() => { elemLayerRef.value?.getNode()?.batchDraw() })
}

function getFontSetting(name, key) {
  return fontOverrides.value[name]?.[key]
}

function setFontSetting(name, key, value) {
  fontOverrides.value = { ...fontOverrides.value, [name]: { ...(fontOverrides.value[name] || {}), [key]: value } }
  nextTick(() => { elemLayerRef.value?.getNode()?.batchDraw() })
}

function toggleFontStyle(name, style) {
  const current = fontOverrides.value[name]?.fontStyle ?? 'normal'
  let parts = current === 'normal' ? [] : current.split(' ')
  parts = parts.includes(style) ? parts.filter(p => p !== style) : [...parts, style]
  // bold italic 순서 보장
  parts.sort((a, b) => a === 'bold' ? -1 : 1)
  setFontSetting(name, 'fontStyle', parts.length ? parts.join(' ') : 'normal')
}

async function loadLayerImages() {
  const bust = `&t=${Date.now()}`
  if (layout.value?.bg_file) {
    bgImage.value = await loadImg(layerFileUrl(jobId, layout.value.bg_file) + bust)
  }
  for (const lyr of editLayers.value) {
    if (lyr.layer_file) {
      imgCache.value[lyr.name] = await loadImg(layerFileUrl(jobId, lyr.layer_file) + bust)
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
function toggleVis(name) {
  const next = new Set(hiddenNames.value)
  if (next.has(name)) next.delete(name)
  else next.add(name)
  hiddenNames.value = next
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
  || Object.values(textOverrides.value).some(v => !!v)
  || Object.keys(fontOverrides.value).length > 0
)

function resetLayers() {
  editLayers.value    = JSON.parse(JSON.stringify(origLayers.value))
  textOverrides.value = {}
  fontOverrides.value = {}
  selectedIdx.value   = null
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
    // textOverrides: 빈 문자열은 제외하고 전달
    const activeOverrides = Object.fromEntries(
      Object.entries(textOverrides.value).filter(([, v]) => !!v)
    )
    await recomposite(jobId, fileName, payload, Object.keys(activeOverrides).length ? activeOverrides : undefined)
    router.push('/jobs')
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
  padding: 5px 10px; cursor: pointer; font-size: 14px; color: #333;
  white-space: nowrap;
}
.back-btn:hover { background: #F5F5F5; }
.editor-title {
  flex: 1; font-size: 14px; font-weight: 600; color: #333;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.editor-actions { display: flex; align-items: center; gap: 6px; }
.bar-sep { width: 1px; height: 20px; background: #E5E5E5; margin: 0 2px; }

.btn-tool {
  background: none; border: 1px solid transparent; border-radius: 6px;
  padding: 5px 9px; cursor: pointer; font-size: 16px; color: #333;
  transition: background 0.1s;
}
.btn-tool:hover:not(:disabled) { background: #F0F0F0; border-color: #E5E5E5; }
.btn-tool:disabled { opacity: 0.35; cursor: default; }

.zoom-display {
  font-size: 14px; color: #888; min-width: 38px;
  text-align: center; flex-shrink: 0;
}

.btn-reset {
  background: none; border: 1px solid #E5E5E5; border-radius: 6px;
  padding: 5px 12px; cursor: pointer; font-size: 14px; color: #333;
}
.btn-reset:hover:not(:disabled) { background: #F5F5F5; }
.btn-reset:disabled { opacity: 0.4; cursor: default; }

.btn-save {
  background: #0D99FF; color: #fff; border: none; border-radius: 6px;
  padding: 6px 14px; cursor: pointer; font-size: 14px; font-weight: 600;
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
  justify-content: center; flex: 1; gap: 12px; color: #888; font-size: 15px;
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
.error-title { font-weight: 700; color: #C62828; margin-bottom: 8px; font-size: 16px; }
.error-msg   { font-size: 15px; color: #7F1D1D; }

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
  font-size: 13px; font-weight: 700; color: #888;
  text-transform: uppercase; letter-spacing: 0.06em;
  padding: 12px 14px 8px; border-bottom: 1px solid #E5E5E5;
  position: sticky; top: 0; background: #FFFFFF; z-index: 1;
}

/* ── Layers 패널 아이템 ──────────────────────────────────────────────────────── */
.layer-item {
  display: flex; align-items: center; gap: 8px;
  padding: 7px 12px; cursor: pointer; font-size: 14px; color: #333;
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

.layer-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 14px; }

/* ── PSD 트리 뷰 ─────────────────────────────────────────────────────────────── */
.tree-item {
  display: flex; align-items: center; gap: 5px;
  padding-top: 5px; padding-bottom: 5px; padding-right: 10px;
  font-size: 14px; border-bottom: 1px solid #F5F5F5; user-select: none;
}
.tree-item.tree-rendered { cursor: pointer; color: #333; }
.tree-item.tree-rendered:hover { background: #F5F5F5; }
.tree-item.tree-rendered.active { background: #E5F2FF; }
.tree-item.tree-dim { cursor: default; color: #BBB; }
.tree-chevron { font-size: 11px; width: 12px; flex-shrink: 0; color: #AAA; }
.tree-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.vis-btn {
  background: none; border: none; padding: 0; cursor: pointer;
  width: 16px; height: 16px; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
  color: #0D99FF; border-radius: 3px; transition: background 0.1s;
}
.vis-btn:hover { background: rgba(13,153,255,0.1); }
.vis-btn.vis-hidden { color: #CCC; }
.vis-placeholder { width: 16px; flex-shrink: 0; }
.layer-order-btns { display: flex; flex-direction: column; gap: 1px; flex-shrink: 0; }
.layer-order-btn {
  background: none; border: none; padding: 0 2px; cursor: pointer;
  font-size: 11px; color: #CCC; line-height: 1;
}
.layer-order-btn:hover:not(:disabled) { color: #333; }
.layer-order-btn:disabled { opacity: 0.25; cursor: default; }

/* ── Design 패널 ─────────────────────────────────────────────────────────────── */
.design-meta {
  display: flex; align-items: center; gap: 8px;
  padding: 10px 14px 4px; font-size: 14px; color: #333;
}
.design-meta-name { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.design-role-badge {
  display: inline-block; margin: 0 14px 10px;
  padding: 2px 8px; border-radius: 10px;
  background: #F0F0F0; color: #888; font-size: 12px; font-weight: 600;
  text-transform: uppercase; letter-spacing: 0.04em;
}

.design-group-label {
  font-size: 12px; font-weight: 700; color: #888;
  text-transform: uppercase; letter-spacing: 0.06em;
  padding: 10px 14px 4px;
}
.design-row { display: flex; gap: 8px; padding: 0 14px 6px; }
.design-field { flex: 1; display: flex; flex-direction: column; gap: 3px; }
.design-field label {
  font-size: 12px; color: #888; font-weight: 600;
  text-transform: uppercase; letter-spacing: 0.04em;
}
.design-field input {
  border: 1px solid #E5E5E5; border-radius: 5px;
  padding: 5px 8px; font-size: 14px; color: #333; width: 100%; box-sizing: border-box;
  background: #FAFAFA;
}
.design-field input:focus { outline: none; border-color: #0D99FF; background: #FFFFFF; }

.design-empty {
  padding: 24px 14px; font-size: 14px; color: #888; text-align: center;
}

/* ── Text 오버라이드 ─────────────────────────────────────────────────────────── */
.design-text-area { padding: 0 14px 10px; }
.design-row-full { padding: 0 14px 6px; display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.design-field-label {
  font-size: 12px; color: #888; font-weight: 600;
  text-transform: uppercase; letter-spacing: 0.04em; white-space: nowrap;
}
.font-select {
  flex: 1; border: 1px solid #E5E5E5; border-radius: 5px;
  padding: 5px 8px; font-size: 13px; color: #333;
  background: #FAFAFA; cursor: pointer; min-width: 0;
}
.font-select:focus { outline: none; border-color: #0D99FF; }
.btn-group { display: flex; gap: 2px; }
.btn-fs {
  background: #F5F5F5; border: 1px solid #E5E5E5; border-radius: 4px;
  padding: 3px 7px; font-size: 13px; cursor: pointer; color: #555;
  transition: background 0.1s; min-width: 26px; text-align: center;
}
.btn-fs:hover { background: #EBEBEB; }
.btn-fs.active { background: #0D99FF; color: #fff; border-color: #0D99FF; }
.color-input {
  width: 100%; height: 30px; border: 1px solid #E5E5E5; border-radius: 5px;
  padding: 2px; cursor: pointer; background: #FAFAFA; box-sizing: border-box;
}
.text-override-input {
  width: 100%; box-sizing: border-box;
  border: 1px solid #E5E5E5; border-radius: 5px;
  padding: 7px 9px; font-size: 14px; color: #333;
  resize: vertical; font-family: inherit; line-height: 1.5;
  background: #FAFAFA;
}
.text-override-input:focus { outline: none; border-color: #0D99FF; background: #FFFFFF; }
.text-override-hint {
  margin-top: 5px; font-size: 12px; color: #0D99FF; font-weight: 600;
}
.text-badge {
  flex-shrink: 0; background: #0D99FF; color: #fff;
  font-size: 11px; font-weight: 700; border-radius: 3px;
  padding: 1px 4px; line-height: 1.4;
}

/* ── 토스트 ──────────────────────────────────────────────────────────────────── */
.toast {
  position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%);
  background: #1A1A1A; color: #fff; padding: 10px 20px;
  border-radius: 8px; font-size: 15px; z-index: 999;
  box-shadow: 0 4px 16px rgba(0,0,0,0.25); white-space: nowrap;
}
</style>
