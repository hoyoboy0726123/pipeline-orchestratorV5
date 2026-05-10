'use client'
/**
 * Dry-run 預覽 modal — Ticket 1e。
 *
 * 給定 YAML + 啟動參數,呼叫後端 /pipeline/dry-run、不執行,
 * 把每個 step 的 {{ }} 渲染後命令秀出來、讓使用者看清楚變數展開後實際會跑什麼。
 *
 * 用途:
 * - canvas toolbar 按「▶ 預覽渲染」叫出來
 * - 進階使用者驗證新加的變數值是否符合預期
 */
import { useState, useMemo, useEffect } from 'react'
import { createPortal } from 'react-dom'
import { X, Eye, AlertTriangle, CheckCircle2, Loader2 } from 'lucide-react'
import { dryRunPipeline, type DryRunResult } from '@/lib/api'

interface Props {
  yamlContent: string
  workflowId?: string
  initialInputParams?: Record<string, string>
  /** YAML 自動掃出來的 input keys、給空表單用 */
  scannedInputKeys?: string[]
  onClose: () => void
}

export default function DryRunModal({
  yamlContent,
  workflowId,
  initialInputParams,
  scannedInputKeys,
  onClose,
}: Props) {
  const [params, setParams] = useState<Record<string, string>>(initialInputParams || {})
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<DryRunResult | null>(null)
  const [error, setError] = useState<string | null>(null)

  // 開啟時先補上 scannedInputKeys 的空欄位、讓使用者一眼看到要填什麼
  useEffect(() => {
    if (!scannedInputKeys || scannedInputKeys.length === 0) return
    setParams((p) => {
      const next = { ...p }
      for (const k of scannedInputKeys) {
        if (!(k in next)) next[k] = ''
      }
      return next
    })
  }, [scannedInputKeys])

  const run = async () => {
    setRunning(true)
    setError(null)
    try {
      const r = await dryRunPipeline({
        yaml_content: yamlContent,
        input_params: params,
        workflow_id: workflowId,
      })
      setResult(r)
    } catch (e: any) {
      setError(String(e?.message || e))
    } finally {
      setRunning(false)
    }
  }

  // 第一次打開自動跑一次
  useEffect(() => {
    run()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const inputEntries = useMemo(() => Object.entries(params), [params])

  return createPortal(
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/40"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-2xl shadow-2xl w-[760px] max-w-[94vw] max-h-[88vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3.5 border-b">
          <div className="flex items-center gap-2">
            <Eye className="w-4 h-4 text-indigo-500" />
            <span className="font-semibold text-sm text-gray-800">預覽渲染 — 此 workflow 實際會跑的命令</span>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600">
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Input params 編輯區 */}
        {inputEntries.length > 0 && (
          <div className="px-5 py-3 border-b bg-gray-50">
            <div className="text-xs font-semibold text-gray-600 mb-2">啟動參數 (input)</div>
            <div className="grid grid-cols-2 gap-x-3 gap-y-2">
              {inputEntries.map(([k, v]) => (
                <div key={k}>
                  <label className="text-[11px] text-gray-500 block mb-0.5 font-mono">input.{k}</label>
                  <input
                    value={v}
                    onChange={(e) => setParams((p) => ({ ...p, [k]: e.target.value }))}
                    placeholder={`例:${k.includes('date') ? '2026-05-10' : ''}`}
                    className="w-full border border-gray-200 rounded-md px-2 py-1 text-xs font-mono outline-none focus:border-indigo-400"
                  />
                </div>
              ))}
            </div>
            <div className="flex justify-end mt-2">
              <button
                onClick={run}
                disabled={running}
                className="px-3 py-1 text-xs bg-indigo-600 text-white rounded-md hover:bg-indigo-700 disabled:opacity-50 inline-flex items-center gap-1"
              >
                {running ? <Loader2 className="w-3 h-3 animate-spin" /> : '重新預覽'}
              </button>
            </div>
          </div>
        )}

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-4 space-y-3">
          {running && !result && (
            <p className="text-center text-gray-400 py-8 text-sm">渲染中…</p>
          )}
          {error && (
            <div className="text-sm text-red-600 bg-red-50 rounded-lg p-3">
              <AlertTriangle className="w-4 h-4 inline mr-1" /> {error}
            </div>
          )}
          {result && (
            <>
              <div
                className={`text-sm rounded-lg p-3 flex items-start gap-2 ${
                  result.ok
                    ? 'bg-emerald-50 text-emerald-700'
                    : 'bg-amber-50 text-amber-700'
                }`}
              >
                {result.ok ? (
                  <CheckCircle2 className="w-4 h-4 shrink-0 mt-0.5" />
                ) : (
                  <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
                )}
                <div className="flex-1">
                  {result.ok
                    ? '✓ 所有 step 變數都展開成功'
                    : '⚠ 部分 step 有未定義變數,展開到下方步驟看詳情'}
                </div>
              </div>

              {result.steps.map((s) => (
                <div
                  key={s.index}
                  className={`border rounded-lg overflow-hidden ${
                    s.errors.length > 0 ? 'border-red-200' : 'border-gray-200'
                  }`}
                >
                  <div className="px-3 py-2 bg-gray-50 border-b flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className="text-[11px] font-mono text-gray-400">#{s.index + 1}</span>
                      <span className="font-semibold text-xs text-gray-800">{s.name}</span>
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-gray-200 text-gray-600">
                        {s.node_type}
                      </span>
                    </div>
                    {s.referenced_vars.length > 0 && (
                      <span className="text-[10px] text-gray-500">
                        引用 {s.referenced_vars.length} 個變數
                      </span>
                    )}
                  </div>
                  <div className="p-3 space-y-2">
                    {Object.keys(s.rendered).length === 0 && s.errors.length === 0 && (
                      <p className="text-[11px] text-gray-400">此 step 無 {`{{ }}`} 引用</p>
                    )}
                    {Object.entries(s.rendered).map(([fname, val]) => (
                      <div key={fname}>
                        <div className="text-[11px] text-gray-500 mb-0.5">{fname}</div>
                        <div className="bg-gray-900 text-emerald-300 font-mono text-xs px-2.5 py-1.5 rounded leading-relaxed break-all">
                          {val}
                        </div>
                      </div>
                    ))}
                    {s.errors.map((err, i) => (
                      <div
                        key={i}
                        className="bg-red-50 text-red-700 text-xs rounded px-2 py-1 border border-red-200"
                      >
                        ❌ {err}
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </>
          )}
        </div>

        {/* Footer */}
        <div className="px-5 py-2.5 border-t bg-gray-50 text-[11px] text-gray-500">
          💡 此預覽不會執行任何 step、純 render {`{{ }}`} 給你看實際命令
        </div>
      </div>
    </div>,
    document.body,
  )
}
