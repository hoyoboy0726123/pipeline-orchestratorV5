'use client'
import { X } from 'lucide-react'
import type { OutlookData, OutlookNode } from './_helpers'

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
  category: 'inbox' | 'send' | 'attach' | 'calendar'
  description: string
  params: ParamSpec[]
}

const TEMPLATES: Template[] = [
  // A. 每日整理 / 摘要
  {
    id: 'daily_todo',
    label: '🗒 整理符合條件信件 → 待辦清單',
    category: 'inbox',
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
    label: '📤 把上一步輸出當附件寄出',
    category: 'send',
    description: '常用情境：前一步整理產出 xlsx → 直接寄給主管',
    params: [
      { key: 'to', label: 'To', type: 'text' },
      { key: 'subject', label: '主旨', type: 'text' },
      { key: 'body', label: '本文（簡短說明）', type: 'textarea' },
    ],
  },
  {
    id: 'bulk_send',
    label: '📨 從 csv/xlsx 收件清單群發',
    category: 'send',
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
    description: '把搜到的信件附件全部存到資料夾，可自訂檔名規則',
    params: [
      { key: 'subject', label: '主旨關鍵字', type: 'text' },
      { key: 'sender', label: '寄件人', type: 'text' },
      { key: 'since', label: '從', type: 'datetime-local' },
      { key: 'until', label: '到', type: 'datetime-local' },
      { key: 'out_dir', label: '目標資料夾', type: 'text', placeholder: 'D:/downloads/...' },
      { key: 'name_template', label: '檔名範本', type: 'text',
        placeholder: '{date}_{sender}_{filename}',
        hint: '可用變數：{date} {sender} {subject} {filename}' },
    ],
  },
  // D. 行事曆
  {
    id: 'calendar_list',
    label: '📅 列出某時間範圍的會議',
    category: 'calendar',
    description: '回傳 DataFrame：主旨 / 起訖 / 地點 / 與會者 / 是否定期',
    params: [
      { key: 'since', label: '從', type: 'datetime-local' },
      { key: 'until', label: '到', type: 'datetime-local' },
      { key: 'output_format', label: '輸出格式', type: 'select',
        options: [{ value: 'md', label: 'Markdown 條列' }, { value: 'xlsx', label: 'Excel 表' }] },
    ],
  },
  {
    id: 'create_meeting',
    label: '🆕 新增會議邀請',
    category: 'calendar',
    description: '建立會議並（可選）發送邀請給與會者',
    params: [
      { key: 'subject', label: '主旨', type: 'text' },
      { key: 'start', label: '開始時間', type: 'datetime-local' },
      { key: 'end', label: '結束時間', type: 'datetime-local' },
      { key: 'location', label: '地點', type: 'text', placeholder: '會議室 / Zoom 連結' },
      { key: 'required_attendees', label: '必要與會者', type: 'text', placeholder: 'a@x.com, b@x.com' },
      { key: 'optional_attendees', label: '選擇性與會者', type: 'text' },
      { key: 'reminder_minutes', label: '提醒（分鐘）', type: 'number', placeholder: '15' },
      { key: 'body', label: '會議說明', type: 'textarea' },
      { key: 'send_invitation', label: '自動寄出邀請', type: 'bool' },
    ],
  },
]

const CATEGORY_LABEL: Record<Template['category'], string> = {
  inbox: '📥 收信整理',
  send: '📤 寄信',
  attach: '📎 附件',
  calendar: '📅 行事曆',
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

  const currentTemplate = TEMPLATES.find(t => t.id === data.template) || null

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
        {/* Name */}
        <div>
          <label className="text-xs font-semibold text-gray-500 uppercase tracking-wide block mb-1.5">節點名稱</label>
          <input value={data.name} onChange={e => onUpdate({ name: e.target.value })} className={`${inputCls} font-mono`} />
        </div>

        {/* 模式區：模板 vs 自由輸入 */}
        <div className="p-3 rounded-lg border border-blue-200 bg-blue-50/50 space-y-2">
          <p className="text-xs text-blue-900 font-medium">執行模式：選一個模板，或直接寫自由輸入需求</p>
          <p className="text-[11px] text-blue-700/80 leading-relaxed">
            模板 = 固定 prompt + 你填參數，agent 跑得最穩。
            自由輸入 = 你描述需求，agent 自己決定怎麼用 win32 工具。
            兩者擇一即可（選了模板就會清掉自由輸入）。
          </p>
        </div>

        {/* 模板選單 */}
        <div>
          <div className="flex items-center justify-between mb-2">
            <label className="text-xs font-semibold text-gray-500 uppercase tracking-wide">選單模板</label>
            {data.template && (
              <button onClick={clearTemplate} className="text-xs text-blue-600 hover:underline">清除模板</button>
            )}
          </div>
          {(['inbox', 'send', 'attach', 'calendar'] as const).map(cat => (
            <div key={cat} className="mb-3">
              <p className="text-[11px] font-semibold text-gray-500 mb-1">{CATEGORY_LABEL[cat]}</p>
              <div className="space-y-1">
                {TEMPLATES.filter(t => t.category === cat).map(t => (
                  <button key={t.id}
                    onClick={() => selectTemplate(t.id)}
                    className={`w-full text-left px-2.5 py-1.5 rounded-lg text-xs border transition-colors ${
                      data.template === t.id
                        ? 'bg-blue-500 text-white border-blue-500'
                        : 'bg-white text-gray-700 border-gray-200 hover:border-blue-300'
                    }`}
                    title={t.description}
                  >
                    {t.label}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>

        {/* 模板參數區 */}
        {currentTemplate && (
          <div className="p-3 rounded-lg border border-gray-200 bg-gray-50/50 space-y-3">
            <p className="text-xs font-semibold text-gray-700">{currentTemplate.label} — 參數</p>
            <p className="text-[11px] text-gray-500">{currentTemplate.description}</p>
            {currentTemplate.params.map(p => (
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

        {/* 自由輸入區（沒選模板時顯示） */}
        {!data.template && (
          <div>
            <label className="text-xs font-semibold text-gray-500 uppercase tracking-wide block mb-1.5">
              自由輸入需求（agent 限定使用 pywin32 + Outlook COM）
            </label>
            <textarea
              className={`${inputCls} font-mono`}
              rows={5}
              placeholder="範例：把昨天到今天主旨含『發票』的信件附件全部存到 D:/invoices，並寄一封摘要給 a@x.com"
              value={data.freeText}
              onChange={e => onUpdate({ freeText: e.target.value })}
            />
            <p className="text-[10px] text-gray-400 mt-1 leading-relaxed">
              做不到的需求（例如要連 Web API、操作 Slack）agent 會直接停下來回報「無法做到」，不會 fallback 到其他工具。
            </p>
          </div>
        )}

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
      </div>
    </div>
  )
}
