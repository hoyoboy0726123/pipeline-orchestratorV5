'use client'
import { useEffect, useRef, useState } from 'react'
import { X, Circle, Square as StopIcon, Play, Trash2, ChevronUp, ChevronDown, Pencil, Plus, Eye, MousePointer2 } from 'lucide-react'
import { toast } from 'sonner'
import type { ComputerUseData, ComputerUseNode, ComputerUseAction } from './_helpers'

// ── vlm_check 內建模板（6 個常見場景）─────────────────────────────
const VLM_CHECK_BUILTIN_TEMPLATES: { id: string; label: string; prompt: string }[] = [
  { id: 'login_success',    label: '登入成功訊息',  prompt: '畫面是否顯示「登入成功」、「歡迎回來」之類的成功提示？沒有任何錯誤訊息？' },
  { id: 'error_message',    label: '無錯誤訊息',     prompt: '畫面是否「沒有」紅色錯誤訊息或失敗提示？所有 input 欄位都正常無紅框？' },
  { id: 'dialog_appeared',  label: '對話框已出現',  prompt: '畫面是否有對話框（彈窗）出現，且標題或按鈕清楚可見？' },
  { id: 'page_loaded',      label: '頁面載入完成',  prompt: '頁面主要內容是否完整顯示？沒有 spinner、骨架屏或載入中狀態？' },
  { id: 'button_active',    label: '按鈕變可點擊',  prompt: '目標按鈕是否變為可點擊狀態（顏色變亮、不再 disabled）？' },
  { id: 'data_loaded',      label: '資料已載入',     prompt: '畫面是否顯示了實際資料（表格有列、清單有項目），不是空白或「無資料」訊息？' },
]

const VLM_CUSTOM_TEMPLATES_KEY = 'pipeline.vlm_check.custom_templates.v1'
type CustomTemplate = { id: string; label: string; prompt: string }

function loadCustomTemplates(): CustomTemplate[] {
  if (typeof window === 'undefined') return []
  try {
    const raw = window.localStorage.getItem(VLM_CUSTOM_TEMPLATES_KEY)
    if (!raw) return []
    const arr = JSON.parse(raw)
    return Array.isArray(arr) ? arr.filter(x => x && x.label && x.prompt) : []
  } catch { return [] }
}

function saveCustomTemplates(items: CustomTemplate[]): void {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(VLM_CUSTOM_TEMPLATES_KEY, JSON.stringify(items))
  } catch (e) { console.warn('save custom templates failed:', e) }
}
import {
  startComputerUseRecording,
  stopComputerUseRecording,
  getComputerUseRecordingStatus,
  loadComputerUseRecording,
  deleteComputerUseAssets,
} from '@/lib/api'
import AnchorEditorModal from './_anchorEditorModal'
import VlmAnchorPicker from './_vlmAnchorPicker'
import { assetImageUrl } from '@/lib/api'

const NODE_COLOR = '#9333ea'

interface Props {
  node: ComputerUseNode
  pipelineName: string       // 用於推導預設 assets_dir
  onUpdate: (data: Partial<ComputerUseData>) => void
  onClose: () => void
  onDelete: () => void
}

export default function ComputerUsePanel({ node, pipelineName, onUpdate, onClose, onDelete }: Props) {
  const data = node.data
  const inputCls = 'w-full border border-gray-200 rounded-lg px-2.5 py-1.5 text-sm outline-none focus:border-purple-400 focus:ring-1 focus:ring-purple-400/20 bg-white'

  // 錄製狀態
  const [recording, setRecording] = useState(false)
  const [statusText, setStatusText] = useState('')
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // CV 比對設定摺疊（預設收折，避免佔太多空間）
  const [cvOpen, setCvOpen] = useState(false)
  // OCR 比對設定摺疊（預設收折）
  const [ocrOpen, setOcrOpen] = useState(false)
  // VLM 把關 Phase 1 摺疊（預設收折、進階功能）
  const [vlmOpen, setVlmOpen] = useState(false)

  // 預設錄製輸出目錄
  const defaultAssetsDir = data.assetsDir ||
    `ai_output/${pipelineName || 'pipeline'}/${data.name}_assets`

  // 錄製過程輪詢狀態
  useEffect(() => {
    if (!recording) {
      if (pollRef.current) clearInterval(pollRef.current)
      pollRef.current = null
      return
    }
    const poll = async () => {
      try {
        const s = await getComputerUseRecordingStatus()
        if (s.recording) {
          setStatusText(`錄製中… ${s.action_count ?? 0} 個動作`)
        } else {
          // 錄製已被 F9 或後端自行停止
          setRecording(false)
          setStatusText('')
          await handleLoadRecording()
        }
      } catch {/* ignore transient errors */}
    }
    pollRef.current = setInterval(poll, 1000)
    return () => { if (pollRef.current) clearInterval(pollRef.current) }
  }, [recording])

  const handleStart = async () => {
    if (recording) return
    try {
      const sessionId = `${data.name}-${Date.now()}`
      await startComputerUseRecording(sessionId, defaultAssetsDir)
      onUpdate({ assetsDir: defaultAssetsDir })
      setRecording(true)
      setStatusText('錄製中…（按 F9 或這個按鈕結束）')
      toast.success('🔴 開始錄製。請操作螢幕，F9 停止。')
    } catch (e) {
      toast.error((e as Error).message)
    }
  }

  const handleStop = async () => {
    try {
      await stopComputerUseRecording()
      setRecording(false)
      setStatusText('')
      await handleLoadRecording()
    } catch (e) {
      toast.error((e as Error).message)
    }
  }

  const handleLoadRecording = async () => {
    try {
      const res = await loadComputerUseRecording(defaultAssetsDir)
      onUpdate({ actions: res.actions || [], assetsDir: defaultAssetsDir })
      toast.success(`已載入 ${res.actions?.length ?? 0} 個動作`)
    } catch (e) {
      // 錄製尚未停好或目錄不存在是正常狀況
      console.warn('Load recording:', e)
    }
  }

  // 動作操作
  const moveAction = (i: number, dir: -1 | 1) => {
    const next = [...(data.actions || [])]
    const j = i + dir
    if (j < 0 || j >= next.length) return
    ;[next[i], next[j]] = [next[j], next[i]]
    onUpdate({ actions: next })
  }
  const deleteAction = (i: number) => {
    const next = [...(data.actions || [])]
    next.splice(i, 1)
    onUpdate({ actions: next })
  }
  const [editingAnchor, setEditingAnchor] = useState<number | null>(null)
  // VLM 挑錨點 file picker：用 actionIndex 表示對哪一個動作開
  const [pickingVlmAnchorsAt, setPickingVlmAnchorsAt] = useState<number | null>(null)
  const applyAnchorPatch = (i: number, patch: Partial<ComputerUseAction>) => {
    const next = [...(data.actions || [])]
    next[i] = { ...next[i], ...patch }
    onUpdate({ actions: next })
  }

  // 在指定位置插入 vlm_check 動作（template 帶 prompt 進去；無 template 時 prompt 留空，使用者自填）
  const insertVlmCheckAt = (index: number, prompt: string, label?: string) => {
    const next = [...(data.actions || [])]
    const newAction: ComputerUseAction = {
      type: 'vlm_check',
      description: label ? `視覺判斷：${label}` : '視覺判斷',
      vlm_prompt: prompt,
    } as ComputerUseAction
    next.splice(index, 0, newAction)
    onUpdate({ actions: next })
  }

  // ➕ popover 開關：用 actionIndex 表示要在哪一個 index 插入（actions.length = 在最後）
  const [insertOpenAt, setInsertOpenAt] = useState<number | null>(null)
  const [customTemplates, setCustomTemplates] = useState<CustomTemplate[]>(() => loadCustomTemplates())
  // 點外面關閉 popover
  useEffect(() => {
    if (insertOpenAt === null) return
    const onDown = (e: MouseEvent) => {
      const t = e.target as HTMLElement
      if (!t.closest('[data-vlm-insert-popover]') && !t.closest('[data-vlm-insert-trigger]')) {
        setInsertOpenAt(null)
      }
    }
    window.addEventListener('mousedown', onDown)
    return () => window.removeEventListener('mousedown', onDown)
  }, [insertOpenAt])

  const handlePickTemplate = (index: number, prompt: string, label: string) => {
    insertVlmCheckAt(index, prompt, label)
    setInsertOpenAt(null)
    toast.success(`已插入 vlm_check：${label}`)
  }

  const handleSaveCustomFromCurrent = (label: string, prompt: string) => {
    const trimmed = label.trim()
    const trimmedPrompt = prompt.trim()
    if (!trimmed || !trimmedPrompt) { toast.error('名稱和 prompt 都不能空白'); return }
    const next = [...customTemplates, { id: `custom_${Date.now()}`, label: trimmed, prompt: trimmedPrompt }]
    setCustomTemplates(next)
    saveCustomTemplates(next)
    toast.success(`已存自訂模板：${trimmed}`)
  }

  const handleDeleteCustom = (id: string) => {
    const next = customTemplates.filter(t => t.id !== id)
    setCustomTemplates(next)
    saveCustomTemplates(next)
  }

  const toggleUseCoord = (i: number) => {
    const next = [...(data.actions || [])]
    const cur = { ...next[i] }
    // 預設視為 true（座標模式）；toggle 後：true → false（圖像）、false → true（座標）
    // 三個 primary mode 獨立不互斥：use_ocr 跟 ocr_text 保留著，下次再切回 OCR 勾選
    // 文字就還在，不用重打
    const currentlyUsingCoord = cur.use_coord !== false
    cur.use_coord = !currentlyUsingCoord
    next[i] = cur
    onUpdate({ actions: next })
  }

  return (
    <div className="absolute top-0 right-0 h-full w-[420px] bg-white shadow-2xl border-l border-gray-100 flex flex-col z-30 overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-3.5 border-b" style={{ borderTopColor: NODE_COLOR, borderTopWidth: 3 }}>
        <span className="w-8 h-8 rounded-full flex items-center justify-center text-white shrink-0"
          style={{ background: NODE_COLOR }}><MousePointer2 className="w-4 h-4" strokeWidth={2.4} /></span>
        <div className="flex-1 min-w-0">
          <span className="font-semibold text-gray-800 text-sm block truncate">桌面自動化節點</span>
          <span className="text-xs text-gray-400">錄製滑鼠/鍵盤操作，以圖像錨點穩定回放</span>
        </div>
        <button onClick={onDelete} title="刪除" className="text-gray-300 hover:text-red-400 transition-colors p-1">🗑</button>
        <button onClick={onClose} className="text-gray-400 hover:text-gray-600 transition-colors"><X className="w-4 h-4" /></button>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {/* Name */}
        <div>
          <label className="text-xs font-semibold text-gray-500 uppercase tracking-wide block mb-1.5">節點名稱</label>
          <input value={data.name} onChange={e => onUpdate({ name: e.target.value })} className={`${inputCls} font-mono`} />
        </div>

        {/* 錄製按鈕 */}
        <div className="p-3 rounded-lg border border-purple-200 bg-purple-50/50 space-y-2">
          <div className="flex items-center gap-2">
            {!recording ? (
              <button onClick={handleStart}
                className="flex-1 flex items-center justify-center gap-2 px-3 py-2 bg-red-500 hover:bg-red-600 text-white rounded-lg text-sm font-medium transition-colors">
                <Circle className="w-3.5 h-3.5 fill-current" />
                開始錄製
              </button>
            ) : (
              <button onClick={handleStop}
                className="flex-1 flex items-center justify-center gap-2 px-3 py-2 bg-gray-700 hover:bg-gray-800 text-white rounded-lg text-sm font-medium transition-colors">
                <StopIcon className="w-3.5 h-3.5" />
                停止錄製
              </button>
            )}
          </div>
          {recording && (
            <p className="text-xs text-red-600 flex items-center gap-1.5">
              <span className="inline-block w-2 h-2 rounded-full bg-red-500 animate-pulse" />
              {statusText}
            </p>
          )}
          <p className="text-[11px] text-gray-500 leading-relaxed">
            按下開始後切換到要自動化的應用操作即可。點擊時會擷取周圍 240×80 的錨點 + 整個螢幕截圖（存在 <code className="font-mono text-purple-700">assets_dir</code> 中，日後可點「✏️ 編輯錨點」手動調整範圍）。按 F9 或這個按鈕可停止。
          </p>
        </div>

        {/* 動作列表 */}
        <div>
          <div className="flex items-center justify-between mb-2">
            <label className="text-xs font-semibold text-gray-500 uppercase tracking-wide">
              動作序列（{data.actions?.length ?? 0}）
            </label>
            {data.actions && data.actions.length > 0 && (
              <button onClick={async () => {
                const dir = data.assetsDir || defaultAssetsDir
                const alsoDelete = confirm(
                  '清除所有動作？\n\n按「確定」會同時刪除磁碟上的錨點圖資料夾（建議，避免殘留檔）。\n按「取消」則只清空節點動作、保留磁碟檔（通常不需要）。'
                )
                onUpdate({ actions: [] })
                if (alsoDelete && dir) {
                  try {
                    const r = await deleteComputerUseAssets(dir)
                    if (r.deleted) toast.success(`已刪除錨點資料夾：${r.path}`)
                    else toast.info(r.reason || '資料夾不存在')
                  } catch (e) {
                    toast.error((e as Error).message)
                  }
                }
              }}
                className="text-[11px] text-red-500 hover:text-red-700">清除全部</button>
            )}
          </div>
          {/* 錄製中提示：F8 插入視覺判斷標記、F9 停止 */}
          {recording && (
            <p className="text-[11px] text-purple-700 bg-purple-50 border border-purple-200 rounded px-2 py-1 mb-2">
              <Eye className="inline w-3 h-3 mr-1" />
              錄製中：按 <span className="font-mono font-bold">F8</span> 在當下位置插入視覺判斷（vlm_check）標記
              ／按 <span className="font-mono font-bold">F9</span> 停止錄製
            </p>
          )}
          {(!data.actions || data.actions.length === 0) ? (
            <>
              <p className="text-xs text-gray-400 text-center py-6 border border-dashed border-gray-200 rounded-lg">
                尚未錄製任何動作
              </p>
              {/* 沒動作時也可以手動加 vlm_check */}
              <VlmCheckInserter
                index={0}
                isOpen={insertOpenAt === 0}
                openMenu={() => setInsertOpenAt(0)}
                onPick={handlePickTemplate}
                onSaveCustom={handleSaveCustomFromCurrent}
                onDeleteCustom={handleDeleteCustom}
                customTemplates={customTemplates}
              />
            </>
          ) : (
            <div className="space-y-1.5">
              {data.actions.map((a: ComputerUseAction, i: number) => (
                <div key={i}>
                {/* 動作前的 ➕ 插入點 */}
                <VlmCheckInserter
                  index={i}
                  isOpen={insertOpenAt === i}
                  openMenu={() => setInsertOpenAt(i)}
                  onPick={handlePickTemplate}
                  onSaveCustom={handleSaveCustomFromCurrent}
                  onDeleteCustom={handleDeleteCustom}
                  customTemplates={customTemplates}
                />
                <div className="flex items-start gap-2 p-2 bg-gray-50 border border-gray-200 rounded-lg">
                  <span className="text-[10px] font-mono text-gray-400 pt-0.5">#{i + 1}</span>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-1.5 flex-wrap">
                      <span className="text-[11px] px-1.5 py-0.5 rounded font-mono bg-purple-100 text-purple-700">
                        {a.type}
                      </span>
                      {a.image && <span className="text-[11px] text-gray-500 truncate">{a.image}</span>}
                      {/* 圖像比對 toggle。OCR 啟用時不管 use_coord 是 true/false 都顯示為 dimmed
                          —— primary method 是 OCR，圖像比對是「可選 fallback」by 步驟層級 ocr_cv_fallback 控制 */}
                      {a.type === 'click_image' && (() => {
                        const usingCoord = a.use_coord !== false
                        const ocrActive = a.use_ocr === true
                        return (
                          <button onClick={() => toggleUseCoord(i)}
                            disabled={ocrActive}
                            title={ocrActive
                              ? 'OCR 啟用中；圖像比對是否作為 OCR 失敗後的 fallback，由步驟層級「OCR 比對設定」的 ocr_cv_fallback 決定'
                              : (usingCoord
                                ? '目前用絕對座標點擊（預設、快速）；按一下切到圖像比對（視窗位置會變時用）'
                                : '目前用圖像比對；按一下切回絕對座標')}
                            className={`text-[10px] px-1.5 py-0.5 rounded border transition-colors ${
                              ocrActive
                                ? 'bg-gray-50 border-gray-200 text-gray-300 cursor-not-allowed'
                                : (!usingCoord
                                  ? 'bg-amber-100 border-amber-300 text-amber-800'
                                  : 'bg-white border-gray-200 text-gray-400 hover:text-gray-700 hover:border-gray-400')
                            }`}
                          >
                            {ocrActive ? '圖像比對（OCR 接管中）' : (!usingCoord ? '🔍 圖像比對' : '圖像比對')}
                          </button>
                        )
                      })()}
                      {/* 手動編輯錨點（click_image/drag 有 full_image 時才顯示） */}
                      {(a.type === 'click_image' || a.type === 'drag') && a.full_image && (
                        <button onClick={() => setEditingAnchor(i)}
                          title="手動圈選錨點（用全螢幕截圖重新定義這個動作要比對的區域）"
                          className="text-[10px] px-1.5 py-0.5 rounded border bg-white border-purple-200 text-purple-600 hover:bg-purple-50">
                          <Pencil className="w-2.5 h-2.5 inline" /> 編輯錨點
                        </button>
                      )}
                    </div>
                    {a.description && <p className="text-xs text-gray-600 mt-0.5 truncate">{a.description}</p>}
                    {a.text && <p className="text-xs text-gray-500 mt-0.5 truncate font-mono">"{a.text}"</p>}
                    {a.keys && a.keys.length > 0 && (
                      <p className="text-xs text-gray-500 mt-0.5 font-mono">{a.keys.join('+')}</p>
                    )}
                    {typeof a.seconds === 'number' && a.seconds > 0 && (
                      <p className="text-xs text-gray-500 mt-0.5">{a.seconds}s</p>
                    )}
                    {/* vlm_check 動作：直接內嵌 vlm_prompt 編輯 */}
                    {a.type === 'vlm_check' && (
                      <div className="mt-1 space-y-1">
                        <textarea
                          value={a.vlm_prompt || ''}
                          onChange={e => applyAnchorPatch(i, { vlm_prompt: e.target.value } as Partial<ComputerUseAction>)}
                          placeholder="判斷條件（例：畫面是否出現綠色「登入成功」訊息？）"
                          rows={2}
                          className="w-full text-[11px] px-1.5 py-1 rounded border border-purple-300 bg-white outline-none focus:border-purple-500 focus:ring-1 focus:ring-purple-400/20 font-mono resize-y"
                        />
                        {!a.vlm_prompt && (
                          <p className="text-[10px] text-amber-600">⚠ vlm_prompt 為空 — 步驟執行時會直接報錯</p>
                        )}
                      </div>
                    )}
                    {/* VLM 輔助模式（click_image 專用，永遠不直接給座標）
                        - off          → 走原本 OCR / 座標 / CV 三模式
                        - description  → VLM 看圖 + vlm_prompt → 回目標文字 → OCR 找該文字 → 點中心
                        - anchor_pick  → VLM 從多張錨點變體挑一張 → 用挑出的圖走 CV 比對
                        VLM mode 開了會吃掉 OCR/座標短路（每次回放多一次 VLM 呼叫；準確度↑、速度↓） */}
                    {a.type === 'click_image' && (() => {
                      const vlmMode = (a.vlm_mode || 'off') as 'off' | 'description' | 'anchor_pick'
                      const vlmActive = vlmMode !== 'off'
                      return (
                        <div className="mt-1 space-y-1">
                          <div className="flex items-center gap-1 flex-wrap">
                            <span className="text-[10px] text-gray-500 mr-0.5">VLM 輔助：</span>
                            {([
                              { v: 'off',         label: '關',         hint: '不啟用 VLM，走原本 OCR / 座標 / CV' },
                              { v: 'description', label: '描述→OCR',   hint: 'VLM 看圖回目標文字→OCR 找文字→點中心。VLM 不給座標' },
                              { v: 'anchor_pick', label: '挑錨點',     hint: 'VLM 從多張變體錨點挑最像的→用該張走 CV 比對' },
                            ] as const).map(opt => (
                              <button
                                key={opt.v}
                                type="button"
                                onClick={() => applyAnchorPatch(i, { vlm_mode: opt.v })}
                                title={opt.hint}
                                className={`text-[10px] px-1.5 py-0.5 rounded border transition-colors ${
                                  vlmMode === opt.v
                                    ? 'bg-indigo-500 text-white border-indigo-500'
                                    : 'bg-white text-gray-500 border-gray-200 hover:border-indigo-300 hover:text-indigo-600'
                                }`}
                              >{opt.label}</button>
                            ))}
                          </div>
                          {vlmMode === 'description' && (
                            <textarea
                              value={a.vlm_prompt || ''}
                              onChange={e => applyAnchorPatch(i, { vlm_prompt: e.target.value })}
                              placeholder="描述要點什麼（例：紅色「送出」按鈕，不是藍色取消鈕）"
                              rows={2}
                              className="w-full text-[11px] px-1.5 py-1 rounded border border-indigo-300 bg-white outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-400/20 font-mono resize-y"
                            />
                          )}
                          {vlmMode === 'anchor_pick' && (
                            <>
                              <textarea
                                value={a.vlm_prompt || ''}
                                onChange={e => applyAnchorPatch(i, { vlm_prompt: e.target.value })}
                                placeholder="描述要點什麼（給 VLM 判斷哪張錨點符合當下螢幕）"
                                rows={1}
                                className="w-full text-[11px] px-1.5 py-1 rounded border border-indigo-300 bg-white outline-none focus:border-indigo-500 font-mono resize-y"
                              />
                              {/* 已選錨點 chips（縮圖 + 檔名 + 移除）*/}
                              {(a.vlm_anchors && a.vlm_anchors.length > 0) ? (
                                <div className="flex flex-wrap gap-1.5">
                                  {a.vlm_anchors.map((name, ai) => (
                                    <div key={ai}
                                      className="inline-flex items-center gap-1 px-1.5 py-1 bg-white border border-indigo-300 rounded">
                                      <img
                                        src={assetImageUrl(data.assetsDir || defaultAssetsDir, name)}
                                        alt={name}
                                        className="w-8 h-6 object-contain rounded bg-gray-100"
                                        onError={(e) => { (e.target as HTMLImageElement).style.display = 'none' }}
                                      />
                                      <span className="text-[11px] font-mono text-gray-700 max-w-[120px] truncate" title={name}>
                                        {name}
                                      </span>
                                      <button
                                        type="button"
                                        onClick={() => applyAnchorPatch(i, {
                                          vlm_anchors: (a.vlm_anchors || []).filter(x => x !== name)
                                        })}
                                        title="從候選中移除"
                                        className="text-gray-300 hover:text-red-500 p-0.5"
                                      >
                                        <X className="w-3 h-3" />
                                      </button>
                                    </div>
                                  ))}
                                </div>
                              ) : null}
                              <button
                                type="button"
                                onClick={() => setPickingVlmAnchorsAt(i)}
                                className="w-full text-[11px] px-2 py-1.5 rounded border border-dashed border-indigo-300 bg-white text-indigo-700 hover:bg-indigo-50 hover:border-indigo-500 transition-colors"
                              >
                                {(a.vlm_anchors && a.vlm_anchors.length > 0) ? '+ 從錨點資料夾再選 / 修改' : '+ 從錨點資料夾選圖（不用打檔名）'}
                              </button>
                              <p className="text-[10px] text-gray-500 leading-relaxed">
                                共 {(a.vlm_anchors || []).length} 張候選。
                                <strong className="text-indigo-700">1 張</strong> = VLM 守門員 + 強制 CV（避開盲點錄製座標的 fast-path）；
                                <strong className="text-indigo-700">2+ 張不同變體</strong> = VLM 看畫面當下挑最像的那張，再走 CV
                              </p>
                            </>
                          )}
                          {vlmActive && (
                            <p className="text-[10px] text-amber-600 leading-relaxed">
                              ⚠ VLM 模式啟用中，下方 OCR / 圖像比對切換會被忽略（VLM 永遠優先）。
                              每次回放多一次 VLM 呼叫（耗 token + ~1-3 秒）
                            </p>
                          )}
                        </div>
                      )
                    })()}
                    {/* OCR 文字比對（只對 click_image action 顯示）
                        規則：
                          - checkbox 勾選 = use_ocr=true，input enable；OCR 變為 primary 方法
                          - 取消勾選 = use_ocr=false，但 ocr_text 保留（下次再勾就不用重打）
                          - 勾選 OCR 不改動 use_coord（primary mode 互相獨立；use_coord 只控制
                            OCR 關閉時用什麼）；失敗 fallback 行為由步驟層級 ocr_cv_fallback 控制 */}
                    {a.type === 'click_image' && (() => {
                      const ocrEnabled = a.use_ocr === true
                      const inputId = `ocr-input-${i}`
                      return (
                        <div className="mt-1 flex items-center gap-1.5">
                          <label className="flex items-center gap-1 shrink-0 cursor-pointer select-none"
                            title={ocrEnabled
                              ? '已啟用 OCR 文字比對；OCR 為主要方法（取代 CV）。預設失敗直接 FAIL（不退 CV），需在下方「OCR 比對設定」手動開啟 ocr_cv_fallback 才會退回 CV'
                              : '勾選啟用 Windows OCR 文字比對。需搭配右側輸入目標文字；取消時保留文字供下次使用'}>
                            <input
                              type="checkbox"
                              checked={ocrEnabled}
                              onChange={e => {
                                if (e.target.checked) {
                                  // 啟用 OCR。不動 use_coord、不動 ocr_text（可能有舊值，直接重用）
                                  applyAnchorPatch(i, { use_ocr: true })
                                  // 若沒文字就 focus input 提示使用者填
                                  if (!a.ocr_text) {
                                    setTimeout(() => {
                                      const el = document.getElementById(inputId) as HTMLInputElement | null
                                      el?.focus()
                                    }, 50)
                                  }
                                } else {
                                  // 只翻 use_ocr，保留 ocr_text（下次勾選可直接重用）
                                  applyAnchorPatch(i, { use_ocr: false })
                                }
                              }}
                              className="w-3 h-3 rounded accent-purple-600"
                            />
                            <span className={`text-[10px] ${ocrEnabled ? 'text-purple-700 font-medium' : 'text-gray-500'}`}>
                              🔤 OCR
                            </span>
                          </label>
                          <input
                            id={inputId}
                            type="text"
                            value={a.ocr_text || ''}
                            onChange={e => applyAnchorPatch(i, { ocr_text: e.target.value })}
                            disabled={!ocrEnabled}
                            placeholder={ocrEnabled ? '要找的文字（例：關閉、下載）' : '勾選 OCR 才能填寫（會保留上次輸入）'}
                            className={`flex-1 min-w-0 text-[11px] px-1.5 py-0.5 rounded border outline-none ${
                              ocrEnabled
                                ? 'border-purple-300 bg-white focus:border-purple-500 focus:ring-1 focus:ring-purple-400/20'
                                : 'border-gray-200 bg-gray-50 text-gray-400 cursor-not-allowed'
                            }`}
                          />
                        </div>
                      )
                    })()}
                    {/* VLM 把關 Phase 1：每動作後驗證(expected outcome)─────── */}
                    {/* 只在節點層級啟用 VLM 時(strategy != off)才顯示這欄、避免干擾沒用的人 */}
                    {data.cuVlmCheckStrategy && data.cuVlmCheckStrategy !== 'off' && (
                      <div className="mt-1.5 pt-1.5 border-t border-dashed border-emerald-200">
                        <div className="flex items-center gap-1.5 mb-1">
                          <span className="text-[10px] font-semibold text-emerald-700">🛡 VLM 預期</span>
                          {data.cuVlmCheckStrategy === 'critical_only' && (
                            <label className="flex items-center gap-1 text-[10px] cursor-pointer">
                              <input type="checkbox" checked={a.verify_critical === true}
                                onChange={e => applyAnchorPatch(i, { verify_critical: e.target.checked } as Partial<ComputerUseAction>)}
                                className="w-3 h-3 accent-emerald-600" />
                              <span className="text-emerald-700">標為 critical</span>
                            </label>
                          )}
                          {a.expected && (
                            <span className="text-[9px] text-emerald-600 font-mono">(會驗)</span>
                          )}
                        </div>
                        <textarea
                          value={a.expected || ''}
                          onChange={e => applyAnchorPatch(i, { expected: e.target.value } as Partial<ComputerUseAction>)}
                          placeholder={data.cuVlmCheckStrategy === 'critical_only' && !a.verify_critical
                            ? '(critical_only 模式下、勾上方 critical 才會驗;此欄留空 = 不驗)'
                            : '動作後應看到的狀態（例：另存新檔對話框已開啟、含 File name 輸入框）'}
                          rows={1}
                          className="w-full text-[10px] px-1.5 py-1 rounded border border-emerald-200 bg-emerald-50/30 outline-none focus:border-emerald-400 focus:ring-1 focus:ring-emerald-300/20 resize-y"
                        />
                      </div>
                    )}
                  </div>
                  <div className="flex flex-col shrink-0">
                    <button onClick={() => moveAction(i, -1)} className="p-0.5 text-gray-400 hover:text-gray-700 disabled:opacity-30" disabled={i === 0}>
                      <ChevronUp className="w-3 h-3" />
                    </button>
                    <button onClick={() => moveAction(i, 1)} className="p-0.5 text-gray-400 hover:text-gray-700 disabled:opacity-30" disabled={i === (data.actions!.length - 1)}>
                      <ChevronDown className="w-3 h-3" />
                    </button>
                  </div>
                  <button onClick={() => deleteAction(i)} className="text-gray-300 hover:text-red-500 shrink-0">
                    <Trash2 className="w-3 h-3" />
                  </button>
                </div>
                </div>
              ))}
              {/* 列表最後的 ➕ 插入點 */}
              <VlmCheckInserter
                index={data.actions.length}
                isOpen={insertOpenAt === data.actions.length}
                openMenu={() => setInsertOpenAt(data.actions.length)}
                onPick={handlePickTemplate}
                onSaveCustom={handleSaveCustomFromCurrent}
                onDeleteCustom={handleDeleteCustom}
                customTemplates={customTemplates}
              />
            </div>
          )}
        </div>

        {/* Assets 目錄 */}
        <div>
          <label className="text-xs font-semibold text-gray-500 uppercase tracking-wide block mb-1.5">
            錨點圖片資料夾（相對專案根或絕對路徑）
          </label>
          <input value={data.assetsDir} onChange={e => onUpdate({ assetsDir: e.target.value })}
            placeholder={defaultAssetsDir}
            className={`${inputCls} font-mono text-xs`} />
        </div>

        {/* 選項 */}
        <div className="space-y-2">
          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input type="checkbox" checked={data.failFast}
              onChange={e => onUpdate({ failFast: e.target.checked })} className="w-4 h-4 accent-purple-600" />
            <span className="text-gray-700">遇錯立即中止（fail_fast）</span>
          </label>
        </div>

        {/* CV 比對設定（可摺疊，預設收折） */}
        <div className="rounded-xl border border-gray-200 bg-gray-50/50 overflow-hidden">
          <button
            type="button"
            onClick={() => setCvOpen(v => !v)}
            className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-gray-100/80 transition-colors"
          >
            {cvOpen ? <ChevronUp className="w-3.5 h-3.5 text-gray-400" />
                    : <ChevronDown className="w-3.5 h-3.5 text-gray-400" />}
            <span className="text-xs font-semibold text-gray-500 uppercase tracking-wide flex-1">CV 比對設定</span>
            <span className="text-[11px] text-gray-400 font-mono">
              {(data.cvThreshold ?? 0.5)}{data.cvSearchOnlyNear ? ' · 只搜附近' : ''}{(data.cvTriggerHover ?? true) ? ` · hover ${data.cvHoverWaitMs ?? 200}ms` : ''}
            </span>
          </button>
          {cvOpen && (
            <div className="px-3 pb-3 space-y-3 border-t border-gray-200">
              <div className="pt-3" />
              {/* 比對門檻 3 段 */}
              <div>
                <label className="text-xs text-gray-600 block mb-1.5">比對門檻</label>
                <div className="grid grid-cols-3 gap-1">
                  {[
                    { v: 0.50, label: '寬鬆', hint: '容錯高，DPI / 主題色 / hover 差異容忍' },
                    { v: 0.80, label: '標準', hint: '預設 sweet spot' },
                    { v: 0.90, label: '嚴格', hint: '幾乎不誤判' },
                  ].map(opt => (
                    <button
                      key={opt.v}
                      type="button"
                      onClick={() => onUpdate({ cvThreshold: opt.v })}
                      title={opt.hint}
                      className={`px-2 py-1.5 rounded-lg text-xs font-medium transition-colors border ${
                        (data.cvThreshold ?? 0.5) === opt.v
                          ? 'bg-purple-500 text-white border-purple-500'
                          : 'bg-white text-gray-600 border-gray-200 hover:border-purple-300'
                      }`}
                    >
                      {opt.label} {opt.v}
                    </button>
                  ))}
                </div>
              </div>

              {/* 只搜附近 toggle */}
              <label className="flex items-center gap-2 text-sm cursor-pointer">
                <input type="checkbox" checked={data.cvSearchOnlyNear}
                  onChange={e => onUpdate({ cvSearchOnlyNear: e.target.checked })}
                  className="w-4 h-4 accent-purple-600" />
                <span className="text-gray-700">只搜錄製座標附近</span>
              </label>
              <p className="text-[11px] text-gray-400 leading-relaxed pl-6 -mt-1">
                {data.cvSearchOnlyNear
                  ? '開啟：只在附近搜尋，不擴大到全螢幕（避免跨螢幕找錯位置）'
                  : '關閉：附近找不到 → 擴大到全螢幕 CV 搜尋'}
              </p>

              {/* CV 失敗退回座標 toggle（預設 false：失敗就停、不亂點）*/}
              <label className="flex items-center gap-2 text-sm cursor-pointer">
                <input type="checkbox" checked={data.cvCoordFallback === true}
                  onChange={e => onUpdate({ cvCoordFallback: e.target.checked })}
                  className="w-4 h-4 accent-purple-600" />
                <span className="text-gray-700">CV 失敗時退回錄製座標</span>
              </label>
              <p className="text-[11px] text-gray-400 leading-relaxed pl-6 -mt-1">
                {data.cvCoordFallback === true
                  ? '開啟：CV 完全找不到 → 退回原錄製座標硬點下去（對畫面穩定的場景多一層保險）'
                  : '關閉（預設）：CV 失敗就直接 FAIL、不亂點。選擇 CV 就代表位置可能有偏差，盲點座標反而更危險'}
              </p>

              {/* 觸發 hover toggle */}
              <label className="flex items-center gap-2 text-sm cursor-pointer">
                <input type="checkbox" checked={data.cvTriggerHover ?? true}
                  onChange={e => onUpdate({ cvTriggerHover: e.target.checked })}
                  className="w-4 h-4 accent-purple-600" />
                <span className="text-gray-700">比對前觸發 hover 效果</span>
              </label>
              <p className="text-[11px] text-gray-400 leading-relaxed pl-6 -mt-1">
                {(data.cvTriggerHover ?? true)
                  ? '開啟（建議）：先把游標移到錄製座標 + 等待，讓 Windows hover highlight 出現後再比對。'
                  : '關閉：跳過 hover 觸發、每次 click_image 會快一點。若錨點不含 hover 變色區域可關掉'}
              </p>

              {/* hover 等待 2 段 */}
              {(data.cvTriggerHover ?? true) && (
                <div>
                  <label className="text-xs text-gray-600 block mb-1.5">Hover 等待時間</label>
                  <div className="grid grid-cols-2 gap-1">
                    {[
                      { v: 200, label: '快', hint: '200ms，夠大多數 Windows UI' },
                      { v: 400, label: '保險', hint: '400ms，應付 fade-in 較慢的動畫或遠端桌面' },
                    ].map(opt => (
                      <button
                        key={opt.v}
                        type="button"
                        onClick={() => onUpdate({ cvHoverWaitMs: opt.v })}
                        title={opt.hint}
                        className={`px-2 py-1.5 rounded-lg text-xs font-medium transition-colors border ${
                          (data.cvHoverWaitMs ?? 200) === opt.v
                            ? 'bg-purple-500 text-white border-purple-500'
                            : 'bg-white text-gray-600 border-gray-200 hover:border-purple-300'
                        }`}
                      >
                        {opt.label} {opt.v}ms
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {/* 搜尋半徑 */}
              <div>
                <label className="text-xs text-gray-600 block mb-1.5">
                  附近搜尋半徑
                  <span className="text-gray-400 font-normal">
                    （實際搜尋 {(data.cvSearchRadius ?? 400) * 2}×{(data.cvSearchRadius ?? 400) * 2} px）
                  </span>
                </label>
                <input
                  type="number"
                  min={50}
                  max={2000}
                  step={50}
                  value={data.cvSearchRadius ?? 400}
                  onChange={e => {
                    const v = parseInt(e.target.value) || 400
                    onUpdate({ cvSearchRadius: Math.max(50, Math.min(2000, v)) })
                  }}
                  className={inputCls}
                />
                <p className="text-[11px] text-gray-400 mt-1">
                  視窗很少移動 → 可調小（150-200）更快更準；常跨螢幕 → 調大（600-800）
                </p>
              </div>
            </div>
          )}
        </div>

        {/* OCR 比對設定（摺疊，預設收折）*/}
        <div className="rounded-xl border border-gray-200 bg-gray-50/50 overflow-hidden">
          <button
            type="button"
            onClick={() => setOcrOpen(v => !v)}
            className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-gray-100/80 transition-colors"
          >
            {ocrOpen ? <ChevronUp className="w-3.5 h-3.5 text-gray-400" />
                    : <ChevronDown className="w-3.5 h-3.5 text-gray-400" />}
            <span className="text-xs font-semibold text-gray-500 uppercase tracking-wide flex-1">🔤 OCR 比對設定</span>
            <span className="text-[11px] text-gray-400 font-mono">
              門檻 {(data.ocrThreshold ?? 0.6).toFixed(2)}{data.ocrCvFallback ? ' · fallback→CV' : ''}
            </span>
          </button>
          {ocrOpen && (
            <div className="px-3 pb-3 space-y-3 border-t border-gray-200">
              <div className="pt-3" />
              {/* OCR 最小 conf 門檻 */}
              <div>
                <label className="text-xs text-gray-600 block mb-1.5">最小匹配信心</label>
                <div className="grid grid-cols-4 gap-1">
                  {[
                    { v: 0.6, label: '模糊', hint: '包含大小寫+去空白的模糊匹配（最寬）' },
                    { v: 0.8, label: '跨詞', hint: '允許 CJK 被 OCR 拆字後行層級拼接匹配' },
                    { v: 0.9, label: '詞包含', hint: '目標必須是某個 OCR word 的子字串' },
                    { v: 1.0, label: '精確', hint: 'OCR word 必須完全等於目標文字' },
                  ].map(opt => (
                    <button
                      key={opt.v}
                      type="button"
                      onClick={() => onUpdate({ ocrThreshold: opt.v })}
                      title={opt.hint}
                      className={`px-2 py-1.5 rounded-lg text-[11px] font-medium transition-colors border ${
                        (data.ocrThreshold ?? 0.6) === opt.v
                          ? 'bg-purple-500 text-white border-purple-500'
                          : 'bg-white text-gray-600 border-gray-200 hover:border-purple-300'
                      }`}
                    >
                      {opt.label} {opt.v.toFixed(1)}
                    </button>
                  ))}
                </div>
                <p className="text-[11px] text-gray-400 mt-1.5 leading-relaxed">
                  低於此 conf 視為沒找到。繁中被 OCR 拆字時，"跨詞 0.8" 才能從分字結果拼回原目標。
                </p>
              </div>

              {/* OCR 失敗時的 fallback 行為 */}
              <label className="flex items-center gap-2 text-sm cursor-pointer">
                <input type="checkbox" checked={data.ocrCvFallback === true}
                  onChange={e => onUpdate({ ocrCvFallback: e.target.checked })}
                  className="w-4 h-4 accent-purple-600" />
                <span className="text-gray-700">OCR 失敗時退回 CV 比對</span>
              </label>
              <p className="text-[11px] text-gray-400 leading-relaxed pl-6 -mt-1">
                {data.ocrCvFallback === true
                  ? '開啟：OCR 找不到 → 接著跑 CV 圖像比對鏈（gray→edge），CV 再失敗時是否退座標看上方 CV 設定'
                  : '關閉（預設）：OCR 失敗就直接 FAIL，不退到 CV 或座標。選擇 OCR 代表目標位置/樣式會變、CV 不適用'}
              </p>
            </div>
          )}
        </div>

        {/* 🛡 VLM 把關設定（Phase 1 — 動作後驗 expected outcome）─────────── */}
        <div className="rounded-xl border border-emerald-200 bg-emerald-50/30 overflow-hidden">
          <button
            type="button"
            onClick={() => setVlmOpen(v => !v)}
            className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-emerald-100/50 transition-colors"
          >
            {vlmOpen ? <ChevronUp className="w-3.5 h-3.5 text-emerald-600" />
                     : <ChevronDown className="w-3.5 h-3.5 text-emerald-600" />}
            <span className="text-xs font-semibold text-emerald-700 uppercase tracking-wide flex-1">🛡 VLM 把關（每動作後驗證）</span>
            <span className="text-[11px] text-emerald-600 font-mono">
              {(data.cuVlmCheckStrategy || 'off') === 'off' ? '已關閉' :
               (data.cuVlmCheckStrategy === 'after_each' ? '每步都驗' : '只驗 critical')}
              {data.cuOnMismatch && data.cuOnMismatch !== 'stop_notify' ? ` · ${data.cuOnMismatch === 'retry_once' ? '重試' : '略過'}` : ''}
            </span>
          </button>
          {vlmOpen && (
            <div className="px-3 pb-3 space-y-3 border-t border-emerald-200">
              <div className="pt-3" />
              <p className="text-[11px] text-emerald-700 leading-relaxed">
                錄製座標準確主路徑、VLM 額外當「驗證者」。每動作後比對「動作前 / 動作後截圖」+ 你寫的 expected 描述、
                偏離立刻停 + push TG 通知。99% 失敗模式從「整套悶著錯」變「立刻發現+人介入」。
                <br />
                <span className="text-[10px] text-emerald-600">每次驗證 ~$0.005-0.015、用設定頁的主模型(必須支援 vision)。</span>
              </p>

              {/* strategy 3 選 */}
              <div>
                <label className="text-xs text-gray-600 block mb-1.5">驗證策略</label>
                <div className="grid grid-cols-3 gap-1">
                  {[
                    { v: 'off', label: '關閉', hint: '完全不驗(預設、向後相容)' },
                    { v: 'after_each', label: '每步都驗', hint: '所有有 expected 的動作都送 VLM' },
                    { v: 'critical_only', label: '只驗 critical', hint: '只驗有勾「critical」的動作' },
                  ].map(opt => (
                    <button
                      key={opt.v}
                      type="button"
                      onClick={() => onUpdate({ cuVlmCheckStrategy: opt.v as 'off' | 'after_each' | 'critical_only' })}
                      title={opt.hint}
                      className={`px-2 py-1.5 rounded-lg text-xs font-medium transition-colors border ${
                        (data.cuVlmCheckStrategy || 'off') === opt.v
                          ? 'bg-emerald-500 text-white border-emerald-500'
                          : 'bg-white text-gray-600 border-gray-200 hover:border-emerald-300'
                      }`}
                    >
                      {opt.label}
                    </button>
                  ))}
                </div>
              </div>

              {/* on_mismatch 3 選 */}
              {(data.cuVlmCheckStrategy || 'off') !== 'off' && (
                <>
                  <div>
                    <label className="text-xs text-gray-600 block mb-1.5">偏離時行為</label>
                    <div className="grid grid-cols-3 gap-1">
                      {[
                        { v: 'stop_notify', label: '停 + 通知', hint: '立即停 pipeline、push TG 截圖、等人介入(預設、最安全)' },
                        { v: 'retry_once', label: '重試', hint: '重執行同動作 N 次、仍失敗才停' },
                        { v: 'skip_and_continue', label: '略過繼續', hint: '警告但繼續、用於非關鍵步' },
                      ].map(opt => (
                        <button
                          key={opt.v}
                          type="button"
                          onClick={() => onUpdate({ cuOnMismatch: opt.v as 'stop_notify' | 'retry_once' | 'skip_and_continue' })}
                          title={opt.hint}
                          className={`px-2 py-1.5 rounded-lg text-xs font-medium transition-colors border ${
                            (data.cuOnMismatch || 'stop_notify') === opt.v
                              ? 'bg-emerald-500 text-white border-emerald-500'
                              : 'bg-white text-gray-600 border-gray-200 hover:border-emerald-300'
                          }`}
                        >
                          {opt.label}
                        </button>
                      ))}
                    </div>
                  </div>

                  {/* retry_once 模式才顯示 max_retries */}
                  {data.cuOnMismatch === 'retry_once' && (
                    <div>
                      <label className="text-xs text-gray-600 block mb-1.5">重試上限</label>
                      <input type="number" min={1} max={5}
                        value={data.cuVlmMaxRetries || 1}
                        onChange={e => onUpdate({ cuVlmMaxRetries: Math.max(1, Math.min(5, parseInt(e.target.value) || 1)) })}
                        className={`${inputCls} w-24`} />
                      <p className="text-[10px] text-emerald-600 mt-1">每動作最多重試 N 次、仍失敗才走 stop_notify</p>
                    </div>
                  )}

                  <p className="text-[11px] text-emerald-700 leading-relaxed bg-emerald-100/40 px-2 py-1.5 rounded">
                    💡 在每個動作上面填「expected 預期狀態」(例「另存對話框已開」)、VLM 驗證才有用。
                    {data.cuVlmCheckStrategy === 'critical_only' && ' 此模式下還要勾上 critical 那格。'}
                  </p>
                </>
              )}
            </div>
          )}
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="text-xs font-semibold text-gray-500 uppercase tracking-wide block mb-1.5">超時（秒）</label>
            <input type="number" value={data.timeout}
              onChange={e => onUpdate({ timeout: parseInt(e.target.value) || 300 })} className={inputCls} />
          </div>
          <div>
            <label className="text-xs font-semibold text-gray-500 uppercase tracking-wide block mb-1.5">重試次數</label>
            <input type="number" value={data.retry}
              onChange={e => onUpdate({ retry: parseInt(e.target.value) || 0 })} className={inputCls} />
            <p className="text-[10px] text-gray-500 mt-1 leading-relaxed">
              預設 0：桌面自動化重試會從動作 #1 重頭跑一遍，可能重複點擊、造成副作用（例如重複送單）。建議 0；確定所有動作 idempotent 才調大
            </p>
          </div>
        </div>

        <div className="p-2.5 bg-yellow-50 border border-yellow-200 rounded-lg text-[11px] text-yellow-800 leading-relaxed">
          <strong>⚠ 安全提醒</strong>：執行時滑鼠會實際操作系統。失控可把滑鼠甩到螢幕左上角 (0,0) 立即中止。動作數上限 500。
        </div>
      </div>

      {/* 手動圈選錨點 Modal */}
      {editingAnchor !== null && data.actions && data.actions[editingAnchor] && (
        <AnchorEditorModal
          action={data.actions[editingAnchor]}
          actionIndex={editingAnchor}
          assetsDir={data.assetsDir || defaultAssetsDir}
          defaultSearchRadius={data.cvSearchRadius || 400}
          onApply={(patch) => applyAnchorPatch(editingAnchor, patch)}
          onClose={() => setEditingAnchor(null)}
        />
      )}

      {/* VLM 挑錨點 file picker Modal */}
      {pickingVlmAnchorsAt !== null && data.actions && data.actions[pickingVlmAnchorsAt] && (
        <VlmAnchorPicker
          assetsDir={data.assetsDir || defaultAssetsDir}
          initialSelected={data.actions[pickingVlmAnchorsAt].vlm_anchors || []}
          onApply={(anchors) => applyAnchorPatch(pickingVlmAnchorsAt, { vlm_anchors: anchors } as Partial<ComputerUseAction>)}
          onClose={() => setPickingVlmAnchorsAt(null)}
        />
      )}
    </div>
  )
}


// ── VLM check 插入點：➕ 按鈕 + 模板選單 ─────────────────────────────
// 6 個內建模板 + 自訂模板（localStorage）+ 「自訂…」可即時新增
interface VlmCheckInserterProps {
  index: number
  isOpen: boolean
  openMenu: () => void
  onPick: (index: number, prompt: string, label: string) => void
  onSaveCustom: (label: string, prompt: string) => void
  onDeleteCustom: (id: string) => void
  customTemplates: CustomTemplate[]
}

function VlmCheckInserter({
  index, isOpen, openMenu, onPick, onSaveCustom, onDeleteCustom, customTemplates
}: VlmCheckInserterProps) {
  const [showCustomForm, setShowCustomForm] = useState(false)
  const [customLabel, setCustomLabel] = useState('')
  const [customPrompt, setCustomPrompt] = useState('')

  const submitCustom = () => {
    onSaveCustom(customLabel, customPrompt)
    if (customLabel.trim() && customPrompt.trim()) {
      // 立即用該模板插入
      onPick(index, customPrompt.trim(), customLabel.trim())
      setShowCustomForm(false)
      setCustomLabel('')
      setCustomPrompt('')
    }
  }

  if (!isOpen) {
    return (
      <div className="flex justify-center -my-0.5">
        <button
          data-vlm-insert-trigger
          type="button"
          onClick={openMenu}
          title="在此位置插入 vlm_check 視覺判斷"
          className="opacity-30 hover:opacity-100 transition-opacity flex items-center gap-0.5 px-2 py-0.5 rounded-full text-[10px] text-purple-600 hover:bg-purple-100 hover:text-purple-700 border border-transparent hover:border-purple-300"
        >
          <Plus className="w-2.5 h-2.5" /> vlm_check
        </button>
      </div>
    )
  }

  return (
    <div data-vlm-insert-popover
      className="border border-purple-300 bg-white rounded-lg shadow-lg p-2 my-1 space-y-1.5">
      <div className="flex items-center justify-between mb-0.5">
        <span className="text-[11px] font-semibold text-purple-700 flex items-center gap-1">
          <Eye className="w-3 h-3" /> 插入視覺判斷（vlm_check）
        </span>
        <span className="text-[10px] text-gray-400">在 #{index + 1} 之前</span>
      </div>
      {/* 內建 6 個模板 */}
      <div className="space-y-0.5">
        {VLM_CHECK_BUILTIN_TEMPLATES.map(t => (
          <button
            key={t.id}
            type="button"
            onClick={() => onPick(index, t.prompt, t.label)}
            className="w-full text-left px-2 py-1 rounded hover:bg-purple-50 border border-transparent hover:border-purple-200"
          >
            <div className="text-[11px] font-medium text-gray-700">{t.label}</div>
            <div className="text-[10px] text-gray-500 truncate">{t.prompt}</div>
          </button>
        ))}
      </div>
      {customTemplates.length > 0 && (
        <>
          <div className="text-[10px] text-gray-400 uppercase tracking-wide pt-1 border-t border-gray-100">自訂模板</div>
          <div className="space-y-0.5">
            {customTemplates.map(t => (
              <div key={t.id} className="flex items-center gap-1">
                <button
                  type="button"
                  onClick={() => onPick(index, t.prompt, t.label)}
                  className="flex-1 text-left px-2 py-1 rounded hover:bg-purple-50 border border-transparent hover:border-purple-200"
                >
                  <div className="text-[11px] font-medium text-gray-700">{t.label}</div>
                  <div className="text-[10px] text-gray-500 truncate">{t.prompt}</div>
                </button>
                <button
                  type="button"
                  onClick={() => onDeleteCustom(t.id)}
                  title="刪除這個自訂模板"
                  className="text-gray-300 hover:text-red-500 px-1"
                >
                  <Trash2 className="w-3 h-3" />
                </button>
              </div>
            ))}
          </div>
        </>
      )}
      {!showCustomForm ? (
        <div className="flex items-center gap-2 pt-1 border-t border-gray-100">
          <button
            type="button"
            onClick={() => setShowCustomForm(true)}
            className="flex-1 text-[10px] text-purple-600 hover:text-purple-800 px-2 py-1 rounded border border-dashed border-purple-300 hover:bg-purple-50"
          >
            ➕ 新增自訂模板
          </button>
          <button
            type="button"
            onClick={() => onPick(index, '', '空白模板')}
            title="插入空白 vlm_check（在動作面板內手填判斷條件）"
            className="text-[10px] text-gray-500 hover:text-gray-800 px-2 py-1 rounded border border-gray-200 hover:bg-gray-50"
          >
            空白
          </button>
        </div>
      ) : (
        <div className="space-y-1 pt-1 border-t border-gray-100">
          <input
            value={customLabel}
            onChange={e => setCustomLabel(e.target.value)}
            placeholder="模板名稱（例：表單送出成功）"
            className="w-full text-[11px] px-1.5 py-1 rounded border border-gray-300 outline-none focus:border-purple-500"
          />
          <textarea
            value={customPrompt}
            onChange={e => setCustomPrompt(e.target.value)}
            placeholder="判斷條件（給 VLM 看的提示）"
            rows={2}
            className="w-full text-[11px] px-1.5 py-1 rounded border border-gray-300 outline-none focus:border-purple-500 resize-y font-mono"
          />
          <div className="flex gap-1">
            <button type="button" onClick={submitCustom}
              className="flex-1 text-[10px] bg-purple-500 text-white px-2 py-1 rounded hover:bg-purple-600">儲存並插入</button>
            <button type="button" onClick={() => { setShowCustomForm(false); setCustomLabel(''); setCustomPrompt('') }}
              className="text-[10px] text-gray-500 hover:text-gray-800 px-2 py-1 rounded border border-gray-200">取消</button>
          </div>
        </div>
      )}
    </div>
  )
}
