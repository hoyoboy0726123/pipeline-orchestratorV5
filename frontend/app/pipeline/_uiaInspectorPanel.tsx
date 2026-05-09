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
import { useState, useCallback } from 'react'
import { ChevronDown, ChevronRight, MousePointerClick, Type, Eye, Hash, ListChecks, Clock, CheckCircle, RefreshCcw, Search } from 'lucide-react'
import { toast } from 'sonner'
import { uiaInspect, type UiaElement, type UiaInspectResult } from '@/lib/api'
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
          onClick={() => setPicker({ element: el, path: path.split('/') })}
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
      {/* 視窗 pattern 輸入 + 抓取按鈕 */}
      <div>
        <label className="text-xs font-semibold text-purple-700 uppercase tracking-wide block mb-1.5">
          🪟 目標視窗(支援 wildcard *、空 = 當前 foreground)
        </label>
        <div className="flex items-center gap-2">
          <input
            value={uiaWindow}
            onChange={e => onUpdateWindow(e.target.value)}
            placeholder="例:公司系統*訂單* 或 留空用 foreground"
            className="flex-1 border border-gray-200 rounded-lg px-2.5 py-1.5 text-sm font-mono outline-none focus:border-purple-400 focus:ring-1 focus:ring-purple-400/20 bg-white"
          />
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
          按下時會把當前 Windows app 的 UIA tree 抓回來、可從中挑要操作的 control。
          先把目標視窗叫到前景、或填 title pattern 鎖定特定視窗。
        </p>
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
