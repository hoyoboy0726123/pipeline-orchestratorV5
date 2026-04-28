'use client'
import { useEffect, useState } from 'react'
import { X, AlertTriangle, Loader2, Check } from 'lucide-react'
import { toast } from 'sonner'
import { testOutlookConnection } from '@/lib/api'
import type { OutlookData, OutlookNode } from './_helpers'

// 帶「確定」按鈕的日期/時間欄位：onChange 只寫 draft，按確定才 commit 到 params
// 避免使用者在 picker 裡選一半就被當前值覆蓋（用戶反映需要明確確認）
function DateTimeField({ value, type, onCommit }: {
  value: string
  type: 'date' | 'datetime-local'
  onCommit: (v: string) => void
}) {
  const [draft, setDraft] = useState(value || '')
  // 外部值變動（譬如切模板後重置）時同步 draft
  useEffect(() => { setDraft(value || '') }, [value])
  const dirty = draft !== (value || '')
  return (
    <div className="flex gap-1.5 items-center">
      <input
        className="flex-1 border border-gray-200 rounded-lg px-2.5 py-1.5 text-sm outline-none focus:border-sky-400 focus:ring-1 focus:ring-sky-400/20 bg-white"
        type={type}
        value={draft}
        onChange={e => setDraft(e.target.value)}
      />
      <button
        type="button"
        onClick={() => onCommit(draft)}
        disabled={!dirty}
        title={dirty ? '套用此日期' : '已套用'}
        className={`shrink-0 px-2.5 py-1.5 rounded-lg text-xs font-medium flex items-center gap-1 transition-colors ${
          dirty
            ? 'bg-sky-500 hover:bg-sky-600 text-white'
            : 'bg-gray-100 text-gray-400 cursor-not-allowed'
        }`}
      >
        <Check className="w-3.5 h-3.5" />
        {dirty ? '確定' : '已套用'}
      </button>
    </div>
  )
}

const COMPATIBILITY_DISMISS_KEY = 'outlook-compat-warning-dismissed-v1'

const NODE_COLOR = '#0078d4'

// ── 模板定義（顯示給使用者選的選單） ─────────────────────────────────
// 後端 agent 會看 template ID + params 組 prompt；前端只負責收參數。
type ParamSpec = {
  key: string
  label: string
  type: 'text' | 'textarea' | 'date' | 'datetime-local' | 'number' | 'select' | 'bool'
  placeholder?: string
  options?: { value: string; label: string }[]
  hint?: string
}

type Template = {
  id: string
  label: string
  category: 'inbox' | 'send' | 'attach' | 'manage'
  description: string
  params: ParamSpec[]
  // execMode: 'direct' = 後端直接 call wrapper、不進 LLM（快、零 token、可預測）
  //          'llm'    = 進 LLM agent loop（需要摘要 / 分析時才用）
  execMode: 'direct' | 'llm'
}

const TEMPLATES: Template[] = [
  // A. 每日整理 / 摘要
  {
    id: 'daily_todo',
    label: '🗒 整理符合條件信件 → 待辦清單',
    category: 'inbox',
    execMode: 'direct',
    description: '掃指定資料夾的信，按條件過濾，結果整理成 markdown / xlsx 待辦清單',
    params: [
      { key: 'folder', label: '資料夾', type: 'text', placeholder: 'inbox / 收件匣 / Inbox/Projects', hint: '預設 inbox' },
      { key: 'subject', label: '主旨關鍵字（多個用逗號）', type: 'text', placeholder: '報告, urgent' },
      { key: 'sender', label: '寄件人（多個用逗號）', type: 'text', placeholder: 'boss@x.com' },
      { key: 'exact_match', label: '完全相等比對（不勾 = 模糊比對）', type: 'bool' },
      { key: 'since', label: '從', type: 'datetime-local' },
      { key: 'until', label: '到', type: 'datetime-local' },
      { key: 'unread_only', label: '只取未讀', type: 'bool' },
      { key: 'output_format', label: '輸出格式', type: 'select',
        options: [{ value: 'md', label: 'Markdown' }, { value: 'xlsx', label: 'Excel' }, { value: 'txt', label: '純文字' }] },
    ],
  },
  {
    id: 'search_summary',
    label: '🔍 指定關鍵字撈相關信件 → 摘要報告',
    category: 'inbox',
    execMode: 'llm',
    description: '用 LLM 摘要符合條件的信件群、產出報告',
    params: [
      { key: 'keywords', label: '關鍵字（多個用逗號 = OR 邏輯）', type: 'text', placeholder: '客戶投訴, 退費' },
      { key: 'search_in', label: '搜尋範圍', type: 'select',
        options: [{ value: 'subject', label: '主旨' }, { value: 'body', label: '本文' }, { value: 'both', label: '主旨+本文' }] },
      { key: 'folder', label: '資料夾', type: 'text', placeholder: 'inbox', hint: '預設 inbox' },
      { key: 'since', label: '從', type: 'datetime-local' },
      { key: 'until', label: '到', type: 'datetime-local' },
      { key: 'detail_level', label: '報告詳細度', type: 'select',
        options: [{ value: 'brief', label: '簡' }, { value: 'medium', label: '中' }, { value: 'detail', label: '詳' }] },
      { key: 'output_format', label: '輸出格式', type: 'select',
        options: [{ value: 'md', label: 'Markdown' }, { value: 'docx', label: 'Word' }, { value: 'pdf', label: 'PDF' }] },
    ],
  },
  {
    id: 'unanswered',
    label: '❓ 未回覆超過 N 天的信',
    category: 'inbox',
    execMode: 'llm',
    description: '找出收件匣中我還沒回過、且收件超過指定天數的信',
    params: [
      { key: 'days', label: '超過幾天未回', type: 'number', placeholder: '3' },
      { key: 'sender_filter', label: '寄件人過濾（可選）', type: 'text', placeholder: '只看特定人' },
    ],
  },
  // B. 寄信
  {
    id: 'send_mail',
    label: '✉ 寄信給指定收件人',
    category: 'send',
    execMode: 'direct',
    description: '直接寄一封信',
    params: [
      { key: 'to', label: 'To（多個用逗號）', type: 'text', placeholder: 'a@x.com, b@x.com' },
      { key: 'cc', label: 'CC（可選）', type: 'text' },
      { key: 'bcc', label: 'BCC（可選）', type: 'text' },
      { key: 'subject', label: '主旨', type: 'text' },
      { key: 'body', label: '本文（HTML / 純文字）', type: 'textarea' },
      { key: 'body_format', label: '本文格式', type: 'select',
        options: [{ value: 'html', label: 'HTML' }, { value: 'text', label: '純文字' }] },
      { key: 'attachments', label: '附件路徑（多個用換行）', type: 'textarea',
        hint: '可填 {prev_output} 接前一步驟輸出檔' },
      { key: 'save_to_drafts', label: '只存草稿不送出', type: 'bool' },
    ],
  },
  {
    id: 'send_with_attachment',
    label: '📤 把上一步輸出（或指定檔案）當附件寄出',
    category: 'send',
    execMode: 'direct',
    description: '常用情境：前一步整理產出 xlsx → 直接寄給主管。也可以填路徑寄任何檔案',
    params: [
      { key: 'to', label: 'To', type: 'text' },
      { key: 'subject', label: '主旨', type: 'text' },
      { key: 'body', label: '本文（簡短說明）', type: 'textarea' },
      { key: 'attachment_path', label: '附件路徑（留空 = 上一步輸出）', type: 'text',
        placeholder: '留空自動用上一步；或填路徑（支援 {prev_output}）',
        hint: '相對路徑以專案根為基準；可填 {prev_output} 接前一步輸出檔' },
    ],
  },
  {
    id: 'bulk_send',
    label: '📨 從 csv/xlsx 收件清單群發',
    category: 'send',
    execMode: 'direct',
    description: '收件清單一筆一封，主旨/本文可帶 {欄位名} 變數',
    params: [
      { key: 'recipient_file', label: '收件清單檔', type: 'text', placeholder: 'recipients.csv' },
      { key: 'subject_template', label: '主旨範本', type: 'text', placeholder: 'Hi {name}, 請查收' },
      { key: 'body_template', label: '本文範本', type: 'textarea' },
    ],
  },
  // C. 附件
  {
    id: 'download_attachments',
    label: '📎 批次下載符合條件信件的附件',
    category: 'attach',
    execMode: 'direct',
    description: '把搜到的信件附件全部存到資料夾，可自訂檔名規則',
    params: [
      { key: 'subject', label: '主旨關鍵字', type: 'text' },
      { key: 'sender', label: '寄件人', type: 'text' },
      { key: 'since', label: '從', type: 'datetime-local' },
      { key: 'until', label: '到', type: 'datetime-local' },
      { key: 'out_dir', label: '目標資料夾', type: 'text', placeholder: 'D:/downloads/...' },
      { key: 'extensions', label: '檔案類型（留空=全部）', type: 'text',
        placeholder: 'pdf, xlsx, zip',
        hint: '逗號分隔副檔名（可帶或不帶 .，不分大小寫）；留空抓所有附件' },
      { key: 'name_template', label: '檔名範本', type: 'text',
        placeholder: '{date}_{sender}_{filename}',
        hint: '可用變數：{date} {sender} {subject} {filename}' },
    ],
  },
  // E. 信件管理（批次 move / mark / flag）
  {
    id: 'bulk_move',
    label: '📂 批次搬信到指定資料夾',
    category: 'manage',
    execMode: 'direct',
    description: '搜出符合條件的信件、批次搬到目標資料夾',
    params: [
      { key: 'folder', label: '來源資料夾', type: 'text', placeholder: 'inbox', hint: '預設 inbox' },
      { key: 'subject', label: '主旨關鍵字（可選）', type: 'text' },
      { key: 'sender', label: '寄件人（可選）', type: 'text' },
      { key: 'since', label: '從（可選）', type: 'datetime-local' },
      { key: 'until', label: '到（可選）', type: 'datetime-local' },
      { key: 'target_folder', label: '目標資料夾', type: 'text',
        placeholder: 'Inbox/Projects/2026',
        hint: '可寫別名（inbox / 收件匣）或路徑（Inbox/Projects/2026）' },
      { key: 'limit', label: '最多處理幾封', type: 'number', placeholder: '500' },
    ],
  },
  {
    id: 'bulk_mark_read',
    label: '✅ 批次標已讀／未讀',
    category: 'manage',
    execMode: 'direct',
    description: '搜出符合條件的信件、批次設為已讀或未讀',
    params: [
      { key: 'folder', label: '資料夾', type: 'text', placeholder: 'inbox' },
      { key: 'subject', label: '主旨關鍵字（可選）', type: 'text' },
      { key: 'sender', label: '寄件人（可選）', type: 'text' },
      { key: 'since', label: '從（可選）', type: 'datetime-local' },
      { key: 'until', label: '到（可選）', type: 'datetime-local' },
      { key: 'state', label: '目標狀態', type: 'select',
        options: [{ value: 'read', label: '標已讀' }, { value: 'unread', label: '標未讀' }] },
      { key: 'limit', label: '最多處理幾封', type: 'number', placeholder: '500' },
    ],
  },
  {
    id: 'bulk_set_flag',
    label: '🚩 批次設旗標 / 標完成 / 清除',
    category: 'manage',
    execMode: 'direct',
    description: '搜出符合條件的信件、批次加追蹤旗標、標完成或清除',
    params: [
      { key: 'folder', label: '資料夾', type: 'text', placeholder: 'inbox' },
      { key: 'subject', label: '主旨關鍵字（可選）', type: 'text' },
      { key: 'sender', label: '寄件人（可選）', type: 'text' },
      { key: 'since', label: '從（可選）', type: 'datetime-local' },
      { key: 'until', label: '到（可選）', type: 'datetime-local' },
      { key: 'flag', label: '旗標', type: 'select',
        options: [
          { value: 'follow_up', label: '🚩 追蹤（紅旗）' },
          { value: 'complete', label: '✓ 已完成' },
          { value: 'clear', label: '清除旗標' },
        ] },
      { key: 'limit', label: '最多處理幾封', type: 'number', placeholder: '500' },
    ],
  },
]

const CATEGORY_LABEL: Record<Template['category'], string> = {
  inbox: '📥 收信整理',
  send: '📤 寄信',
  attach: '📎 附件',
  manage: '🗂 信件管理',
}

interface Props {
  node: OutlookNode
  pipelineName: string
  onUpdate: (data: Partial<OutlookData>) => void
  onClose: () => void
  onDelete: () => void
}

export default function OutlookPanel({ node, onUpdate, onClose, onDelete }: Props) {
  const data = node.data
  const inputCls = 'w-full border border-gray-200 rounded-lg px-2.5 py-1.5 text-sm outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500/20 bg-white'


  // 相容性警告：只支援傳統 Outlook（New Outlook for Windows / Web 不支援 COM）
  // 使用者讀過、按「我知道了」後存到 localStorage 不再顯示，但摘要列永遠保留
  const [warnDismissed, setWarnDismissed] = useState(() => {
    if (typeof window === 'undefined') return false
    return localStorage.getItem(COMPATIBILITY_DISMISS_KEY) === '1'
  })
  const dismissWarn = () => {
    try { localStorage.setItem(COMPATIBILITY_DISMISS_KEY, '1') } catch {/* ignore */}
    setWarnDismissed(true)
  }

  // 連線測試 — 直接打 backend /outlook/test-connection 看 inbox 有幾封
  const [testing, setTesting] = useState(false)
  const runConnectionTest = async () => {
    if (testing) return
    setTesting(true)
    try {
      const res = await testOutlookConnection()
      if (res.ok && res.inbox_count && res.inbox_count > 0) {
        toast.success(res.diagnosis, { duration: 8000 })
      } else if (res.ok) {
        // COM 通了但 inbox = 0
        toast.warning(res.diagnosis, { duration: 12000 })
      } else {
        toast.error(res.diagnosis + (res.error ? `\n\n錯誤：${res.error}` : ''), { duration: 12000 })
      }
    } catch (e) {
      toast.error(`測試失敗：${(e as Error).message}`, { duration: 8000 })
    } finally {
      setTesting(false)
    }
  }

  const setParam = (key: string, value: unknown) => {
    onUpdate({ params: { ...data.params, [key]: value } })
  }

  // 點選模板：清掉舊參數、清掉自由輸入（避免兩個來源混淆）
  const selectTemplate = (id: string) => {
    onUpdate({ template: id, params: {}, freeText: '' })
  }

  const clearTemplate = () => {
    onUpdate({ template: '', params: {} })
  }

  const renderParam = (p: ParamSpec) => {
    const v = data.params?.[p.key]
    if (p.type === 'textarea') {
      return (
        <textarea
          className={`${inputCls} font-mono`} rows={4}
          placeholder={p.placeholder}
          value={(v as string) || ''}
          onChange={e => setParam(p.key, e.target.value)}
        />
      )
    }
    if (p.type === 'select' && p.options) {
      return (
        <select className={inputCls} value={(v as string) || ''} onChange={e => setParam(p.key, e.target.value)}>
          <option value="">（未選）</option>
          {p.options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
      )
    }
    if (p.type === 'bool') {
      return (
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={!!v} onChange={e => setParam(p.key, e.target.checked)} />
          <span>{p.label}</span>
        </label>
      )
    }
    if (p.type === 'date' || p.type === 'datetime-local') {
      return (
        <DateTimeField
          value={(v as string) || ''}
          type={p.type}
          onCommit={(val) => setParam(p.key, val)}
        />
      )
    }
    return (
      <input
        className={inputCls}
        type={p.type === 'number' ? 'number' : p.type}
        placeholder={p.placeholder}
        value={(v as string | number) ?? ''}
        onChange={e => {
          const raw = e.target.value
          setParam(p.key, p.type === 'number' ? (raw === '' ? '' : Number(raw)) : raw)
        }}
      />
    )
  }

  return (
    <div className="absolute top-0 right-0 h-full w-[420px] bg-white shadow-2xl border-l border-gray-100 flex flex-col z-30 overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-3.5 border-b" style={{ borderTopColor: NODE_COLOR, borderTopWidth: 3 }}>
        <span className="w-8 h-8 rounded-full flex items-center justify-center text-white text-sm font-bold shrink-0"
          style={{ background: NODE_COLOR }}>📧</span>
        <div className="flex-1 min-w-0">
          <span className="font-semibold text-gray-800 text-sm block truncate">Outlook 自動化節點</span>
          <span className="text-xs text-gray-400">透過 pywin32 + Outlook COM；只在 Windows host 跑</span>
        </div>
        <button onClick={onDelete} title="刪除" className="text-gray-300 hover:text-red-400 transition-colors p-1">🗑</button>
        <button onClick={onClose} className="text-gray-400 hover:text-gray-600 transition-colors"><X className="w-4 h-4" /></button>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {/* ── 相容性警告：只支援 Classic Outlook ────────────────────── */}
        {!warnDismissed ? (
          <div className="p-3 rounded-lg border border-amber-300 bg-amber-50 space-y-2">
            <div className="flex items-start gap-2">
              <AlertTriangle className="w-4 h-4 text-amber-600 shrink-0 mt-0.5" />
              <div className="flex-1 min-w-0">
                <p className="text-xs font-semibold text-amber-900">⚠ 開始前請先確認 Outlook 版本</p>
                <p className="text-[11px] text-amber-800 mt-1 leading-relaxed">
                  本節點透過 <code className="font-mono bg-amber-100 px-1 rounded">pywin32 + Outlook COM</code> 操作，
                  <b>只支援傳統 Outlook（Classic Outlook）</b>，不支援新版 Outlook for Windows、Outlook 網頁版、Outlook 行動版。
                </p>
                <p className="text-[11px] text-amber-900 mt-1.5 leading-relaxed bg-amber-100/60 px-2 py-1 rounded">
                  💡 若你用的是新版 Outlook：點上方「<b>說明</b>」分頁 → 右邊最後一個按鈕「<b>前往傳統 Outlook</b>」即可切回（保留同個帳號）。
                </p>
                <p className="text-[11px] text-amber-800 mt-1.5 leading-relaxed">按下方按鈕測試你的環境是否可用。</p>
              </div>
            </div>
            <div className="flex items-center gap-2 pt-1">
              <button
                onClick={runConnectionTest}
                disabled={testing}
                className="flex items-center gap-1.5 px-3 py-1.5 bg-amber-600 hover:bg-amber-700 disabled:opacity-50 text-white rounded-lg text-xs font-medium transition-colors"
              >
                {testing ? <Loader2 className="w-3 h-3 animate-spin" /> : <span>🧪</span>}
                {testing ? '測試中…' : '測試 Outlook 連線'}
              </button>
              <button
                onClick={dismissWarn}
                className="text-[11px] text-amber-700 hover:bg-amber-200 px-2 py-0.5 rounded transition-colors"
              >
                我知道了，不再顯示
              </button>
            </div>
          </div>
        ) : (
          <div className="px-2 py-1.5 rounded-lg bg-gray-50 border border-gray-200 flex items-center gap-2">
            <AlertTriangle className="w-3 h-3 text-amber-500 shrink-0" />
            <span className="text-[11px] text-gray-500 flex-1">只支援 Classic Outlook</span>
            <button
              onClick={runConnectionTest}
              disabled={testing}
              className="text-[11px] text-blue-600 hover:underline disabled:opacity-50 shrink-0"
            >
              {testing ? '測試中…' : '🧪 測試連線'}
            </button>
            <button
              onClick={() => setWarnDismissed(false)}
              className="text-[11px] text-gray-400 hover:underline shrink-0"
            >
              詳情
            </button>
          </div>
        )}

        {/* Name */}
        <div>
          <label className="text-xs font-semibold text-gray-500 uppercase tracking-wide block mb-1.5">節點名稱</label>
          <input value={data.name} onChange={e => onUpdate({ name: e.target.value })} className={`${inputCls} font-mono`} />
        </div>

        {/* 模式說明 */}
        <div className="p-3 rounded-lg border border-blue-200 bg-blue-50/50 space-y-1.5">
          <p className="text-xs text-blue-900 font-medium">執行路徑分兩種，各有適用情境：</p>
          <p className="text-[11px] text-blue-800/90 leading-relaxed">
            <span className="inline-block w-2 h-2 rounded-full bg-emerald-500 mr-1.5 align-middle" />
            <b>🚀 直接執行</b>：不進 LLM、後端直接呼叫 Outlook API，快、零 token、結果可預測
          </p>
          <p className="text-[11px] text-blue-800/90 leading-relaxed">
            <span className="inline-block w-2 h-2 rounded-full bg-purple-500 mr-1.5 align-middle" />
            <b>🤖 AI 處理</b>：進 LLM agent loop，會花 token、可能多輪 retry，適合需要摘要 / 分析的情境
          </p>
        </div>

        {/* ── 🚀 直接執行區（無需 LLM）──────────────────────────── */}
        <div>
          <div className="flex items-center justify-between mb-2">
            <label className="text-xs font-semibold text-emerald-700 uppercase tracking-wide flex items-center gap-1">
              🚀 直接執行（無需 LLM）
            </label>
            {data.template && TEMPLATES.find(t => t.id === data.template)?.execMode === 'direct' && (
              <button onClick={clearTemplate} className="text-xs text-blue-600 hover:underline">清除選擇</button>
            )}
          </div>
          {(['inbox', 'send', 'attach', 'manage'] as const).map(cat => {
            const directInCat = TEMPLATES.filter(t => t.category === cat && t.execMode === 'direct')
            if (directInCat.length === 0) return null
            return (
              <div key={cat} className="mb-3">
                <p className="text-[11px] font-semibold text-gray-500 mb-1">{CATEGORY_LABEL[cat]}</p>
                <div className="space-y-1">
                  {directInCat.map(t => {
                    const isSelected = data.template === t.id
                    return (
                      <div key={t.id} className="space-y-0">
                        <button
                          onClick={() => selectTemplate(t.id)}
                          className={`w-full text-left px-2.5 py-1.5 text-xs border transition-colors ${
                            isSelected
                              ? 'bg-emerald-500 text-white border-emerald-500 rounded-t-lg'
                              : 'bg-white text-gray-700 border-gray-200 hover:border-emerald-300 rounded-lg'
                          }`}
                          title={t.description}
                        >
                          {t.label}
                        </button>
                        {isSelected && (
                          <div className="px-3 py-3 rounded-b-lg border border-t-0 border-emerald-500 bg-emerald-50/30 space-y-3">
                            <p className="text-[11px] text-gray-600 leading-relaxed">{t.description}</p>
                            {t.params.map(p => (
                              <div key={p.key}>
                                {p.type !== 'bool' && (
                                  <label className="text-xs text-gray-600 block mb-1">{p.label}</label>
                                )}
                                {renderParam(p)}
                                {p.hint && <p className="text-[10px] text-gray-400 mt-0.5">{p.hint}</p>}
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              </div>
            )
          })}
        </div>

        {/* ── 🤖 AI 處理區（LLM 模板 + 自由輸入）─────────────────── */}
        <div>
          <div className="flex items-center justify-between mb-2">
            <label className="text-xs font-semibold text-purple-700 uppercase tracking-wide flex items-center gap-1">
              🤖 AI 處理（需要 LLM、會花 token）
            </label>
            {data.template && TEMPLATES.find(t => t.id === data.template)?.execMode === 'llm' && (
              <button onClick={clearTemplate} className="text-xs text-blue-600 hover:underline">清除選擇</button>
            )}
          </div>
          {(['inbox', 'send', 'attach', 'manage'] as const).map(cat => {
            const llmInCat = TEMPLATES.filter(t => t.category === cat && t.execMode === 'llm')
            if (llmInCat.length === 0) return null
            return (
              <div key={cat} className="mb-3">
                <p className="text-[11px] font-semibold text-gray-500 mb-1">{CATEGORY_LABEL[cat]}</p>
                <div className="space-y-1">
                  {llmInCat.map(t => {
                    const isSelected = data.template === t.id
                    return (
                      <div key={t.id} className="space-y-0">
                        <button
                          onClick={() => selectTemplate(t.id)}
                          className={`w-full text-left px-2.5 py-1.5 text-xs border transition-colors ${
                            isSelected
                              ? 'bg-purple-500 text-white border-purple-500 rounded-t-lg'
                              : 'bg-white text-gray-700 border-gray-200 hover:border-purple-300 rounded-lg'
                          }`}
                          title={t.description}
                        >
                          {t.label}
                        </button>
                        {isSelected && (
                          <div className="px-3 py-3 rounded-b-lg border border-t-0 border-purple-500 bg-purple-50/30 space-y-3">
                            <p className="text-[11px] text-gray-600 leading-relaxed">{t.description}</p>
                            {t.params.map(p => (
                              <div key={p.key}>
                                {p.type !== 'bool' && (
                                  <label className="text-xs text-gray-600 block mb-1">{p.label}</label>
                                )}
                                {renderParam(p)}
                                {p.hint && <p className="text-[10px] text-gray-400 mt-0.5">{p.hint}</p>}
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              </div>
            )
          })}

          {/* 自由輸入：屬於 AI 處理區的最後一塊 */}
          <div className="mb-3">
            <p className="text-[11px] font-semibold text-gray-500 mb-1">✏️ 自由輸入需求</p>
            <p className="text-[10px] text-gray-500 mb-1.5 leading-relaxed">
              不在上面選單裡的需求，直接打字描述。agent 限定使用 pywin32 + Outlook COM；做不到會回報無法執行、不會 fallback 到其他工具。
            </p>
            <textarea
              className={`${inputCls} font-mono ${data.template ? 'opacity-50' : ''}`}
              rows={4}
              placeholder="範例：把昨天到今天主旨含『發票』的信件附件全部存到 D:/invoices，並寄一封摘要給 a@x.com"
              value={data.freeText}
              disabled={!!data.template}
              onChange={e => onUpdate({ freeText: e.target.value })}
            />
            {data.template && (
              <p className="text-[10px] text-gray-400 mt-1">已選模板「{TEMPLATES.find(t => t.id === data.template)?.label}」，自由輸入暫時不啟用。</p>
            )}
          </div>
        </div>

        {/* 輸出檔（可選） */}
        <div>
          <label className="text-xs font-semibold text-gray-500 uppercase tracking-wide block mb-1.5">輸出檔路徑（可選）</label>
          <input
            className={`${inputCls} font-mono`}
            placeholder="ai_output/{name}/result.xlsx"
            value={data.outputPath}
            onChange={e => onUpdate({ outputPath: e.target.value })}
          />
          <p className="text-[10px] text-gray-400 mt-0.5">整理 / 摘要結果寫到這裡，下個節點可以讀</p>
        </div>

        {/* Retry */}
        <div>
          <label className="text-xs font-semibold text-gray-500 uppercase tracking-wide block mb-1.5">失敗重試次數</label>
          <input type="number" min={0} max={5}
            className={inputCls}
            value={data.retry}
            onChange={e => onUpdate({ retry: Number(e.target.value) || 0 })}
          />
        </div>

        {/* Timeout（秒）*/}
        <div>
          <label className="text-xs font-semibold text-gray-500 uppercase tracking-wide block mb-1.5">執行上限（秒）</label>
          <input type="number" min={60} max={7200} step={60}
            className={inputCls}
            value={typeof data.timeout === 'number' ? data.timeout : 600}
            onChange={e => onUpdate({ timeout: Number(e.target.value) || 600 })}
          />
          <p className="text-[10px] text-gray-400 mt-0.5">
            Outlook COM 對巨型收信夾 search_mail 可能跑 4-5 分鐘；30k+ 信箱建議 1800-3600
          </p>
        </div>
      </div>
    </div>
  )
}
