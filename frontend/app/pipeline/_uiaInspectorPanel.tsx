'use client'
/**
 * UIA inspector panel — 抓當下視窗 element tree、讓使用者選 element + 動作、
 * 自動加進 cu_actions[]。
 *
 * 設計:
 *  - 上方:視窗 title pattern input + foreground 模式 toggle + 「🔍 抓取元素」按鈕
 *  - 中間:可摺的 tree(每個 element 顯示 type / name / auto_id / enabled)
 *    - 滑過/點擊 element → 右側顯示可用 action(uia_click / uia_send_keys 等)
 *  - 點 action button → 把組好的 ComputerUseAction 加進 actions[](onAddAction)
 *  - 不錄製、不需 assets/、不會碰桌面動作
 */
import { useState, useCallback, useRef, useEffect } from 'react'
import { ChevronDown, ChevronRight, MousePointerClick, Type, Eye, Hash, ListChecks, Clock, CheckCircle, RefreshCcw, Search, AppWindow, Crosshair, X } from 'lucide-react'
import { toast } from 'sonner'
import {
  uiaInspect, uiaHighlight, uiaListWindows,
  uiaPickerStart, uiaPickerPoll, uiaPickerConsume, uiaPickerStop, uiaPickerConfirm,
  type UiaElement, type UiaInspectResult, type UiaWindowInfo
} from '@/lib/api'
import type { ComputerUseAction } from './_helpers'

interface Props {
  uiaWindow: string
  onUpdateWindow: (w: string) => void
  onAddAction: (action: ComputerUseAction) => void
}

interface PickerState {
  element: UiaElement
  path: string[]   // 從 root 來的描述路徑(顯示用、不送 backend)
}

export default function UiaInspectorPanel({ uiaWindow, onUpdateWindow, onAddAction }: Props) {
  const [tree, setTree] = useState<UiaInspectResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [expanded, setExpanded] = useState<Set<string>>(new Set([''])) // root 路徑
  const [picker, setPicker] = useState<PickerState | null>(null)
  // hover highlight 節流(避免快速滑過 spam backend)
  const hoverTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const lastHoverPathRef = useRef<string>('')
  // 「列出視窗」popup
  const [windowsList, setWindowsList] = useState<UiaWindowInfo[]>([])
  const [showWindows, setShowWindows] = useState(false)
  const [loadingWindows, setLoadingWindows] = useState(false)
  // Live Picker(滑鼠 hover 桌面選元素)
  const [pickerActive, setPickerActive] = useState(false)
  const [hoveredEl, setHoveredEl] = useState<UiaElement | null>(null)
  const pickerPollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const pickerActiveRef = useRef(false)   // unmount cleanup 用、避免 useEffect deps=[pickerActive]
                                          // 在 state 改變時誤觸發 cleanup 把 setInterval 砍掉

  const inspect = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const r = await uiaInspect({ window: uiaWindow, max_depth: 6, max_children_per_node: 80 })
      setTree(r)
      setExpanded(new Set(['']))
      setPicker(null)
      toast.success(`已讀取 ${r.window.name || '(foreground)'} 的元素樹`)
    } catch (e) {
      setError((e as Error).message)
      setTree(null)
    } finally {
      setLoading(false)
    }
  }, [uiaWindow])

  const loadWindows = useCallback(async () => {
    setLoadingWindows(true)
    try {
      const r = await uiaListWindows()
      setWindowsList(r.windows || [])
      setShowWindows(true)
    } catch (e) {
      toast.error((e as Error).message)
    } finally {
      setLoadingWindows(false)
    }
  }, [])

  // Live Picker:啟動 + 輪詢 + 確認後 reset、停止 picker
  const startPicker = useCallback(async () => {
    if (pickerActiveRef.current) return
    try {
      await uiaPickerStart()
      pickerActiveRef.current = true
      setPickerActive(true)
      setHoveredEl(null)
      toast.success('🎯 移動滑鼠到目標、按 F8 確認、F9 取消', { duration: 4000 })
      // 輪詢:每 250ms 拿狀態
      pickerPollRef.current = setInterval(async () => {
        try {
          const s = await uiaPickerPoll()
          setHoveredEl(s.hovered)
          if (s.confirmed) {
            const consumed = await uiaPickerConsume()
            if (consumed.element) {
              setPicker({ element: consumed.element, path: ['picked'] })
              toast.success(`已選 ${consumed.element.type}${consumed.element.name ? ': ' + consumed.element.name.slice(0, 40) : ''}`)
            }
            // 確認後 picker 已自停;清 polling
            if (pickerPollRef.current) { clearInterval(pickerPollRef.current); pickerPollRef.current = null }
            pickerActiveRef.current = false
            setPickerActive(false)
            setHoveredEl(null)
          } else if (!s.running) {
            // F9 取消或 picker 自停
            if (pickerPollRef.current) { clearInterval(pickerPollRef.current); pickerPollRef.current = null }
            pickerActiveRef.current = false
            setPickerActive(false)
            setHoveredEl(null)
            if (s.error) toast.error(s.error)
          }
        } catch {
          // 失敗不打擾、下次再 poll
        }
      }, 250)
    } catch (e) {
      toast.error((e as Error).message)
      pickerActiveRef.current = false
      setPickerActive(false)
    }
  }, [])

  const stopPicker = useCallback(async () => {
    if (!pickerActiveRef.current) return
    try { await uiaPickerStop() } catch {}
    if (pickerPollRef.current) { clearInterval(pickerPollRef.current); pickerPollRef.current = null }
    pickerActiveRef.current = false
    setPickerActive(false)
    setHoveredEl(null)
  }, [])

  // unmount cleanup(deps 空、只在 component 真的 unmount 時跑、避免每次 pickerActive 變動誤觸發)
  useEffect(() => () => {
    if (pickerPollRef.current) clearInterval(pickerPollRef.current)
    if (pickerActiveRef.current) uiaPickerStop().catch(() => {})
  }, [])

  const togglePath = (path: string) => {
    setExpanded(s => {
      const next = new Set(s)
      next.has(path) ? next.delete(path) : next.add(path)
      return next
    })
  }

  // 把 element 包成 ComputerUseAction、推給 panel
  const addAction = (type: ComputerUseAction['type'], extra: Partial<ComputerUseAction> = {}) => {
    if (!picker) return
    const el = picker.element
    const control = {
      ...(el.type ? { type: el.type } : {}),
      ...(el.name ? { name: el.name } : {}),
      ...(el.auto_id ? { auto_id: el.auto_id } : {}),
    }
    const action: ComputerUseAction = {
      type,
      control,
      description: `${type} ${el.type}${el.name ? `:${el.name}` : ''}`,
      ...extra,
    }
    onAddAction(action)
    toast.success(`已加 ${type}(${el.name || el.type})`)
  }

  // hover highlight 節流 + 觸發
  const handleHover = useCallback((path: string, el: UiaElement) => {
    if (hoverTimerRef.current) clearTimeout(hoverTimerRef.current)
    if (path === lastHoverPathRef.current) return
    hoverTimerRef.current = setTimeout(() => {
      lastHoverPathRef.current = path
      const [x, y, w, h] = el.rect || [0, 0, 0, 0]
      if (w > 0 && h > 0) {
        uiaHighlight({ x, y, width: w, height: h, ttl_ms: 1500 })
      }
    }, 80)
  }, [])
  const handleHoverEnd = useCallback(() => {
    if (hoverTimerRef.current) clearTimeout(hoverTimerRef.current)
  }, [])

  const renderNode = (el: UiaElement, path: string, depth: number = 0) => {
    const hasKids = el.children && el.children.length > 0
    const isOpen = expanded.has(path)
    const dimmed = !el.enabled || el.offscreen
    const isSelected = picker && picker.path.join('/') === path
    return (
      <div key={path}>
        <div
          className={`flex items-start gap-1 py-0.5 pr-2 hover:bg-purple-50 cursor-pointer ${
            isSelected ? 'bg-purple-100 border-l-2 border-purple-500' : ''
          } ${dimmed ? 'opacity-50' : ''}`}
          style={{ paddingLeft: 4 + depth * 14 }}
          onMouseEnter={() => handleHover(path, el)}
          onMouseLeave={handleHoverEnd}
          onClick={() => {
            setPicker({ element: el, path: path.split('/') })
            // 點擊用較長 ttl(3 秒)強調
            const [x, y, w, h] = el.rect || [0, 0, 0, 0]
            if (w > 0 && h > 0) uiaHighlight({ x, y, width: w, height: h, ttl_ms: 3000 })
          }}
        >
          {hasKids ? (
            <button onClick={(e) => { e.stopPropagation(); togglePath(path) }} className="p-0.5 text-gray-400">
              {isOpen ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
            </button>
          ) : (
            <span className="w-4 inline-block" />
          )}
          <div className="flex-1 min-w-0 text-[11px] leading-tight">
            <span className="font-mono text-purple-700">{el.type || '?'}</span>
            {el.name && <span className="text-gray-700 truncate">{' · '}{el.name.length > 60 ? el.name.slice(0, 60) + '…' : el.name}</span>}
            {el.auto_id && <span className="text-gray-400 font-mono ml-1">[{el.auto_id}]</span>}
            {!el.enabled && <span className="text-amber-600 ml-1">(disabled)</span>}
            {el.offscreen && <span className="text-gray-400 ml-1">(offscreen)</span>}
          </div>
        </div>
        {hasKids && isOpen && el.children.map((child, ci) =>
          renderNode(child, path ? `${path}/${ci}` : String(ci), depth + 1)
        )}
      </div>
    )
  }

  return (
    <div className="rounded-xl border border-purple-200 bg-purple-50/30 p-3 space-y-3">
      {/* 🎯 Live Picker — 推薦主要路徑、滑鼠移到桌面選元素 */}
      <div className={`rounded-lg border-2 ${pickerActive ? 'border-emerald-400 bg-emerald-50' : 'border-emerald-200 bg-white'} p-3`}>
        {!pickerActive ? (
          <div>
            <button
              onClick={startPicker}
              className="w-full px-3 py-2.5 bg-emerald-600 text-white rounded-lg text-sm font-medium hover:bg-emerald-700 flex items-center justify-center gap-2"
            >
              <Crosshair className="w-4 h-4" />
              🎯 滑鼠定位元素(推薦、滑到哪選哪)
            </button>
            <p className="text-[11px] text-emerald-700 mt-1.5 leading-relaxed">
              啟動後:**滑鼠移到桌面任意 UI 元素**、紅框會跟隨。
              按 <span className="font-mono font-bold">F8</span> 確認當前 hover 元素、
              按 <span className="font-mono font-bold">F9</span> 取消。
            </p>
          </div>
        ) : (
          <div className="space-y-2">
            <div className="flex items-center gap-2">
              <span className="inline-block w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
              <span className="font-semibold text-emerald-700 text-sm">🎯 定位中…</span>
              <span className="text-[10px] text-gray-500 ml-auto">F8/F9 或下方按鈕</span>
            </div>
            {hoveredEl ? (
              <div className="bg-white border border-emerald-200 rounded p-2 text-[11px]">
                <div className="flex items-center gap-1 mb-0.5">
                  <span className="font-mono text-purple-700 font-semibold">{hoveredEl.type || '?'}</span>
                  {!hoveredEl.enabled && <span className="text-amber-600 ml-1">(disabled)</span>}
                </div>
                {hoveredEl.name && <div className="text-gray-700 truncate"><strong>name:</strong> {hoveredEl.name.slice(0, 80)}</div>}
                {hoveredEl.auto_id && <div className="text-gray-500 font-mono truncate"><strong>auto_id:</strong> {hoveredEl.auto_id}</div>}
                {hoveredEl.rect && hoveredEl.rect.length === 4 && (
                  <div className="text-gray-400 font-mono">
                    rect: {hoveredEl.rect[0]},{hoveredEl.rect[1]} {hoveredEl.rect[2]}×{hoveredEl.rect[3]}
                  </div>
                )}
              </div>
            ) : (
              <div className="text-[11px] text-gray-500 italic px-1">移動滑鼠到桌面元素…</div>
            )}
            {/* 按鈕保險(F8/F9 失靈時用)*/}
            <div className="flex gap-2">
              <button
                onClick={async () => {
                  try {
                    const r = await uiaPickerConfirm()
                    if (r.ok && r.element) {
                      // picker backend 已停、frontend 也要 sync
                      if (pickerPollRef.current) { clearInterval(pickerPollRef.current); pickerPollRef.current = null }
                      setPickerActive(false)
                      setHoveredEl(null)
                      setPicker({ element: r.element, path: ['picked'] })
                      toast.success(`已選 ${r.element.type}${r.element.name ? ': ' + r.element.name.slice(0, 40) : ''}`)
                    } else {
                      toast.error(r.error || '目前沒 hover 任何元素')
                    }
                  } catch (e) { toast.error((e as Error).message) }
                }}
                disabled={!hoveredEl}
                className="flex-1 px-2 py-1.5 bg-emerald-600 text-white rounded text-xs font-medium hover:bg-emerald-700 disabled:bg-gray-300 disabled:cursor-not-allowed flex items-center justify-center gap-1"
              >
                <CheckCircle className="w-3.5 h-3.5" /> 確認(F8)
              </button>
              <button
                onClick={stopPicker}
                className="flex-1 px-2 py-1.5 bg-white border border-gray-300 text-gray-700 rounded text-xs font-medium hover:bg-gray-50 flex items-center justify-center gap-1"
              >
                <X className="w-3.5 h-3.5" /> 取消(F9)
              </button>
            </div>
          </div>
        )}
      </div>

      <div className="text-[10px] text-gray-400 text-center">— 或用以下進階方式 —</div>

      {/* 視窗 pattern 輸入 + 抓取按鈕 */}
      <div>
        <label className="text-xs font-semibold text-purple-700 uppercase tracking-wide block mb-1.5">
          🪟 目標視窗(支援 wildcard *、空 = 當前 foreground)
        </label>
        <div className="flex items-center gap-2">
          <input
            value={uiaWindow}
            onChange={e => onUpdateWindow(e.target.value)}
            placeholder="例:*檔案總管* 或 留空用 foreground"
            className="flex-1 border border-gray-200 rounded-lg px-2.5 py-1.5 text-sm font-mono outline-none focus:border-purple-400 focus:ring-1 focus:ring-purple-400/20 bg-white"
          />
          <button
            onClick={loadWindows}
            disabled={loadingWindows}
            title="列出當下所有 top-level 視窗、選一個自動填 pattern"
            className="px-2.5 py-1.5 bg-white border border-purple-300 text-purple-700 rounded-lg text-sm font-medium hover:bg-purple-50 disabled:bg-gray-100 flex items-center gap-1"
          >
            {loadingWindows ? <RefreshCcw className="w-3.5 h-3.5 animate-spin" /> : <AppWindow className="w-3.5 h-3.5" />}
            列視窗
          </button>
          <button
            onClick={inspect}
            disabled={loading}
            className="px-3 py-1.5 bg-purple-600 text-white rounded-lg text-sm font-medium hover:bg-purple-700 disabled:bg-gray-300 flex items-center gap-1"
          >
            {loading ? <RefreshCcw className="w-3.5 h-3.5 animate-spin" /> : <Search className="w-3.5 h-3.5" />}
            {loading ? '讀取中…' : '抓取元素'}
          </button>
        </div>
        <p className="text-[10px] text-gray-500 mt-1 leading-relaxed">
          ⚠ 留空會抓「當前 foreground」、會抓到瀏覽器自身;**用「列視窗」選目標** 才能抓到背景的應用(檔案總管 / 公司系統等不必拉到前景)。
        </p>

        {/* 視窗 list popup */}
        {showWindows && (
          <div className="mt-2 rounded-lg border border-purple-300 bg-white max-h-64 overflow-y-auto shadow-md">
            <div className="sticky top-0 flex items-center justify-between bg-purple-50 px-2 py-1 border-b border-purple-200 text-[11px] text-purple-700">
              <span>共 {windowsList.length} 個 top-level 視窗、點一個自動填 pattern</span>
              <button onClick={() => setShowWindows(false)} className="text-purple-500 hover:text-purple-700">關閉</button>
            </div>
            {windowsList.length === 0 && <div className="px-3 py-4 text-xs text-gray-400 text-center">沒有可顯示的視窗</div>}
            {windowsList.map((w, i) => (
              <button
                key={i}
                onClick={() => {
                  // 把 name 包成 wildcard pattern(取頭尾去 wildcard、避免特殊字元)
                  // 用「name 的前 30 字 + *」做寬鬆比對
                  const trimmed = w.name.length > 30 ? w.name.slice(0, 30) + '*' : w.name
                  onUpdateWindow(trimmed)
                  setShowWindows(false)
                  toast.success(`已套用 pattern:${trimmed}`)
                }}
                onMouseEnter={() => {
                  if (w.rect && w.rect.length === 4 && w.rect[2] > 0) {
                    uiaHighlight({ x: w.rect[0], y: w.rect[1], width: w.rect[2], height: w.rect[3], ttl_ms: 1500 })
                  }
                }}
                className="w-full text-left px-3 py-1.5 hover:bg-purple-50 border-b border-gray-100 last:border-b-0 flex items-start gap-2 text-xs"
              >
                <AppWindow className="w-3 h-3 text-purple-500 mt-0.5 shrink-0" />
                <div className="flex-1 min-w-0">
                  <div className="text-gray-800 truncate" title={w.name}>{w.name}</div>
                  <div className="text-[10px] text-gray-400 font-mono truncate">
                    [{w.class}] {w.rect[0]},{w.rect[1]} {w.rect[2]}×{w.rect[3]}
                    {w.is_offscreen && <span className="text-amber-500 ml-1">(offscreen)</span>}
                  </div>
                </div>
              </button>
            ))}
          </div>
        )}
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-2 text-xs text-red-700">
          ⚠ {error}
        </div>
      )}

      {tree && tree.ok && (
        <div className="space-y-2">
          <div className="text-[11px] text-purple-700 bg-purple-100/50 rounded px-2 py-1">
            <strong>{tree.window.name || '(foreground)'}</strong>
            {tree.window.class && <span className="ml-2 text-gray-500">[{tree.window.class}]</span>}
          </div>

          {/* element tree */}
          <div className="border border-gray-200 rounded-lg bg-white overflow-y-auto max-h-[40vh]">
            {renderNode(tree.tree, '', 0)}
          </div>

          {/* 已選 element + 動作選單 */}
          {picker ? (
            <UiaActionPicker element={picker.element} onAdd={addAction} />
          ) : (
            <p className="text-[11px] text-gray-500 italic px-1">點上方 element 後、下方會顯示可加的動作。</p>
          )}
        </div>
      )}

      {!tree && !loading && !error && (
        <div className="text-center text-xs text-gray-500 py-6">
          按「抓取元素」開始;不會碰桌面、純讀 UIA tree。
        </div>
      )}
    </div>
  )
}

/** 已選 element 的動作選擇器:列各 uia_* type 對應的按鈕 */
function UiaActionPicker({
  element,
  onAdd,
}: {
  element: UiaElement
  onAdd: (type: ComputerUseAction['type'], extra?: Partial<ComputerUseAction>) => void
}) {
  const [textInput, setTextInput] = useState('')
  const [saveAsInput, setSaveAsInput] = useState('')
  const [rowInput, setRowInput] = useState<string>('')
  const [colInput, setColInput] = useState<string>('')

  const isGrid = ['DataGrid', 'List', 'Tree', 'Table'].some(s => element.type.includes(s))
  const isEditable = ['Edit', 'Document', 'Combo'].some(s => element.type.includes(s))

  return (
    <div className="border border-purple-200 rounded-lg bg-purple-50/40 p-2 space-y-2">
      <div className="text-[11px] text-purple-700">
        <strong>已選:</strong> <span className="font-mono">{element.type}</span>
        {element.name && <span> · {element.name}</span>}
      </div>

      <div className="grid grid-cols-2 gap-1">
        <ActionBtn icon={MousePointerClick} label="點擊" onClick={() => onAdd('uia_click')} />
        <ActionBtn icon={CheckCircle} label="斷言存在" onClick={() => onAdd('uia_assert_state', { check: 'exists' })} />
        <ActionBtn icon={Clock} label="等 enabled" onClick={() => onAdd('uia_wait_enabled')} />
        <ActionBtn icon={Eye} label="讀文字" onClick={() => {
          if (!saveAsInput.trim()) { toast.error('請先填變數名(save_as)'); return }
          onAdd('uia_get_text', { save_as: saveAsInput.trim() })
        }} />
      </div>

      {isEditable && (
        <div className="flex gap-1">
          <input
            value={textInput}
            onChange={e => setTextInput(e.target.value)}
            placeholder="輸入文字(可含 {{變數}})"
            className="flex-1 border border-gray-200 rounded px-2 py-1 text-xs font-mono"
          />
          <button
            onClick={() => {
              if (!textInput.trim()) { toast.error('請填文字'); return }
              onAdd('uia_send_keys', { text: textInput })
              setTextInput('')
            }}
            className="px-2 py-1 bg-purple-600 text-white rounded text-xs flex items-center gap-1 hover:bg-purple-700"
          >
            <Type className="w-3 h-3" /> 送文字
          </button>
        </div>
      )}

      {isGrid && (
        <div className="space-y-1 border-t border-purple-200/50 pt-2">
          <div className="text-[10px] text-purple-700 font-semibold">表格動作</div>
          <div className="flex gap-1">
            <input
              value={saveAsInput}
              onChange={e => setSaveAsInput(e.target.value)}
              placeholder="變數名(例 row_count)"
              className="flex-1 border border-gray-200 rounded px-2 py-1 text-xs font-mono"
            />
            <button
              onClick={() => {
                if (!saveAsInput.trim()) { toast.error('請填變數名'); return }
                onAdd('uia_get_table_rowcount', { save_as: saveAsInput.trim() })
              }}
              className="px-2 py-1 bg-purple-600 text-white rounded text-xs flex items-center gap-1 hover:bg-purple-700"
            >
              <Hash className="w-3 h-3" /> 讀列數
            </button>
          </div>
          <div className="flex gap-1">
            <input
              value={rowInput}
              onChange={e => setRowInput(e.target.value)}
              placeholder="row(可填 {{var}} 或 {{var + 1}})"
              className="flex-1 border border-gray-200 rounded px-2 py-1 text-xs font-mono"
            />
            <input
              value={colInput}
              onChange={e => setColInput(e.target.value)}
              placeholder="column"
              className="w-20 border border-gray-200 rounded px-2 py-1 text-xs font-mono"
            />
            <button
              onClick={() => {
                if (!rowInput.trim() || !colInput.trim()) { toast.error('row / column 都要填'); return }
                onAdd('uia_click_cell', {
                  row: /\D/.test(rowInput) ? rowInput : Number(rowInput),
                  column: /\D/.test(colInput) ? colInput : Number(colInput),
                })
              }}
              className="px-2 py-1 bg-purple-600 text-white rounded text-xs flex items-center gap-1 hover:bg-purple-700"
            >
              <ListChecks className="w-3 h-3" /> 點 cell
            </button>
          </div>
        </div>
      )}

      {!isEditable && !isGrid && (
        <p className="text-[10px] text-gray-500">
          (此元素 type 沒有「送文字 / 表格動作」可選;選 DataGrid 會出表格選項、選 Edit 會出送文字)
        </p>
      )}
    </div>
  )
}

function ActionBtn({
  icon: Icon, label, onClick,
}: {
  icon: typeof MousePointerClick
  label: string
  onClick: () => void
}) {
  return (
    <button
      onClick={onClick}
      className="px-2 py-1.5 bg-white border border-purple-300 rounded text-xs hover:bg-purple-100 flex items-center gap-1.5 text-purple-700"
    >
      <Icon className="w-3 h-3" /> {label}
    </button>
  )
}
