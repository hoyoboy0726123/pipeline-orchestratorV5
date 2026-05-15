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
import { useMemo, useState, useEffect } from 'react'
import { X, GitBranch, Plus, Trash2, Wand2 } from 'lucide-react'
import type { ConditionData, ConditionNode } from './_helpers'
import { VariableInput } from './_variablePicker'
import { getWorkflowVariables, type WorkflowVariablesResult } from '@/lib/api'

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
            <ConditionBuilder
              workflowId={workflowId}
              onApply={(expr) => onUpdate({ expression: expr })}
            />
            <div>
              <div className="flex items-end justify-between mb-1.5">
                <label className="text-xs font-semibold text-gray-500 uppercase tracking-wide">條件表達式</label>
              </div>
              <VariableInput
                value={data.expression || ''}
                onChange={(v) => onUpdate({ expression: v })}
                workflowId={workflowId}
                multiline
                rows={3}
                placeholder={'例:{{ steps.fetch.output.rows | int > 100 }}\n例:{{ "ok" in steps.api.output.stdout }}'}
                showHint={false}
              />
              <p className="text-[11px] text-gray-400 mt-1">Jinja2 boolean 表達式;求值後 truthy → on_true、falsy → on_false。上方「簡易設定」可自動產生、進階使用者也可直接打 Jinja2。</p>
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
              </div>
              <VariableInput
                value={data.switch || ''}
                onChange={(v) => onUpdate({ switch: v })}
                workflowId={workflowId}
                multiline
                rows={2}
                placeholder="例:{{ steps.api.output.status }}"
                showHint={false}
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

// ── 子 component:ConditionBuilder — 視覺化條件組裝(免手寫 Jinja2)─────
// 三個下拉(變數 → 比較符 → 值)→ 自動產生表達式塞回主編輯區
// 進階使用者仍可直接編輯 expression 文字、不會被擋
type OperatorKey =
  | 'eq' | 'ne'
  | 'gt' | 'gte' | 'lt' | 'lte'
  | 'contains' | 'not_contains'
  | 'truthy' | 'falsy'

const OPERATORS: { key: OperatorKey; label: string; needsValue: boolean }[] = [
  { key: 'eq',           label: '等於 (==)',            needsValue: true },
  { key: 'ne',           label: '不等於 (!=)',          needsValue: true },
  { key: 'gt',           label: '大於 (>)',             needsValue: true },
  { key: 'gte',          label: '大於等於 (>=)',         needsValue: true },
  { key: 'lt',           label: '小於 (<)',             needsValue: true },
  { key: 'lte',          label: '小於等於 (<=)',         needsValue: true },
  { key: 'contains',     label: '包含 (contains)',      needsValue: true },
  { key: 'not_contains', label: '不包含 (not in)',      needsValue: true },
  { key: 'truthy',       label: '有值 / 非空',          needsValue: false },
  { key: 'falsy',        label: '無值 / 空',            needsValue: false },
]

function buildExpression(varPath: string, op: OperatorKey, val: string): string {
  if (!varPath) return ''
  const v = `${varPath}`
  // 自動判斷:全數字 → 不加引號、轉 int 比;否則當字串
  const isNumeric = val.trim() !== '' && /^-?\d+(\.\d+)?$/.test(val.trim())
  const valQuoted = isNumeric ? val.trim() : `"${val.replace(/"/g, '\\"')}"`
  switch (op) {
    case 'eq':
      return isNumeric
        ? `{{ ${v} | int == ${val.trim()} }}`
        : `{{ ${v} == ${valQuoted} }}`
    case 'ne':
      return isNumeric
        ? `{{ ${v} | int != ${val.trim()} }}`
        : `{{ ${v} != ${valQuoted} }}`
    case 'gt':  return `{{ ${v} | int > ${val.trim() || '0'} }}`
    case 'gte': return `{{ ${v} | int >= ${val.trim() || '0'} }}`
    case 'lt':  return `{{ ${v} | int < ${val.trim() || '0'} }}`
    case 'lte': return `{{ ${v} | int <= ${val.trim() || '0'} }}`
    case 'contains':     return `{{ ${valQuoted} in ${v} }}`
    case 'not_contains': return `{{ ${valQuoted} not in ${v} }}`
    case 'truthy': return `{{ ${v} }}`
    case 'falsy':  return `{{ not ${v} }}`
  }
}

function ConditionBuilder({
  workflowId, onApply,
}: {
  workflowId?: string
  onApply: (expression: string) => void
}) {
  const [vars, setVars] = useState<WorkflowVariablesResult | null>(null)
  const [varPath, setVarPath] = useState('')
  const [op, setOp] = useState<OperatorKey>('gt')
  const [val, setVal] = useState('')

  useEffect(() => {
    if (!workflowId) return
    getWorkflowVariables(workflowId).then(setVars).catch(() => {})
  }, [workflowId])

  // 攤平所有可選變數(steps.X.output.Y + input.X)
  const options = useMemo(() => {
    if (!vars) return [] as { path: string; label: string; detail: string }[]
    const out: { path: string; label: string; detail: string }[] = []
    for (const s of vars.available.steps) {
      for (const f of s.fields) {
        out.push({
          path: `steps.${s.name}.output.${f.key}`,
          label: `${s.name}.${f.key}`,
          detail: `${s.node_type}${f.source ? ' · ' + f.source : ''}`,
        })
      }
    }
    for (const i of vars.available.input) {
      out.push({
        path: `input.${i.key}`,
        label: `input.${i.key}`,
        detail: i.required ? '啟動參數' : '啟動參數(可選)',
      })
    }
    return out
  }, [vars])

  const currentOp = OPERATORS.find(o => o.key === op)!
  const preview = varPath ? buildExpression(varPath, op, val) : '(請先選變數)'
  const canApply = !!varPath && (!currentOp.needsValue || val.trim() !== '')

  return (
    <div className="border border-orange-200 bg-orange-50/40 rounded-lg p-3 space-y-2">
      <div className="flex items-center gap-1.5 text-xs font-semibold text-orange-700">
        <Wand2 className="w-3.5 h-3.5" />
        <span>簡易設定(不用懂 Jinja2)</span>
      </div>

      <div className="grid grid-cols-1 gap-2">
        {/* 變數 */}
        <div>
          <label className="text-[11px] text-gray-500 block mb-0.5">變數(選上游 step 或啟動參數)</label>
          <select
            value={varPath}
            onChange={e => setVarPath(e.target.value)}
            className="w-full border border-gray-200 rounded-md px-2 py-1 text-xs font-mono outline-none focus:border-orange-400 bg-white"
          >
            <option value="">— 選擇變數 —</option>
            {options.map(o => (
              <option key={o.path} value={o.path}>{o.label} · {o.detail}</option>
            ))}
          </select>
        </div>

        {/* 比較符 + 值 */}
        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className="text-[11px] text-gray-500 block mb-0.5">比較</label>
            <select
              value={op}
              onChange={e => setOp(e.target.value as OperatorKey)}
              className="w-full border border-gray-200 rounded-md px-2 py-1 text-xs outline-none focus:border-orange-400 bg-white"
            >
              {OPERATORS.map(o => (
                <option key={o.key} value={o.key}>{o.label}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="text-[11px] text-gray-500 block mb-0.5">
              值{currentOp.needsValue ? '' : '(此運算子不用填)'}
            </label>
            <input
              value={val}
              onChange={e => setVal(e.target.value)}
              disabled={!currentOp.needsValue}
              placeholder={currentOp.needsValue ? '例:10 / "ok" / today' : ''}
              className="w-full border border-gray-200 rounded-md px-2 py-1 text-xs font-mono outline-none focus:border-orange-400 bg-white disabled:bg-gray-100 disabled:text-gray-400"
            />
          </div>
        </div>

        {/* 預覽 + 套用 */}
        <div className="flex items-center gap-2">
          <div className="flex-1 font-mono text-[11px] bg-white border border-gray-200 rounded-md px-2 py-1 truncate">
            <span className="text-gray-400">預覽:</span>{' '}
            <span className="text-gray-800">{preview}</span>
          </div>
          <button
            onClick={() => onApply(buildExpression(varPath, op, val))}
            disabled={!canApply}
            className="shrink-0 px-3 py-1 text-xs font-medium rounded-md bg-orange-600 text-white hover:bg-orange-700 disabled:bg-gray-300 disabled:cursor-not-allowed transition-colors"
          >
            套用
          </button>
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
