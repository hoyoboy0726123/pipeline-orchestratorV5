'use client'
/**
 * 變數選擇器 — Ticket 1e。
 *
 * 兩種使用方式:
 * 1. <VariableButton ... /> 按鈕點開 modal、列出所有可用變數、點選就插到 textarea / input 游標位置
 * 2. <VariableAutocomplete ... /> 在 textarea 偵測使用者敲 `{{` 時跳出下拉、鍵盤導航
 *
 * 兩個 component 都用同一份 useWorkflowVariables hook 拿可用變數 + 上次值。
 */
import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { createPortal } from 'react-dom'
import { Link as LinkIcon, X, Search } from 'lucide-react'
import {
  getWorkflowVariables,
  type WorkflowVariablesResult,
  type VariableField,
} from '@/lib/api'

// ── 共用 hook:拉變數清單 ─────────────────────────────────────────────────
function useWorkflowVariables(workflowId: string | undefined, enabled: boolean) {
  const [data, setData] = useState<WorkflowVariablesResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!enabled || !workflowId) return
    let cancelled = false
    setLoading(true)
    setError(null)
    getWorkflowVariables(workflowId)
      .then((r) => { if (!cancelled) setData(r) })
      .catch((e) => { if (!cancelled) setError(String(e?.message || e)) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [workflowId, enabled])

  return { data, loading, error }
}

// 把 dotted-path 攤平成「可選變數」list、給搜尋過濾用
interface PickerItem {
  path: string                 // 完整 dotted-path,例:steps.crawl.output.path
  category: 'steps' | 'input'
  label: string                // UI 顯示用,例:crawl.path
  detail: string               // 副標,例:Script · 上次:data/ptt.csv
  lastValue: string
  source?: string
}

function flattenVariables(data: WorkflowVariablesResult | null): PickerItem[] {
  if (!data) return []
  const out: PickerItem[] = []
  for (const step of data.available.steps) {
    for (const f of step.fields) {
      out.push({
        path: `steps.${step.name}.output.${f.key}`,
        category: 'steps',
        label: `${step.name}.${f.key}`,
        detail: `${step.node_type}${f.source ? ' · ' + f.source : ''}`,
        lastValue: String(f.last_value ?? ''),
        source: f.source,
      })
    }
  }
  for (const i of data.available.input) {
    out.push({
      path: `input.${i.key}`,
      category: 'input',
      label: `input.${i.key}`,
      detail: i.required ? '啟動參數' : '啟動參數(可選)',
      lastValue: String(i.last_value ?? ''),
    })
  }
  // 環境變數刻意不放進 picker / autocomplete:對 batch 編寫沒實際用途、徒增認知負擔。
  // runtime 仍會替換 {{ env.X }}、進階使用者要用就直接打字。
  return out
}

// ── Picker Modal ─────────────────────────────────────────────────────────
interface PickerModalProps {
  workflowId?: string
  onSelect: (path: string) => void
  onClose: () => void
}

function PickerModal({ workflowId, onSelect, onClose }: PickerModalProps) {
  const { data, loading, error } = useWorkflowVariables(workflowId, true)
  const [search, setSearch] = useState('')

  const all = useMemo(() => flattenVariables(data), [data])
  const filtered = useMemo(() => {
    if (!search.trim()) return all
    const s = search.toLowerCase()
    return all.filter(
      (it) =>
        it.label.toLowerCase().includes(s) ||
        it.path.toLowerCase().includes(s) ||
        it.lastValue.toLowerCase().includes(s),
    )
  }, [all, search])

  const grouped = useMemo(() => {
    const g: Record<string, PickerItem[]> = { steps: [], input: [] }
    for (const it of filtered) g[it.category].push(it)
    return g
  }, [filtered])

  return createPortal(
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/40"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-2xl shadow-2xl w-[640px] max-w-[92vw] max-h-[80vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3.5 border-b">
          <div className="flex items-center gap-2">
            <LinkIcon className="w-4 h-4 text-indigo-500" />
            <span className="font-semibold text-sm text-gray-800">插入變數</span>
            {workflowId ? null : (
              <span className="text-xs text-amber-600 ml-2">
                (此工作流尚未存檔、暫無上次值參考)
              </span>
            )}
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600">
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Search */}
        <div className="px-5 py-2.5 border-b bg-gray-50">
          <div className="relative">
            <Search className="w-3.5 h-3.5 text-gray-400 absolute left-2.5 top-1/2 -translate-y-1/2" />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="搜尋變數名稱或值…"
              autoFocus
              className="w-full pl-8 pr-3 py-1.5 text-sm border border-gray-200 rounded-lg outline-none focus:border-indigo-400 bg-white"
            />
          </div>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-3 space-y-3">
          {loading && (
            <p className="text-center text-gray-400 py-4 text-sm">載入中…</p>
          )}
          {error && (
            <div className="text-sm text-red-600 bg-red-50 rounded-lg p-3">
              載入失敗:{error}
            </div>
          )}
          {!loading && !error && data && (
            <>
              {/* 上游節點 */}
              {grouped.steps.length > 0 && (
                <Section
                  title="🟦 上游節點輸出"
                  hint="點選會插入 {{ steps.X.output.Y }}"
                  items={grouped.steps}
                  onSelect={onSelect}
                />
              )}
              {/* 啟動參數 */}
              {grouped.input.length > 0 && (
                <Section
                  title="🟩 啟動參數 (input)"
                  hint="跑 workflow 時 user 傳入,例:/run X date=today"
                  items={grouped.input}
                  onSelect={onSelect}
                />
              )}
              {/* 環境變數區塊已移除 — 對 batch 編寫沒實際用途、徒增認知負擔。
                  如需 env 變數仍可在 batch 直接打 {{ env.X }}、runtime 會替換 */}
              {filtered.length === 0 && (
                <p className="text-center text-gray-400 py-6 text-sm">
                  沒有符合的變數
                </p>
              )}
            </>
          )}
        </div>

        {/* Footer hint */}
        <div className="px-5 py-2.5 border-t bg-gray-50 text-[11px] text-gray-500">
          💡 也可在輸入框直接敲 <code className="px-1 py-0.5 rounded bg-gray-200 font-mono">{`{{`}</code> 觸發自動完成
        </div>
      </div>
    </div>,
    document.body,
  )
}

// 變數用途提示 — 教使用者「該選哪個、別選哪個」
// 避免新手把 stdout / exit_code / status 當「上一步輸出檔」誤插
function getUsageHint(path: string): { tone: 'rec' | 'warn' | 'info'; text: string } | null {
  if (path.endsWith('.path')) {
    return { tone: 'rec', text: '✅ 推薦:檔案實際路徑(下游 LLM / 腳本要讀檔請選這個)' }
  }
  if (path.endsWith('.stdout')) {
    return { tone: 'warn', text: '⚠ 上一步 stdout 一行訊息、不是檔案內容,通常不要用' }
  }
  if (path.endsWith('.exit_code')) {
    return { tone: 'warn', text: '⚠ 只是 0 / 1 數字、沒語意,通常不要用' }
  }
  if (path.endsWith('.status')) {
    return { tone: 'warn', text: '⚠ 只是 ok / failed 字串(驗證結果)、沒實際資料,通常不要用' }
  }
  if (path.startsWith('input.')) {
    return { tone: 'info', text: '🟩 啟動參數:跑 workflow 時由 user 傳入(/run X arg=value)' }
  }
  return null
}

const _TONE_CLASS = {
  rec: 'text-emerald-600',
  warn: 'text-amber-600',
  info: 'text-gray-500',
} as const

function Section({
  title, hint, items, onSelect,
}: {
  title: string
  hint: string
  items: PickerItem[]
  onSelect: (path: string) => void
}) {
  return (
    <div className="border border-gray-200 rounded-lg overflow-hidden">
      <div className="px-3 py-2 bg-gray-50 border-b">
        <div className="text-xs font-semibold text-gray-700">{title}</div>
        <div className="text-[11px] text-gray-500 mt-0.5">{hint}</div>
      </div>
      <div className="divide-y divide-gray-100">
        {items.map((it) => {
          const usage = getUsageHint(it.path)
          return (
            <button
              key={it.path}
              onClick={() => onSelect(it.path)}
              className="w-full flex items-center justify-between gap-3 px-3 py-2 hover:bg-indigo-50 text-left transition-colors"
            >
              <div className="flex-1 min-w-0">
                <div className="font-mono text-xs text-gray-800 truncate">{it.label}</div>
                <div className="text-[11px] text-gray-400 truncate mt-0.5">{it.detail}</div>
                {usage && (
                  <div className={`text-[11px] mt-0.5 truncate ${_TONE_CLASS[usage.tone]}`}>
                    {usage.text}
                  </div>
                )}
              </div>
              {it.lastValue && (
                <div
                  className="font-mono text-[11px] text-emerald-700 bg-emerald-50 px-2 py-1 rounded max-w-[200px] truncate shrink-0"
                  title={it.lastValue}
                >
                  {it.lastValue}
                </div>
              )}
              <span className="text-[11px] text-indigo-500 font-medium shrink-0">插入</span>
            </button>
          )
        })}
      </div>
    </div>
  )
}

// ── 公開 component:VariableButton ─────────────────────────────────────────
/**
 * 「🔗 插入變數」按鈕 — 點開 modal、選擇變數後 callback 會收到完整 dotted-path 字串
 * (例:"steps.crawl.output.path")。Caller 需自己負責把字串包成 `{{ X }}` 並插到欄位。
 *
 * 通常搭配 <VariableInput /> 一起用,會幫你處理插入邏輯。
 */
export function VariableButton({
  workflowId,
  onPick,
  className = '',
  label = '插入變數',
}: {
  workflowId?: string
  onPick: (path: string) => void
  className?: string
  label?: string
}) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className={
          'inline-flex items-center gap-1 px-2 py-1 text-[11px] rounded-md border ' +
          'border-indigo-200 bg-indigo-50 text-indigo-700 hover:bg-indigo-100 transition-colors ' +
          className
        }
        title="開啟變數選擇器(列出所有可引用變數)"
      >
        <LinkIcon className="w-3 h-3" />
        {label}
      </button>
      {open && (
        <PickerModal
          workflowId={workflowId}
          onSelect={(path) => {
            onPick(path)
            // modal 不關 — 讓使用者連點插多個
          }}
          onClose={() => setOpen(false)}
        />
      )}
    </>
  )
}

// ── 公開 component:VariableInput(textarea / input wrapper)──────────────
/**
 * 包裝 <textarea> 或 <input>:
 * - 上方右側放 [🔗 插入變數] 按鈕 + 提示字
 * - 偵測使用者敲 `{{` 觸發自動完成下拉
 * - 點選變數時自動把游標位置插入 `{{ <dotted_path> }}`
 *
 * 用法:
 *   <VariableInput
 *     value={data.batch}
 *     onChange={(v) => upd({ batch: v })}
 *     workflowId={workflowId}
 *     multiline
 *     placeholder="..."
 *   />
 */
export function VariableInput({
  value,
  onChange,
  workflowId,
  multiline = false,
  rows = 2,
  placeholder,
  className = '',
  buttonLabel = '插入變數',
  showHint = true,
}: {
  value: string
  onChange: (v: string) => void
  workflowId?: string
  multiline?: boolean
  rows?: number
  placeholder?: string
  className?: string
  buttonLabel?: string
  showHint?: boolean
}) {
  const inputRef = useRef<HTMLTextAreaElement | HTMLInputElement | null>(null)
  const [autocompleteOpen, setAutocompleteOpen] = useState(false)
  const [autocompleteQuery, setAutocompleteQuery] = useState('')
  // {{ 觸發位置:autocomplete popup 在這個游標位置展開,選中後從這裡開始覆蓋
  const [autocompleteAnchor, setAutocompleteAnchor] = useState<{
    start: number   // {{ 起始位置(含 `{{`)
    end: number     // 目前游標位置
    coords: { left: number; top: number; height: number }
  } | null>(null)
  const [highlightIdx, setHighlightIdx] = useState(0)

  const { data } = useWorkflowVariables(workflowId, autocompleteOpen)
  const allItems = useMemo(() => flattenVariables(data), [data])
  const filteredItems = useMemo(() => {
    if (!autocompleteQuery.trim()) return allItems.slice(0, 10)
    const s = autocompleteQuery.toLowerCase()
    return allItems
      .filter((it) => it.path.toLowerCase().includes(s) || it.label.toLowerCase().includes(s))
      .slice(0, 10)
  }, [allItems, autocompleteQuery])

  // 用 element 在 viewport 的位置近似計算 popup 座標(夠用)
  const computeAnchorCoords = (el: HTMLElement) => {
    const rect = el.getBoundingClientRect()
    return { left: rect.left, top: rect.bottom + 4, height: rect.height }
  }

  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement | HTMLInputElement>) => {
    const next = e.target.value
    onChange(next)
    const pos = e.target.selectionStart ?? next.length
    // 找前面最近的 `{{`、且尚未閉合
    const before = next.slice(0, pos)
    const lastOpen = before.lastIndexOf('{{')
    const lastClose = before.lastIndexOf('}}')
    if (lastOpen >= 0 && lastOpen > lastClose) {
      // 在 `{{` 後、未閉合 → 觸發自動完成
      const inside = next.slice(lastOpen + 2, pos)
      // 含換行就退出(別人在多行情境敲 `{{` 不是想觸發)
      if (!inside.includes('\n')) {
        setAutocompleteQuery(inside.trim())
        setAutocompleteAnchor({
          start: lastOpen,
          end: pos,
          coords: computeAnchorCoords(e.target),
        })
        setAutocompleteOpen(true)
        setHighlightIdx(0)
        return
      }
    }
    setAutocompleteOpen(false)
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement | HTMLInputElement>) => {
    if (!autocompleteOpen || filteredItems.length === 0) return
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setHighlightIdx((i) => (i + 1) % filteredItems.length)
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setHighlightIdx((i) => (i - 1 + filteredItems.length) % filteredItems.length)
    } else if (e.key === 'Enter' || e.key === 'Tab') {
      e.preventDefault()
      insertSelected(filteredItems[highlightIdx].path)
    } else if (e.key === 'Escape') {
      setAutocompleteOpen(false)
    }
  }

  const insertSelected = useCallback(
    (path: string) => {
      if (!autocompleteAnchor) return
      const before = value.slice(0, autocompleteAnchor.start)
      const after = value.slice(autocompleteAnchor.end)
      // 用標準 Jinja2 寫法:`{{ path }}`(前後加空格、人眼好讀)
      const insertion = `{{ ${path} }}`
      const next = before + insertion + after
      onChange(next)
      setAutocompleteOpen(false)
      // 把游標移到插入文字之後
      requestAnimationFrame(() => {
        const el = inputRef.current
        if (el) {
          const pos = before.length + insertion.length
          el.focus()
          el.setSelectionRange(pos, pos)
        }
      })
    },
    [autocompleteAnchor, value, onChange],
  )

  // VariableButton 的插入(沒在 textarea 自動完成情境):游標位置插
  const insertAtCursor = (path: string) => {
    const el = inputRef.current
    const insertion = `{{ ${path} }}`
    if (el) {
      const start = el.selectionStart ?? value.length
      const end = el.selectionEnd ?? value.length
      const next = value.slice(0, start) + insertion + value.slice(end)
      onChange(next)
      requestAnimationFrame(() => {
        const pos = start + insertion.length
        el.focus()
        el.setSelectionRange(pos, pos)
      })
    } else {
      onChange(value + insertion)
    }
  }

  const baseInputCls =
    'w-full border border-gray-200 rounded-lg px-2.5 py-1.5 text-sm outline-none ' +
    'focus:border-indigo-400 focus:ring-1 focus:ring-indigo-400/20 bg-white font-mono'

  return (
    <div className={`relative ${className}`}>
      <div className="flex items-center justify-end gap-2 mb-1">
        <VariableButton workflowId={workflowId} onPick={insertAtCursor} label={buttonLabel} />
      </div>

      {multiline ? (
        <textarea
          ref={inputRef as React.RefObject<HTMLTextAreaElement>}
          rows={rows}
          value={value}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          className={`${baseInputCls} resize-none`}
        />
      ) : (
        <input
          ref={inputRef as React.RefObject<HTMLInputElement>}
          value={value}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          className={baseInputCls}
        />
      )}

      {showHint && (
        <p className="text-[11px] text-gray-400 mt-1">
          💡 輸入 <code className="px-1 rounded bg-gray-100 font-mono">{`{{`}</code> 或按
          <span className="font-semibold text-indigo-600"> 🔗 插入變數 </span>
          引用上游 step / input / env
        </p>
      )}

      {/* Autocomplete dropdown */}
      {autocompleteOpen && autocompleteAnchor && filteredItems.length > 0 &&
        createPortal(
          <div
            className="fixed z-[70] bg-white border border-gray-200 rounded-lg shadow-xl w-[420px] max-h-[280px] overflow-y-auto"
            style={{
              left: Math.min(autocompleteAnchor.coords.left, window.innerWidth - 440),
              top: Math.min(autocompleteAnchor.coords.top, window.innerHeight - 300),
            }}
          >
            <div className="px-3 py-1.5 bg-gray-50 border-b text-[11px] text-gray-500">
              ↑↓ 選擇 · Enter/Tab 插入 · Esc 關閉
            </div>
            <div className="divide-y divide-gray-100">
              {filteredItems.map((it, i) => (
                <button
                  type="button"
                  key={it.path}
                  onMouseEnter={() => setHighlightIdx(i)}
                  onMouseDown={(e) => {
                    e.preventDefault() // 防止 textarea blur
                    insertSelected(it.path)
                  }}
                  className={`w-full flex items-center justify-between gap-3 px-3 py-1.5 text-left transition-colors ${
                    i === highlightIdx ? 'bg-indigo-100' : 'hover:bg-indigo-50'
                  }`}
                >
                  <div className="flex-1 min-w-0">
                    <div className="font-mono text-[11px] text-gray-800 truncate">{it.path}</div>
                    {it.detail && (
                      <div className="text-[10px] text-gray-400 truncate mt-0.5">{it.detail}</div>
                    )}
                  </div>
                  {it.lastValue && (
                    <div className="font-mono text-[10px] text-emerald-700 bg-emerald-50 px-1.5 py-0.5 rounded max-w-[150px] truncate shrink-0">
                      {it.lastValue}
                    </div>
                  )}
                </button>
              ))}
            </div>
          </div>,
          document.body,
        )}
    </div>
  )
}
