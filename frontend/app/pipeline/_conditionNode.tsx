'use client'
import { memo } from 'react'
import { Handle, Position, type NodeProps, type Node } from '@xyflow/react'
import { GitBranch } from 'lucide-react'
import type { ConditionData } from './_helpers'
import { useRunStatusStore } from './_runStatus'

const STATUS_ICON: Record<string, string> = { idle: '●', running: '⟳', success: '✓', failed: '✗' }
const STATUS_COLOR: Record<string, string> = {
  idle: 'text-white/60', running: 'text-yellow-200 animate-spin', success: 'text-green-200', failed: 'text-red-200',
}

const CONDITION_COLOR = '#f97316'  // 橘色

type ConditionNodeType = Node<ConditionData>

function ConditionNodeComponent({ data, selected }: NodeProps<ConditionNodeType>) {
  const runtime = useRunStatusStore(s => s.stepStatuses[data.name])
  const status = runtime?.status ?? 'idle'

  const color = status === 'failed' ? '#ef4444'
    : status === 'success' ? '#10b981'
    : status === 'running' ? '#f59e0b'
    : CONDITION_COLOR

  // 簡短描述 — 顯示在 body
  let summary = ''
  if (data.mode === 'if') {
    summary = data.expression
      ? `IF: ${data.expression.slice(0, 30)}${data.expression.length > 30 ? '…' : ''}`
      : '(未設條件)'
  } else {
    const cases = Object.keys(data.cases || {}).length
    summary = data.switch ? `Switch: ${cases} 個 case` : '(未設 switch)'
  }

  return (
    <div className="w-56 rounded-xl overflow-hidden shadow-md transition-shadow"
      style={{
        border: selected ? `2px solid ${color}` : '2px solid transparent',
        boxShadow: selected ? `0 0 0 3px ${color}33, 0 4px 16px rgba(0,0,0,0.12)` : '0 2px 8px rgba(0,0,0,0.10)',
      }}
    >
      <Handle type="target" position={Position.Left}
        className="!w-3 !h-3 !rounded-full !border-2 !border-white" style={{ background: color }} />

      {/* Header */}
      <div className="px-3 py-2.5 flex items-center gap-2" style={{ background: color }}>
        <GitBranch className="w-3.5 h-3.5 text-white shrink-0" strokeWidth={2.5} />
        <span className="text-white font-semibold text-sm flex-1 truncate leading-tight">{data.name}</span>
        <span className="text-[10px] px-1.5 py-0.5 rounded bg-white/20 text-white font-medium shrink-0">
          {data.mode === 'if' ? 'IF' : 'Switch'}
        </span>
        <span className={`text-sm shrink-0 ${STATUS_COLOR[status]}`}>{STATUS_ICON[status]}</span>
      </div>

      {/* Body */}
      <div className="bg-white px-3 py-2.5 space-y-1">
        <p className="text-xs text-orange-600 font-mono truncate" title={summary}>
          {summary}
        </p>
        {data.mode === 'if' && (data.onTrue || data.onFalse) && (
          <div className="text-[10px] text-gray-500 leading-tight">
            {data.onTrue && <span>✓ → {data.onTrue}</span>}
            {data.onTrue && data.onFalse && <span className="mx-1">·</span>}
            {data.onFalse && <span>✗ → {data.onFalse}</span>}
          </div>
        )}
        {data.mode === 'switch' && data.default && (
          <div className="text-[10px] text-gray-500 leading-tight">
            default → {data.default}
          </div>
        )}
      </div>

      <Handle type="source" position={Position.Right}
        className="!w-3 !h-3 !rounded-full !border-2 !border-white" style={{ background: color }} />
    </div>
  )
}

export default memo(ConditionNodeComponent)
