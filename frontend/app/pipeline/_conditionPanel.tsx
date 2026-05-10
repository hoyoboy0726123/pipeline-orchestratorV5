'use client'
/**
 * Condition 節點面板 — Ticket 2。
 *
 * 兩個模式:
 * - IF:expression(boolean)決定 on_true / on_false 跳哪
 * - Switch:switch 求值後 str() 比對 cases keys、沒命中走 default
 *
 * 此節點純 metadata、不執行命令。runner 求值後跳到目標 step name。
 */
import { useMemo } from 'react'
import { X, GitBranch, Plus, Trash2 } from 'lucide-react'
import type { ConditionData, ConditionNode } from './_helpers'
import { VariableButton } from './_variablePicker'

const CONDITION_COLOR = '#f97316'  // 橘色 — 跟其他節點區隔

interface Props {
  node: ConditionNode
  onUpdate: (data: Partial<ConditionData>) => void
  onClose: () => void
  onDelete: () => void
  workflowId?: string
  /** 此 workflow 內所有可跳轉的 step 名稱(供下拉選單) */
  availableStepNames?: string[]
}

export default function ConditionPanel({
  node, onUpdate, onClose, onDelete, workflowId, availableStepNames = [],
}: Props) {
  const data = node.data
  const inputCls = 'w-full border border-gray-200 rounded-lg px-2.5 py-1.5 text-sm outline-none focus:border-orange-400 focus:ring-1 focus:ring-orange-400/20 bg-white'

  // 排除自己,避免直接跳回自己造成無限迴圈
  const targetOptions = useMemo(
    () => availableStepNames.filter(n => n !== data.name),
    [availableStepNames, data.name],
  )

  return (
    <div className="absolute top-0 right-0 h-full w-[420px] bg-white shadow-2xl border-l border-gray-100 flex flex-col z-30 overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-3.5 border-b" style={{ borderTopColor: CONDITION_COLOR, borderTopWidth: 3 }}>
        <span className="w-8 h-8 rounded-full flex items-center justify-center text-white shrink-0"
          style={{ background: CONDITION_COLOR }}><GitBranch className="w-4 h-4" strokeWidth={2.4} /></span>
        <div className="flex-1 min-w-0">
          <span className="font-semibold text-gray-800 text-sm block truncate">Condition 控制流</span>
          <span className="text-xs text-gray-400">求值表達式、跳到指定 step</span>
        </div>
        <button onClick={onDelete} title="刪除" className="text-gray-300 hover:text-red-400 transition-colors p-1">🗑</button>
        <button onClick={onClose} className="text-gray-400 hover:text-gray-600 transition-colors"><X className="w-4 h-4" /></button>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {/* 名稱 */}
        <div>
          <label className="text-xs font-semibold text-gray-500 uppercase tracking-wide block mb-1.5">節點名稱</label>
          <input value={data.name} onChange={e => onUpdate({ name: e.target.value })} className={`${inputCls} font-mono`} placeholder="例:check_size / route_status" />
        </div>

        {/* 模式切換 */}
        <div>
          <label className="text-xs font-semibold text-gray-500 uppercase tracking-wide block mb-1.5">模式</label>
          <div className="flex gap-2">
            <button
              onClick={() => onUpdate({ mode: 'if' })}
              className={`flex-1 py-2 rounded-lg text-sm font-medium border transition-colors ${
                data.mode === 'if'
                  ? 'bg-orange-600 text-white border-orange-600'
                  : 'text-gray-600 border-gray-200 hover:border-orange-400'
              }`}
            >🔀 IF(成立 / 不成立)</button>
            <button
              onClick={() => onUpdate({ mode: 'switch' })}
              className={`flex-1 py-2 rounded-lg text-sm font-medium border transition-colors ${
                data.mode === 'switch'
                  ? 'bg-orange-600 text-white border-orange-600'
                  : 'text-gray-600 border-gray-200 hover:border-orange-400'
              }`}
            >🎯 Switch(多分支)</button>
          </div>
        </div>

        {/* IF 模式 */}
        {data.mode === 'if' && (
          <>
            <div>
              <div className="flex items-end justify-between mb-1.5">
                <label className="text-xs font-semibold text-gray-500 uppercase tracking-wide">條件表達式</label>
                <VariableButton
                  workflowId={workflowId}
                  onPick={(p) => onUpdate({ expression: `${data.expression || ''}{{ ${p} }}` })}
                />
              </div>
              <textarea
                rows={3}
                value={data.expression}
                onChange={e => onUpdate({ expression: e.target.value })}
                placeholder={'例:{{ steps.fetch.output.rows | int > 100 }}\n例:{{ "ok" in steps.api.output.stdout }}'}
                className={`${inputCls} font-mono text-xs resize-y leading-relaxed`}
              />
              <p className="text-[11px] text-gray-400 mt-1">Jinja2 boolean 表達式;求值後 truthy → on_true、falsy → on_false</p>
            </div>

            <div>
              <label className="text-xs font-semibold text-gray-500 uppercase tracking-wide block mb-1.5">✅ 條件成立(true)→ 跳到</label>
              <StepSelect value={data.onTrue} options={targetOptions} onChange={v => onUpdate({ onTrue: v })} />
            </div>

            <div>
              <label className="text-xs font-semibold text-gray-500 uppercase tracking-wide block mb-1.5">❌ 條件不成立(false)→ 跳到</label>
              <StepSelect value={data.onFalse} options={targetOptions} onChange={v => onUpdate({ onFalse: v })} />
            </div>
          </>
        )}

        {/* Switch 模式 */}
        {data.mode === 'switch' && (
          <>
            <div>
              <div className="flex items-end justify-between mb-1.5">
                <label className="text-xs font-semibold text-gray-500 uppercase tracking-wide">求值表達式</label>
                <VariableButton
                  workflowId={workflowId}
                  onPick={(p) => onUpdate({ switch: `${data.switch || ''}{{ ${p} }}` })}
                />
              </div>
              <input
                value={data.switch}
                onChange={e => onUpdate({ switch: e.target.value })}
                placeholder="例:{{ steps.api.output.status }}"
                className={`${inputCls} font-mono text-xs`}
              />
              <p className="text-[11px] text-gray-400 mt-1">求值結果 str() 後與 cases keys 比對</p>
            </div>

            <div>
              <label className="text-xs font-semibold text-gray-500 uppercase tracking-wide block mb-1.5">分支(cases)</label>
              <CasesEditor
                cases={data.cases}
                stepOptions={targetOptions}
                onChange={(c) => onUpdate({ cases: c })}
              />
            </div>

            <div>
              <label className="text-xs font-semibold text-gray-500 uppercase tracking-wide block mb-1.5">⚙️ 預設(default)→ 沒命中時跳到</label>
              <StepSelect value={data.default} options={targetOptions} onChange={v => onUpdate({ default: v })} />
              <p className="text-[11px] text-gray-400 mt-1">留空 = 沒命中就結束流程</p>
            </div>
          </>
        )}

        {/* 提示 */}
        <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 text-xs text-amber-800 leading-relaxed">
          💡 <b>記得在 branch step 加 `next: end`</b> — 否則 branch 跑完會線性走到下一個 step、可能誤跑到對方 branch。
        </div>
      </div>
    </div>
  )
}

// ── 子 component:step 名稱下拉(可選)+ 純文字輸入(打字)的 union ──
function StepSelect({ value, options, onChange }: {
  value: string
  options: string[]
  onChange: (v: string) => void
}) {
  return (
    <div className="flex gap-1.5">
      <input
        list="condition-step-list"
        value={value}
        onChange={e => onChange(e.target.value)}
        placeholder="留空 = 結束流程 / 或 'end' / 或下拉選 step"
        className="flex-1 border border-gray-200 rounded-lg px-2.5 py-1.5 text-sm font-mono outline-none focus:border-orange-400 focus:ring-1 focus:ring-orange-400/20"
      />
      <datalist id="condition-step-list">
        <option value="end" />
        {options.map(name => <option key={name} value={name} />)}
      </datalist>
    </div>
  )
}

// ── Switch cases 編輯器 ─────────────────────────────────────────────────
function CasesEditor({ cases, stepOptions, onChange }: {
  cases: Record<string, string>
  stepOptions: string[]
  onChange: (c: Record<string, string>) => void
}) {
  const entries = Object.entries(cases || {})

  const updateKey = (oldKey: string, newKey: string) => {
    if (newKey === oldKey) return
    const next: Record<string, string> = {}
    for (const [k, v] of entries) {
      next[k === oldKey ? newKey : k] = v
    }
    onChange(next)
  }
  const updateVal = (key: string, val: string) => {
    onChange({ ...cases, [key]: val })
  }
  const addCase = () => {
    const nextKey = `case${entries.length + 1}`
    onChange({ ...cases, [nextKey]: '' })
  }
  const removeCase = (key: string) => {
    const next = { ...cases }
    delete next[key]
    onChange(next)
  }

  return (
    <div className="space-y-1.5">
      {entries.map(([k, v]) => (
        <div key={k} className="flex gap-1.5 items-center">
          <input
            value={k}
            onChange={(e) => updateKey(k, e.target.value)}
            placeholder="value"
            className="w-24 shrink-0 border border-gray-200 rounded-md px-2 py-1 text-xs font-mono outline-none focus:border-orange-400"
          />
          <span className="text-xs text-gray-400">→</span>
          <input
            list="condition-step-list"
            value={v}
            onChange={(e) => updateVal(k, e.target.value)}
            placeholder="step name"
            className="flex-1 border border-gray-200 rounded-md px-2 py-1 text-xs font-mono outline-none focus:border-orange-400"
          />
          <button
            onClick={() => removeCase(k)}
            title="刪除這個 case"
            className="shrink-0 p-1 text-gray-300 hover:text-red-400"
          ><Trash2 className="w-3.5 h-3.5" /></button>
        </div>
      ))}
      <button
        onClick={addCase}
        className="w-full py-1 text-xs text-orange-600 border border-dashed border-orange-300 rounded-md hover:bg-orange-50 transition-colors flex items-center justify-center gap-1"
      ><Plus className="w-3 h-3" /> 加一個 case</button>
    </div>
  )
}
