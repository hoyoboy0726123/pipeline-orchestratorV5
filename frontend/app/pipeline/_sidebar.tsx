'use client'
import { useState, useRef, useEffect } from 'react'
import {
  Plus, Workflow, X, Bot, ChevronUp, ChevronDown,
  Send, Loader2, Pencil, Check, Trash2, Settings, BookOpen,
  Download, Upload, Square,
} from 'lucide-react'
import Link from 'next/link'
import { toast } from 'sonner'
import ReactMarkdown from 'react-markdown'
import rehypeRaw from 'rehype-raw'
import { useWorkflowStore } from './_store'
import {
  pipelineChatStream, createWorkflowApi, exportWorkflowUrl, importWorkflow,
  getPipelineScheduled, getPipelineRuns, cancelPipelineSchedule,
  getEnvPaths, type EnvPaths,
  getWorkflowChat, appendWorkflowChat, clearWorkflowChat,
} from '@/lib/api'
import type { ScheduledTask } from '@/lib/types'

// ── AI Chat Message Type ─────────────────────────────────────────────────────
interface ToolBlock {
  name: string         // tool 名稱(list_workflows / get_run_log 等)
  args: Record<string, unknown>
  status: 'running' | 'done'
  preview?: string     // tool_end 時填、回傳前 200 字
}

interface ChatMsg {
  role: 'user' | 'assistant'
  content: string
  hasYaml?: boolean
  yaml?: string | null
  yamlError?: string | null
  toolBlocks?: ToolBlock[]   // 串流時顯示的 tool 呼叫紀錄
  streaming?: boolean        // 串流中(顯示游標 / 還在打字)
}

// 根據實際專案路徑組 AI 助手的初始訊息：範例使用真實可執行的腳本路徑，
// 輸出用相對 `ai_output/<name>/` 慣例。使用者可直接把範例描述貼給 AI 產生 YAML。
function buildWelcomeMessage(env: EnvPaths): string {
  const root = env.project_root
  const financeDir = env.finance_example_dir  // 例如 ".../test-workflows/finance"
  const intro = `你好！請告訴我你想自動化的工作流程，我會問你幾個關鍵問題、提一份步驟方案讓你點頭、再產生 Pipeline YAML 設定。

我會用到以下節點（依需要組合）：
- **腳本節點**：跑你已寫好的 .py / .bat / shell 指令
- **AI 技能節點**：用自然語言描述任務，LLM 自動寫 Python 跑（可掛 docx / pptx 等 Agent Skill 提升正確率）
- **多代理節點**：探索式 / 試錯式任務（5 個角色：分析師 / 工程師 / 研究員 / 審稿人 / 規劃師），每次都重新推理、適合「不確定怎麼做、邊想邊改」的任務；日常固定邏輯請用 AI 技能節點（更省 token）
- **人工確認節點**：暫停等你 Telegram 點頭再續跑（可自動把上一步輸出傳到手機）
- **網頁爬蟲節點**：丟 URL → markdown，支援 SPA、登入、Cloudflare bypass
- **影片下載節點**：YouTube / Vimeo / Bilibili 等
- **Outlook 自動化節點**：寄信、批次讀信、下載附件、行事曆（10 個內建模板）
- **視覺驗證節點**：用 VLM 看圖判斷產出符不符合預期
- 桌面自動化節點：UI 操作（要在畫布錄製動作，AI 沒辦法幫你寫）`
  const pathNote = `📁 **輸出路徑慣例**：所有產出檔會放在 \`ai_output/<工作流名稱>/\` 子資料夾（系統自動解析到 \`${root}\`）。`

  // 範例 1：Python 腳本串接（用專案內建的 finance 範例腳本，若存在）
  let ex1: string
  if (env.has_finance_example && financeDir) {
    ex1 = `**範例 1（Python 腳本串接・使用本專案內建的財務範例）**
第一步：執行 \`python ${financeDir}\\stage1_generate_transactions.py\`，輸出到 \`ai_output/q1_finance/raw_transactions.xlsx\`
第二步：執行 \`python ${financeDir}\\stage2_clean_data.py\`，讀取上一步的 Excel，輸出到 \`ai_output/q1_finance/cleaned_transactions.xlsx\`
第三步：執行 \`python ${financeDir}\\stage3_analyze_finance.py\`，做財務彙總，輸出到 \`ai_output/q1_finance/financial_summary.xlsx\`
第四步：執行 \`python ${financeDir}\\stage4_generate_report.py\`，產出 \`ai_output/q1_finance/Q1_financial_report.xlsx\``
  } else {
    ex1 = `**範例 1（Python 腳本串接）**
第一步：執行 \`python 你的腳本.py\`，輸出到 \`ai_output/daily_report/raw.csv\`
第二步：執行 \`python 分析腳本.py\`，讀取上一步的 csv，輸出到 \`ai_output/daily_report/result.xlsx\``
  }

  // 範例 2：script + AI skill（純自然語言，讓使用者無腦可用）
  let ex2: string
  if (env.has_finance_example && financeDir) {
    ex2 = `**範例 2（Python 腳本 + AI 技能）**
第一步（Python 腳本）：執行 \`python ${financeDir}\\stage1_generate_transactions.py\`，產出 \`ai_output/demo_beautify/raw_transactions.xlsx\`
第二步（AI 技能）：把上一步產生的 Excel 美化一下 — 表頭加粗、換底色、每欄寬度自動配合內容，另存為 \`ai_output/demo_beautify/pretty.xlsx\``
  } else {
    ex2 = `**範例 2（AI 技能）**
把 \`ai_output/some_input/report.xlsx\`（或上一步產生的檔案）美化一下 — 表頭加粗、每欄寬度自動配合內容，儲存到 \`ai_output/excel_beautify/formatted_report.xlsx\``
  }

  // 範例 3：script + AI skill + human_confirm（在範例 2 基礎上加人工審核）
  let ex3: string
  if (env.has_finance_example && financeDir) {
    ex3 = `**範例 3（三種節點組合・Python + AI + 人工確認）**
第一步（Python 腳本）：執行 \`python ${financeDir}\\stage1_generate_transactions.py\` 產出 \`ai_output/demo_review/raw_transactions.xlsx\`
第二步（AI 技能）：讀取上一步的 Excel，按「部門」加總 Amount，產出一份欄位為「Department, TotalAmount, TransactionCount」的摘要 Excel：\`ai_output/demo_review/summary.xlsx\`
第三步（人工確認）：暫停並透過 Telegram 通知我檢查摘要表，確認後才完成`
  } else {
    ex3 = `**範例 3（Python + AI + 人工確認 組合）**
第一步（Python 腳本）：執行你的腳本，產出 \`ai_output/demo_review/raw.xlsx\`
第二步（AI 技能）：讀取上一步做簡易統計，輸出 \`ai_output/demo_review/summary.xlsx\`
第三步（人工確認）：暫停並透過 Telegram 通知我檢查摘要表`
  }

  // 範例 4：web_crawler + skill + human_confirm + outlook_automation（V5 完整鏈）
  const ex4 = `**範例 4（網頁爬蟲 + AI 摘要 + 人工把關 + Outlook 寄信）**
第一步（網頁爬蟲）：抓 \`https://www.reddit.com/r/ASUS/\` 列表頁
第二步（AI 技能）：抽前 10 篇連結各自展開抓內文，每篇 80 字內摘要，輸出 \`ai_output/reddit_asus/daily.md\`
第三步（人工確認）：把摘要傳到 Telegram，我看過 OK 才繼續
第四步（Outlook）：把 daily.md 當附件用 send_with_attachment 模板寄給 boss@x.com`

  // 範例 5：skill + visual_validation（產出後 VLM 看圖驗證）
  const ex5 = `**範例 5（AI 技能 + 視覺驗證）**
第一步（AI 技能）：讀 \`ai_output/q1_finance/raw.xlsx\`，做透視表加長條圖，輸出到 \`ai_output/q1_finance/dashboard.xlsx\`
第二步（視覺驗證）：檢查 dashboard.xlsx 的畫面 — 應該看到一張表頭加粗、欄寬對齊、含長條圖的儀表板`

  // 範例 5 補上「需要 vision 模型」的提示，避免新使用者誤用
  const ex5Note = '\n\n> ℹ️ 此範例會用「視覺驗證節點」、需 LLM 模型支援視覺輸入（如 Llama 4 Scout / Gemini 2.5 / GPT-4o）。沒設或不支援會友善提示。'

  // 範例 6：啟動既有 Python 專案 + AI 驗證 + 人工確認（故意不寫專案路徑，逼 AI 助手反問）
  const ex6 = `**範例 6（啟動既有 Python 專案 + AI 驗證 + 人工確認）**
我有一個 Python 專案，想接到工作流自動化跑：
1. 跑專案的 main.py、產出檔案到工作流目錄
2. AI 驗證一下產出檔內容對不對
3. 確認沒問題後 Telegram 通知我做最終放行`

  const ex6Note = '\n\n> 📁 **記得把專案放在** `external_projects/<你的專案名>/`（本專案根目錄底下），AI 助手才能找到並改寫。\n> 若你描述時沒提到路徑，AI 助手會反問你。\n> 若 main.py 是 GUI / 含 `input()` 互動阻塞，AI 技能會自動先 read_file 看源碼、把互動點改成命令列參數版本再跑。'

  // 用 HTML <details>/<summary> 包每個範例，瀏覽器原生摺疊；ReactMarkdown 開了
  // rehypeRaw 才會 render 這些 HTML 標籤。預設收起，點擊展開、不佔螢幕。
  // summary 用一行有 emoji 的標題，內容用 markdown body（rehype-raw 會繼續解析裡面的 markdown）
  const wrap = (title: string, body: string) =>
    `<details><summary><strong>${title}</strong></summary>\n\n${body}\n\n</details>`

  // 範例 7：subagent — 探索式分析（沒固定流程、要邊想邊改）
  const ex7 = `**範例 7（多代理 — 探索式分析）**
任務：「我有 \`sales.xlsx\`，想看看 Q1 哪幾個品類賣最差、找出共通原因」這種「**不確定要看什麼指標、邊看邊找**」的場景。
單一步驟：用多代理節點、角色挑「**資料分析師（data_analyst）**」、最多輪數設 6-8、任務描述寫清楚目標即可（不用拆步驟）。多代理會自己 read → run_python → 看結果 → 再 read… 直到產出 \`analysis.md\`。`
  const ex7Note = '\n\n> 🧠 **何時用多代理 vs AI 技能**：每天跑、邏輯固定（讀 X 算 Y 寫 Z）→ 用 **AI 技能 + Recipe**（第二次起零 token）；結構不固定、邊想邊改、要試錯（探索/研究/debug）→ 用 **多代理**（每次重新推理、能根據中間結果調整、token 用量是 skill 的 2-5 倍）。'

  const examples = [
    wrap('📋 範例 1：Python 腳本串接', ex1),
    wrap('📋 範例 2：Python 腳本 + AI 技能', ex2),
    wrap('📋 範例 3：Python + AI + 人工確認', ex3),
    wrap('📋 範例 4：網頁爬蟲 + AI 摘要 + 人工把關 + Outlook 寄信', ex4),
    wrap('📋 範例 5：AI 技能 + 視覺驗證', ex5 + ex5Note),
    wrap('📋 範例 6：啟動既有 Python 專案 + AI 驗證 + 人工確認', ex6 + ex6Note),
    wrap('📋 範例 7：多代理 — 探索式分析', ex7 + ex7Note),
  ]
  const examplesHeader = '\n\n📋 **範例參考**（點任一行展開查看細節，可直接抄走給我作為起點）：'

  return [intro, pathNote, examplesHeader, ...examples].join('\n\n')
}

// ── LaTeX → 純文字 / Unicode 清洗 ───────────────────────────────────────────
// LLM 偶爾會用 LaTeX 數學語法（$\rightarrow$ / $N$ / $\Rightarrow$），這個聊天 UI
// 沒裝 KaTeX、ReactMarkdown 會原字顯示一坨 "$\rightarrow$" 很醜。
// 在渲染前用 regex 把常見 LaTeX 命令換成 Unicode 對應字元；不做完整 LaTeX 解析、
// 沒 cover 的 case 至少把錢字號去掉、字母 / 命令裸露出來、可讀。
const _LATEX_CMD_TO_UNICODE: Record<string, string> = {
  rightarrow: '→', leftarrow: '←', Rightarrow: '⇒', Leftarrow: '⇐',
  to: '→', gets: '←', leftrightarrow: '↔', Leftrightarrow: '⇔',
  uparrow: '↑', downarrow: '↓', updownarrow: '↕',
  times: '×', div: '÷', pm: '±', mp: '∓',
  cdot: '·', cdots: '⋯', ldots: '…', dots: '…',
  leq: '≤', le: '≤', geq: '≥', ge: '≥', neq: '≠', ne: '≠',
  approx: '≈', equiv: '≡',
  alpha: 'α', beta: 'β', gamma: 'γ', delta: 'δ', epsilon: 'ε',
  theta: 'θ', lambda: 'λ', mu: 'μ', pi: 'π', sigma: 'σ', tau: 'τ',
  phi: 'φ', omega: 'ω',
  infty: '∞', forall: '∀', exists: '∃', in: '∈', notin: '∉',
  subset: '⊂', supset: '⊃', cup: '∪', cap: '∩',
  text: '', mathrm: '', mathbf: '', mathit: '',
}

function cleanLatexInChat(text: string): string {
  if (!text || (!text.includes('$') && !text.includes('\\'))) return text
  // 1. inline math $...$ → 內容（剝掉 $ + 解析 LaTeX 命令）
  let out = text.replace(/\$([^\$\n]+?)\$/g, (_m, body: string) => {
    return body.replace(/\\([a-zA-Z]+)/g, (_full, cmd: string) =>
      _LATEX_CMD_TO_UNICODE[cmd] !== undefined ? _LATEX_CMD_TO_UNICODE[cmd] : cmd
    ).trim()
  })
  // 2. 在 $ 外面也可能有裸的 \rightarrow（沒包 $）→ 一起處理
  out = out.replace(/\\([a-zA-Z]+)/g, (m, cmd: string) =>
    _LATEX_CMD_TO_UNICODE[cmd] !== undefined ? _LATEX_CMD_TO_UNICODE[cmd] : m
  )
  return out
}

// ── Countdown Hook ──────────────────────────────────────────────────────────
function useCountdown(nextRun: string | null) {
  const [text, setText] = useState('')
  useEffect(() => {
    if (!nextRun) { setText(''); return }
    const calc = () => {
      // 解析日期並檢查有效性
      const targetDate = new Date(nextRun)
      const now = new Date()
      
      if (isNaN(targetDate.getTime())) { 
        setText('')
        return 
      }
      
      let diff = targetDate.getTime() - now.getTime()
      
      // 如果 diff 為負但絕對值很小（10秒內），視為即將執行
      if (diff <= 0) {
        if (diff > -10000) setText('即將執行…')
        else setText('') 
        return
      }
      
      const h = Math.floor(diff / 3600000)
      const m = Math.floor((diff % 3600000) / 60000)
      const s = Math.floor((diff % 60000) / 1000)
      
      if (h > 24) setText('1天以上')
      else if (h > 0) setText(`${h}時${m}分後執行`)
      else if (m > 0) setText(`${m}分${s}秒後執行`)
      else setText(`${s}秒後執行`)
    }
    calc()
    const iv = setInterval(calc, 1000)
    return () => clearInterval(iv)
  }, [nextRun])
  return text
}

// ── Workflow List Item ───────────────────────────────────────────────────────
function WorkflowItem({
  id, name, active, updatedAt, nextRun, runStatus,
  onSelect, onRename, onDelete, onExport,
}: {
  id: string; name: string; active: boolean; updatedAt: number; nextRun: string | null
  runStatus: 'idle' | 'running' | 'completed' | 'failed' | null
  onSelect: () => void
  onRename: (n: string) => void
  onDelete: () => void
  onExport: () => void
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft]     = useState(name)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => { if (editing) inputRef.current?.focus() }, [editing])
  useEffect(() => { setDraft(name) }, [name])

  const commit = () => {
    const newName = draft.trim()
    if (!newName || newName === name) { setEditing(false); return }
    const ok = confirm(
      `確定要把「${name}」改成「${newName}」嗎？\n\n` +
      `⚠️ 注意：\n` +
      `• 若步驟有使用「預設輸出路徑」（沒手動指定），下次執行會改寫到 ai_output/${newName}/ 資料夾，舊檔案不會搬過去\n` +
      `• 若後續步驟依賴前一步驟的輸出，可能因路徑改變導致 Recipe 快取失效，需要重新生成\n\n` +
      `建議：改名前最好所有節點都已明確指定「輸出路徑」。`
    )
    if (!ok) { setDraft(name); setEditing(false); return }
    onRename(newName)
    setEditing(false)
  }

  const countdown = useCountdown(nextRun)

  const relTime = (() => {
    const diff = Date.now() - updatedAt
    if (diff < 60000) return '剛才'
    if (diff < 3600000) return `${Math.floor(diff / 60000)} 分鐘前`
    if (diff < 86400000) return `${Math.floor(diff / 3600000)} 小時前`
    return new Date(updatedAt).toLocaleDateString('zh-TW', { month: 'short', day: 'numeric' })
  })()

  return (
    <div
      onClick={() => { if (!editing) onSelect() }}
      className={`group relative flex items-center gap-2 px-3 py-2.5 rounded-xl cursor-pointer transition-colors ${
        active ? 'bg-indigo-50 border border-indigo-200' : 'hover:bg-gray-50 border border-transparent'
      }`}
    >
      <Workflow className={`w-4 h-4 shrink-0 ${active ? 'text-indigo-600' : 'text-gray-400'}`} />
      <div className="flex-1 min-w-0">
        {editing ? (
          <input
            ref={inputRef}
            value={draft}
            onChange={e => setDraft(e.target.value)}
            onBlur={commit}
            onKeyDown={e => { if (e.key === 'Enter') commit(); if (e.key === 'Escape') setEditing(false) }}
            className="w-full text-sm font-medium text-gray-800 bg-transparent outline-none border-b border-indigo-400"
            onClick={e => e.stopPropagation()}
          />
        ) : (
          <p title={name} className={`text-sm font-medium truncate ${active ? 'text-indigo-700' : 'text-gray-700'}`}>{name}</p>
        )}
        {runStatus === 'running' ? (
          <p className="text-xs text-indigo-500 font-medium mt-0.5 flex items-center gap-1">
            <Loader2 className="w-3 h-3 animate-spin" />
            執行中…
          </p>
        ) : runStatus === 'completed' ? (
          <p className="text-xs text-emerald-500 font-medium mt-0.5">已完成</p>
        ) : runStatus === 'failed' ? (
          <p className="text-xs text-red-500 font-medium mt-0.5">執行失敗</p>
        ) : countdown ? (
          <div className="flex items-center gap-2 mt-0.5">
            <p className="text-xs text-amber-500 font-medium flex items-center gap-1">
              <span className="inline-block w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse" />
              {countdown}
            </p>
            <button
              onClick={async (e) => {
                e.stopPropagation()
                if (confirm(`確定取消「${name}」的排程執行？`)) {
                  try {
                    await cancelPipelineSchedule(name)
                    toast.success('排程已取消')
                    // 這裡依賴 Sidebar 的 fetchSchedules 每 15 秒同步一次
                  } catch (err) {
                    toast.error('取消失敗')
                  }
                }
              }}
              className="p-0.5 rounded hover:bg-amber-100 text-amber-600 transition-colors"
              title="取消排程"
            >
              <Square className="w-2.5 h-2.5 fill-current" />
            </button>
          </div>
        ) : (
          <p className="text-xs text-gray-400 mt-0.5">{relTime}</p>
        )}
      </div>
      {/* Action buttons */}
      <div className="shrink-0 flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
        {!editing && (
          <>
            <button onClick={e => { e.stopPropagation(); setEditing(true) }}
              className="p-1 rounded hover:bg-gray-200 text-gray-400 hover:text-gray-600" title="重新命名">
              <Pencil className="w-3 h-3" />
            </button>
            <button onClick={e => { e.stopPropagation(); onExport() }}
              className="p-1 rounded hover:bg-blue-50 text-gray-400 hover:text-blue-600" title="匯出">
              <Download className="w-3 h-3" />
            </button>
          </>
        )}
        {editing && (
          <button onClick={e => { e.stopPropagation(); commit() }}
            className="p-1 rounded hover:bg-green-100 text-green-500">
            <Check className="w-3 h-3" />
          </button>
        )}
        <button onClick={e => { e.stopPropagation(); onDelete() }}
          className="p-1 rounded hover:bg-red-50 text-gray-400 hover:text-red-500" title="刪除">
          <Trash2 className="w-3 h-3" />
        </button>
      </div>
    </div>
  )
}

// ── Sidebar ──────────────────────────────────────────────────────────────────
interface SidebarProps {
  onYamlApply: (yaml: string, mode: 'new' | 'overwrite') => void
}

const SIDEBAR_WIDTH_KEY = 'pipeline-sidebar-width'
const SIDEBAR_MIN_WIDTH = 256
const SIDEBAR_MAX_WIDTH = 560
const SIDEBAR_DEFAULT_WIDTH = 256

export default function Sidebar({ onYamlApply }: SidebarProps) {
  const {
    workflows, activeId,
    createWorkflow, updateWorkflow, removeWorkflow, setActive,
  } = useWorkflowStore()

  // ── 拖曳調寬 ─────────────────────────────────────────────────────
  const [width, setWidth] = useState(SIDEBAR_DEFAULT_WIDTH)
  const [resizing, setResizing] = useState(false)
  useEffect(() => {
    const saved = Number(localStorage.getItem(SIDEBAR_WIDTH_KEY))
    if (saved >= SIDEBAR_MIN_WIDTH && saved <= SIDEBAR_MAX_WIDTH) setWidth(saved)
  }, [])
  useEffect(() => {
    if (!resizing) return
    const onMove = (e: MouseEvent) => {
      const w = Math.min(SIDEBAR_MAX_WIDTH, Math.max(SIDEBAR_MIN_WIDTH, e.clientX))
      setWidth(w)
    }
    const onUp = () => {
      setResizing(false)
      try { localStorage.setItem(SIDEBAR_WIDTH_KEY, String(width)) } catch {}
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
    return () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
  }, [resizing, width])
  // 停止拖曳時寫入最新寬度
  useEffect(() => {
    if (resizing) return
    try { localStorage.setItem(SIDEBAR_WIDTH_KEY, String(width)) } catch {}
  }, [width, resizing])

  const [showChat, setShowChat] = useState(false)
  const [showNameModal, setShowNameModal] = useState(false)
  const [newName, setNewName] = useState('')
  const nameInputRef = useRef<HTMLInputElement>(null)
  const [messages, setMessages] = useState<ChatMsg[]>([
    { role: 'assistant', content: '你好！請告訴我你想自動化的工作流程，我會幫你產生 Pipeline YAML 設定。\n\n（正在載入專案路徑資訊…）' }
  ])

  // 環境路徑（用來動態組 welcome message）— 跨多個 effect 共用
  const [envPaths, setEnvPaths] = useState<EnvPaths | null>(null)
  // 「新話題」後暫時解綁工作流：下次發訊息不帶 workflow_id（AI 看不到當前 yaml/canvas）
  // 解綁狀態會在使用者切換 activeId 時自動清掉、重新綁定
  const [chatUnbound, setChatUnbound] = useState(false)
  useEffect(() => {
    // activeId 變了（使用者切了 workflow） → 解除暫時解綁、回到正常綁定
    setChatUnbound(false)
  }, [activeId])
  useEffect(() => {
    getEnvPaths().then(setEnvPaths).catch(() => {/* ignore — 沿用預設訊息 */})
  }, [])

  // 對話歷史持久化：
  //   有 activeId（選了工作流）→ 後端 per-workflow chat
  //   沒 activeId（剛開 app）→ localStorage scratch 暫存
  // 切換 activeId 時重新載入對應的歷史；welcome 訊息只在歷史空時顯示
  const SCRATCH_LS_KEY = 'pipeline-ai-chat-scratch-v1'
  // 防止 initial load 把自己又 persist 回去（會造成無限循環 / 覆蓋 race）
  const loadingRef = useRef(false)

  useEffect(() => {
    loadingRef.current = true
    const loadWelcome = (): ChatMsg => ({
      role: 'assistant',
      content: envPaths ? buildWelcomeMessage(envPaths)
        : '你好！請告訴我你想自動化的工作流程，我會幫你產生 Pipeline YAML 設定。',
    })
    const applyLoaded = (loaded: ChatMsg[]) => {
      setMessages(loaded.length > 0 ? loaded : [loadWelcome()])
      // 讓 React render 完再釋放 loading flag，避免緊接著的 setMessages 被誤判為使用者輸入
      setTimeout(() => { loadingRef.current = false }, 0)
    }
    if (activeId) {
      getWorkflowChat(activeId)
        .then(msgs => applyLoaded(msgs as ChatMsg[]))
        .catch(() => applyLoaded([]))
    } else {
      try {
        const raw = localStorage.getItem(SCRATCH_LS_KEY)
        const parsed = raw ? JSON.parse(raw) : []
        applyLoaded(Array.isArray(parsed) ? parsed : [])
      } catch {
        applyLoaded([])
      }
    }
  }, [activeId, envPaths])

  // 輔助：判斷目前顯示的是「歡迎訊息」單條還是使用者真的有對話
  // welcome 不該被寫進 DB 或 localStorage，避免每次載入都把 welcome 當歷史又寫回
  const isWelcomeOnly = (msgs: ChatMsg[]) =>
    msgs.length === 1 && msgs[0].role === 'assistant' && !msgs[0].hasYaml
  const [input, setInput]     = useState('')
  const [loading, setLoading] = useState(false)
  const chatEndRef = useRef<HTMLDivElement>(null)

  // 排程倒數：定期查詢排程並建立 name → nextRun 對應
  const [scheduleMap, setScheduleMap] = useState<Record<string, string>>({})
  useEffect(() => {
    const fetchSchedules = async () => {
      try {
        const tasks = await getPipelineScheduled()
        const map: Record<string, string> = {}
        for (const t of tasks) {
          if (t.next_run && t.name) map[t.name] = t.next_run
        }
        setScheduleMap(map)
      } catch { /* ignore */ }
    }
    fetchSchedules()
    const iv = setInterval(fetchSchedules, 15000)
    return () => clearInterval(iv)
  }, [])

  // 各工作流執行狀態：name → 'running' | 'completed' | 'failed'
  const [runStatusMap, setRunStatusMap] = useState<Record<string, 'running' | 'completed' | 'failed'>>({})
  useEffect(() => {
    const fetchRuns = async () => {
      try {
        const runs = await getPipelineRuns()
        const map: Record<string, 'running' | 'completed' | 'failed'> = {}
        const recentThreshold = 3 * 60 * 1000 // 完成/失敗狀態只顯示 3 分鐘
        for (const r of runs) {
          const name = r.pipeline_name
          if (r.status === 'running' || r.status === 'awaiting_human') {
            map[name] = 'running'
          } else if (!map[name] && r.ended_at) {
            const age = Date.now() - new Date(r.ended_at).getTime()
            if (age < recentThreshold) {
              if (r.status === 'completed') map[name] = 'completed'
              else if (r.status === 'failed' || r.status === 'aborted') map[name] = 'failed'
            }
          }
        }
        setRunStatusMap(map)
      } catch { /* ignore */ }
    }
    fetchRuns()
    const iv = setInterval(fetchRuns, 3000)
    return () => clearInterval(iv)
  }, [])

  // 自動滾到底部
  useEffect(() => {
    if (showChat) chatEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, showChat])

  // 初始化：從 API 載入工作流，並遷移 localStorage 舊資料
  useEffect(() => {
    const init = async () => {
      // 1) 先嘗試遷移 localStorage 的工作流到後端
      const LS_KEY = 'pipeline-workflows-v1'
      try {
        const raw = localStorage.getItem(LS_KEY)
        if (raw) {
          const parsed = JSON.parse(raw)
          const oldWorkflows: Array<{ id: string; name: string; nodes: any[]; edges: any[]; validate: boolean }> = parsed?.state?.workflows ?? []
          if (oldWorkflows.length > 0) {
            let migrated = 0
            for (const wf of oldWorkflows) {
              try {
                await createWorkflowApi(
                  wf.name,
                  { nodes: wf.nodes ?? [], edges: wf.edges ?? [] },
                  wf.validate ?? false,
                )
                migrated++
              } catch { /* 單筆失敗不中斷 */ }
            }
            if (migrated > 0) {
              toast.success(`已從瀏覽器遷移 ${migrated} 個工作流到資料庫`)
              // 只有成功遷移才清除 localStorage
              localStorage.removeItem(LS_KEY)
            }
          }
        }
      } catch { /* localStorage 讀取失敗不中斷 */ }

      // 2) 從 API 載入
      try {
        await useWorkflowStore.getState().fetchWorkflows()
        if (useWorkflowStore.getState().workflows.length === 0) {
          await createWorkflow('我的第一個工作流')
        }
      } catch (err) {
        toast.error(err instanceof Error ? err.message : '後端連線失敗')
      }
    }
    init()
  }, []) // eslint-disable-line

  const handleDelete = async (id: string, name: string) => {
    if (!confirm(`確定刪除「${name}」？此操作會一併刪除相關的 Recipe 和執行紀錄。`)) return
    try {
      await removeWorkflow(id)
      if (useWorkflowStore.getState().workflows.length === 0) {
        await createWorkflow('新工作流')
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '刪除失敗')
    }
  }

  const handleExport = async (id: string) => {
    try {
      const res = await fetch(exportWorkflowUrl(id))
      if (!res.ok) throw new Error('匯出失敗')
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      const disposition = res.headers.get('Content-Disposition')
      const match = disposition?.match(/filename\*=UTF-8''(.+)/)
      a.download = match ? decodeURIComponent(match[1]) : 'workflow.zip'
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
    } catch (err: any) {
      toast.error(err.message || '匯出失敗')
    }
  }

  const importRef = useRef<HTMLInputElement>(null)
  const handleImport = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    e.target.value = '' // 允許重複選同一檔案
    // 純 YAML：本地解析 → 走 onYamlApply 建新工作流（不經 backend zip 匯入流程，
    // 因為純 YAML 沒有 manifest/scripts/recipes，後端 importer 會拒收）
    const lower = file.name.toLowerCase()
    if (lower.endsWith('.yaml') || lower.endsWith('.yml')) {
      try {
        const text = await file.text()
        onYamlApply(text, 'new')
      } catch (err) {
        toast.error(err instanceof Error ? err.message : 'YAML 讀取失敗')
      }
      return
    }
    try {
      const res = await importWorkflow(file)
      await useWorkflowStore.getState().fetchWorkflows()
      useWorkflowStore.getState().setActive(res.workflow.id)
      let msg = `已匯入「${res.workflow.name}」`
      if (res.recipe_count > 0) msg += `，含 ${res.recipe_count} 個 Recipe`
      toast.success(msg)
      if (res.has_local_scripts) {
        toast.info('此工作流包含本地腳本步驟，請先確認相關腳本檔案已準備好才能執行', { duration: 6000 })
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '匯入失敗')
    }
  }

  const handleSend = async () => {
    const text = input.trim()
    if (!text || loading) return
    const userMsg: ChatMsg = { role: 'user', content: text }
    // 若當前只有 welcome 訊息，送出使用者訊息時把 welcome 丟掉（不存進歷史）
    const baseMsgs = isWelcomeOnly(messages) ? [] : messages
    const newMsgs = [...baseMsgs, userMsg]
    // 立即加入 user msg + 一個空的 assistant streaming bubble、後續 token 邊長邊填
    const assistantBubble: ChatMsg = {
      role: 'assistant',
      content: '',
      streaming: true,
      toolBlocks: [],
    }
    setMessages([...newMsgs, assistantBubble])
    setInput('')
    setLoading(true)
    persistAppend(userMsg).catch(() => {/* 落地失敗不擋 UI */})

    let accumulated = ''
    let finalHasYaml = false
    let finalYaml: string | null = null
    let finalYamlError: string | null = null
    try {
      await pipelineChatStream(
        newMsgs.map(m => ({ role: m.role, content: m.content })),
        chatUnbound ? undefined : (activeId ?? null),
        (ev) => {
          if (ev.type === 'token') {
            accumulated += ev.text
            setMessages(prev => {
              const copy = [...prev]
              const last = copy[copy.length - 1]
              if (last && last.role === 'assistant' && last.streaming) {
                copy[copy.length - 1] = { ...last, content: accumulated }
              }
              return copy
            })
          } else if (ev.type === 'tool_start') {
            setMessages(prev => {
              const copy = [...prev]
              const last = copy[copy.length - 1]
              if (last && last.role === 'assistant' && last.streaming) {
                const blocks = [...(last.toolBlocks || [])]
                blocks.push({ name: ev.name, args: ev.args || {}, status: 'running' })
                copy[copy.length - 1] = { ...last, toolBlocks: blocks }
              }
              return copy
            })
          } else if (ev.type === 'tool_end') {
            setMessages(prev => {
              const copy = [...prev]
              const last = copy[copy.length - 1]
              if (last && last.role === 'assistant' && last.streaming && last.toolBlocks?.length) {
                const blocks = [...last.toolBlocks]
                // 找最近一個 running 的 tool block(同名)
                for (let i = blocks.length - 1; i >= 0; i--) {
                  if (blocks[i].name === ev.name && blocks[i].status === 'running') {
                    blocks[i] = { ...blocks[i], status: 'done', preview: ev.result_preview }
                    break
                  }
                }
                copy[copy.length - 1] = { ...last, toolBlocks: blocks }
              }
              return copy
            })
          } else if (ev.type === 'done') {
            finalHasYaml = ev.has_yaml
            finalYaml = ev.yaml_content
            finalYamlError = ev.yaml_error
            // 用 done 事件帶的 reply 覆寫(_clean_latex 處理過的版本、比累積 token 更乾淨)
            accumulated = ev.reply || accumulated
          } else if (ev.type === 'error') {
            throw new Error(ev.detail || '串流錯誤')
          }
        },
      )
      // 串流結束、finalize bubble
      setMessages(prev => {
        const copy = [...prev]
        const last = copy[copy.length - 1]
        if (last && last.role === 'assistant' && last.streaming) {
          const finalized: ChatMsg = {
            role: 'assistant',
            content: accumulated,
            hasYaml: finalHasYaml,
            yaml: finalYaml,
            yamlError: finalYamlError,
            toolBlocks: last.toolBlocks,
            streaming: false,
          }
          copy[copy.length - 1] = finalized
          // 只 persist 不含 streaming flag、不含 toolBlocks(toolBlocks 是 ephemeral)
          persistAppend({
            role: 'assistant',
            content: accumulated,
            hasYaml: finalHasYaml,
            yaml: finalYaml,
            yamlError: finalYamlError,
          }).catch(() => {/* 同上 */})
        }
        return copy
      })
      if (finalYamlError) {
        const errStr: string = finalYamlError
        toast.error(`產生的 YAML 有語法問題：${errStr.slice(0, 120)}`)
      }
    } catch (e) {
      const errMsg = e instanceof Error ? e.message : '未知錯誤'
      toast.error(`AI 回應失敗:${errMsg.slice(0, 220)}`)
      // 把錯誤替換到 streaming bubble 上
      setMessages(prev => {
        const copy = [...prev]
        const last = copy[copy.length - 1]
        if (last && last.role === 'assistant' && last.streaming) {
          copy[copy.length - 1] = {
            role: 'assistant',
            content: `❌ ${errMsg}`,
            streaming: false,
          }
        } else {
          copy.push({ role: 'assistant', content: `❌ ${errMsg}` })
        }
        return copy
      })
    } finally {
      setLoading(false)
    }
  }

  // 將一則訊息寫入持久層（backend 或 localStorage）
  const persistAppend = async (msg: ChatMsg) => {
    if (loadingRef.current) return  // 初次載入中不 persist 避免 race
    if (activeId) {
      try {
        await appendWorkflowChat(activeId, msg.role, msg.content)
      } catch {/* ignore — 下次進來 DB 讀不到最新一則，但不影響目前 UI */}
    } else {
      // scratch 模式：整個 messages 陣列寫 localStorage（最簡單、讀 side 也統一）
      try {
        // 用 setTimeout 0 確保拿到最新的 setMessages 後的 state
        setTimeout(() => {
          setMessages(curr => {
            try {
              const toSave = curr.filter(m => !m.yamlError)  // 不把 error marker 存進去
              localStorage.setItem(SCRATCH_LS_KEY, JSON.stringify(toSave))
            } catch {/* quota */}
            return curr
          })
        }, 0)
      } catch {/* ignore */}
    }
  }

  // 清空對話 → 退回到只有 welcome 的狀態
  // 同時把對話「暫時解綁」當前工作流：下次發訊息 AI 不帶 yaml/canvas 上下文，
  // 等於從零開始；想討論其他工作流可直接從左邊清單切過去（切了就重新綁定）。
  const handleClearChat = async () => {
    if (loading) return
    if (!confirm(
      '清空目前對話、開始新話題？\n\n' +
      '• 畫布與 YAML 不變\n' +
      '• 對話會暫時與當前工作流解綁（下次訊息 AI 看不到目前 YAML、是真的新話題）\n' +
      '• 想回到原工作流的討論：從左邊清單切換工作流即可重新綁定'
    )) return
    const welcome: ChatMsg = {
      role: 'assistant',
      content: envPaths ? buildWelcomeMessage(envPaths)
        : '你好！請告訴我你想自動化的工作流程，我會幫你產生 Pipeline YAML 設定。',
    }
    setMessages([welcome])
    setChatUnbound(true)
    if (activeId) {
      try { await clearWorkflowChat(activeId) } catch { toast.error('清空失敗') }
    } else {
      try { localStorage.removeItem(SCRATCH_LS_KEY) } catch {/* ignore */}
    }
  }

  const submitCreate = async () => {
    const name = newName.trim()
    if (!name) { toast.error('名稱不能為空'); return }
    setShowNameModal(false)
    try {
      await createWorkflow(name)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '建立失敗，請稍後再試')
    }
  }

  return (
    <div
      className="shrink-0 h-full flex flex-col bg-white border-r border-gray-200 overflow-hidden relative"
      style={{ width, userSelect: resizing ? 'none' : undefined }}
    >
      {/* ── Resize Handle（右邊界） ── */}
      <div
        onMouseDown={(e) => { e.preventDefault(); setResizing(true) }}
        onDoubleClick={() => setWidth(SIDEBAR_DEFAULT_WIDTH)}
        title="拖曳調整寬度・雙擊還原"
        className={`absolute top-0 right-0 bottom-0 w-1 cursor-col-resize z-30 transition-colors ${
          resizing ? 'bg-indigo-400' : 'hover:bg-indigo-300'
        }`}
      />

      {/* ── 新增工作流：命名對話框 ── */}
      {showNameModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={() => setShowNameModal(false)}>
          <div className="bg-white rounded-2xl shadow-2xl w-[420px] p-5" onClick={e => e.stopPropagation()}>
            <h3 className="text-base font-semibold text-gray-800 mb-1">新增工作流</h3>
            <p className="text-xs text-gray-500 mb-4">為工作流命名（也可之後重新命名）</p>
            <input
              ref={nameInputRef}
              value={newName}
              onChange={e => setNewName(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') submitCreate(); if (e.key === 'Escape') setShowNameModal(false) }}
              className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm font-mono outline-none focus:border-indigo-400 focus:ring-1 focus:ring-indigo-400/20"
              placeholder="工作流名稱"
            />
            <div className="mt-3 p-3 rounded-lg bg-amber-50 border border-amber-200 text-xs leading-relaxed">
              <div className="font-medium text-amber-800 mb-1">⚠️ 關於名稱的提醒</div>
              <ul className="text-amber-700 space-y-0.5 ml-4 list-disc">
                <li>名稱會成為「預設輸出資料夾」的路徑（<code className="font-mono">ai_output/名稱/</code>）</li>
                <li>未來改名會造成預設路徑變更，可能導致 Recipe 快取失效、舊檔案留在舊路徑</li>
                <li>建議：取一個穩定的名字，每個節點都**明確指定輸出路徑**以避免後續問題</li>
              </ul>
            </div>
            <div className="mt-4 flex justify-end gap-2">
              <button onClick={() => setShowNameModal(false)}
                className="px-4 py-1.5 border border-gray-200 rounded-lg text-sm text-gray-600 hover:bg-gray-50">取消</button>
              <button onClick={submitCreate}
                className="px-4 py-1.5 bg-indigo-600 text-white rounded-lg text-sm font-medium hover:bg-indigo-700">建立</button>
            </div>
          </div>
        </div>
      )}

      {/* ── Logo — Atlas (山峰幾何 A) ── */}
      <div className="flex items-center gap-2.5 px-4 py-4 border-b border-gray-100">
        <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-indigo-500 via-indigo-600 to-purple-600 flex items-center justify-center shrink-0 shadow-md">
          <svg viewBox="0 0 24 24" className="w-6 h-6" fill="none" stroke="white" strokeWidth="2.8" strokeLinecap="round" strokeLinejoin="round" aria-label="Atlas">
            <path d="M4 21 L12 3 L20 21" />
            <path d="M7.8 14 L16.2 14" />
          </svg>
        </div>
        <span className="font-bold text-gray-900 text-base flex-1 tracking-tight">Atlas</span>
        <Link
          href="/recipes"
          className="p-1.5 rounded-lg text-gray-400 hover:text-gray-700 hover:bg-gray-100 transition-colors"
          title="Recipe Book"
        >
          <BookOpen className="w-4 h-4" />
        </Link>
        <Link
          href="/settings"
          className="p-1.5 rounded-lg text-gray-400 hover:text-gray-700 hover:bg-gray-100 transition-colors"
          title="設定"
        >
          <Settings className="w-4 h-4" />
        </Link>
      </div>

      {/* ── New / Import Workflow Buttons ── */}
      <div className="px-3 pt-3 pb-2 flex gap-1.5">
        <button
          onClick={() => {
            const existingNames = new Set(workflows.map(w => w.name))
            let suggested = '新工作流'
            let i = 1
            while (existingNames.has(suggested)) { suggested = `新工作流(${i})`; i++ }
            setNewName(suggested)
            setShowNameModal(true)
            setTimeout(() => nameInputRef.current?.select(), 50)
          }}
          className="flex-1 flex items-center justify-center gap-1.5 px-3 py-2 bg-indigo-600 text-white rounded-xl text-xs font-medium hover:bg-indigo-700 transition-colors shadow-sm"
        >
          <Plus className="w-3.5 h-3.5" />
          新增
        </button>
        <button
          onClick={() => importRef.current?.click()}
          className="flex items-center justify-center gap-1.5 px-3 py-2 border border-gray-200 text-gray-600 rounded-xl text-xs font-medium hover:bg-gray-50 transition-colors"
          title="匯入工作流（.zip 完整包 或 .yaml/.yml 單純流程）"
        >
          <Upload className="w-3.5 h-3.5" />
          匯入
        </button>
        <input ref={importRef} type="file" accept=".zip,.yaml,.yml" className="hidden" onChange={handleImport} />
      </div>

      {/* ── Workflow List ── */}
      <div className="flex-1 overflow-y-auto px-2 py-1 space-y-0.5 min-h-0">
        {workflows.length === 0 && (
          <p className="text-xs text-gray-400 text-center py-6">尚無工作流</p>
        )}
        {workflows.map(wf => (
          <WorkflowItem
            key={wf.id}
            id={wf.id}
            name={wf.name}
            active={wf.id === activeId}
            updatedAt={wf.updatedAt}
            nextRun={scheduleMap[wf.name] ?? null}
            runStatus={runStatusMap[wf.name] ?? null}
            onSelect={() => setActive(wf.id)}
            onRename={name => updateWorkflow(wf.id, { name })}
            onDelete={() => handleDelete(wf.id, wf.name)}
            onExport={() => handleExport(wf.id)}
          />
        ))}
      </div>

      {/* ── AI Assistant Section ──
          展開時以 absolute 覆蓋在 sidebar 下緣，佔 75% 高度（約蓋住工作流列表），收合時回到底部單列按鈕
      */}
      <div
        className={
          showChat
            ? 'absolute inset-x-0 bottom-0 top-1/4 bg-white border-t border-gray-100 flex flex-col z-20 shadow-lg'
            : 'border-t border-gray-100 flex flex-col'
        }
      >
        {/* Toggle button */}
        <button
          onClick={() => setShowChat(!showChat)}
          className={`flex items-center gap-2 px-4 py-3 text-sm transition-colors ${
            showChat ? 'text-indigo-600 bg-indigo-50' : 'text-gray-600 hover:text-indigo-600 hover:bg-gray-50'
          }`}
        >
          <Bot className="w-4 h-4 shrink-0" />
          <span className="font-medium flex-1 text-left">AI 助手</span>
          {loading && <Loader2 className="w-3.5 h-3.5 animate-spin text-indigo-500" />}
          {!loading && (showChat ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronUp className="w-3.5 h-3.5" />)}
        </button>

        {/* Chat panel */}
        {showChat && (
          <div className="flex flex-col flex-1 min-h-0 border-t border-gray-100">
            {/* Sub-toolbar：顯示目前綁定的工作流 + 新話題按鈕 */}
            <div className="flex items-center justify-between px-2.5 py-1.5 bg-gray-50/50 border-b border-gray-100 text-[11px] text-gray-500">
              <span className="truncate">
                {chatUnbound ? (
                  <>🆕 新話題（未綁工作流；切換 / 重選工作流即重新綁定）</>
                ) : activeId ? (
                  <>💾 對話綁定工作流：<span className="text-gray-700 font-medium">{workflows.find(w => w.id === activeId)?.name || activeId}</span></>
                ) : (
                  <>📝 暫存模式（未選工作流；建立 / 選取後才會持久保存）</>
                )}
              </span>
              <button
                onClick={handleClearChat}
                disabled={loading}
                className="shrink-0 ml-2 px-1.5 py-0.5 rounded text-[11px] text-gray-500 hover:text-red-600 hover:bg-red-50 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                title="清空目前對話，開始新話題"
              >
                🗑️ 新話題
              </button>
            </div>
            {/* Messages */}
            <div className="flex-1 overflow-y-auto p-2.5 space-y-2.5">
              {messages.map((msg, i) => (
                <div key={i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                  {msg.role === 'assistant' && (
                    <div className="w-5 h-5 rounded-full bg-indigo-100 flex items-center justify-center shrink-0 mt-0.5 mr-1.5">
                      <Bot className="w-3 h-3 text-indigo-600" />
                    </div>
                  )}
                  <div className={`max-w-[88%] min-w-0 rounded-xl px-2.5 py-1.5 text-xs leading-relaxed break-words overflow-hidden ${
                    msg.role === 'user'
                      ? 'bg-indigo-600 text-white rounded-br-sm'
                      : 'bg-gray-100 text-gray-700 rounded-bl-sm'
                  }`} style={{ overflowWrap: 'anywhere', wordBreak: 'break-word' }}>
                    {/* Tool blocks（串流時顯示工具呼叫進度）*/}
                    {msg.role === 'assistant' && msg.toolBlocks && msg.toolBlocks.length > 0 && (
                      <div className="mb-1.5 space-y-1">
                        {msg.toolBlocks.map((tb, ti) => (
                          <div
                            key={ti}
                            className={`text-[11px] px-2 py-1 rounded border ${
                              tb.status === 'running'
                                ? 'bg-indigo-50 border-indigo-200 text-indigo-700'
                                : 'bg-gray-50 border-gray-200 text-gray-600'
                            }`}
                          >
                            {tb.status === 'running' ? (
                              <span className="flex items-center gap-1.5">
                                <Loader2 className="w-3 h-3 animate-spin shrink-0" />
                                <span className="font-mono">{tb.name}</span>
                                <span className="text-[10px] text-indigo-500/70 truncate">
                                  {Object.entries(tb.args).slice(0, 2).map(([k, v]) =>
                                    `${k}=${typeof v === 'string' ? `"${v.slice(0, 30)}"` : JSON.stringify(v).slice(0, 30)}`
                                  ).join(', ')}
                                </span>
                              </span>
                            ) : (
                              <span className="flex items-center gap-1.5">
                                <span className="text-emerald-500 shrink-0">✓</span>
                                <span className="font-mono">{tb.name}</span>
                                {tb.preview && (
                                  <span className="text-[10px] text-gray-500 truncate" title={tb.preview}>
                                    {tb.preview.slice(0, 60)}
                                  </span>
                                )}
                              </span>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                    {msg.role === 'assistant' ? (
                      <div className="prose prose-xs max-w-none prose-p:my-0.5 prose-pre:text-xs prose-pre:whitespace-pre-wrap prose-code:break-all">
                        <ReactMarkdown rehypePlugins={[rehypeRaw]}>{cleanLatexInChat(msg.content.replace(/YAML_READY\n```yaml[\s\S]*?```/g, '（已偵測到 YAML ↓）'))}</ReactMarkdown>
                        {msg.streaming && (
                          <span className="inline-block w-1.5 h-3 ml-0.5 bg-indigo-500 animate-pulse align-middle" />
                        )}
                      </div>
                    ) : (
                      <span className="whitespace-pre-wrap">{msg.content}</span>
                    )}
                    {msg.hasYaml && msg.yamlError && (
                      <div className="mt-1.5 p-2 rounded-lg bg-red-50 border border-red-200 text-[11px] text-red-700 leading-relaxed">
                        ⚠️ YAML 有問題，建議請 AI 修正後再套用：<br/>
                        <code className="break-all">{msg.yamlError}</code>
                      </div>
                    )}
                    {msg.hasYaml && msg.yaml && (
                      <div className="mt-1.5 grid grid-cols-2 gap-1">
                        <button
                          onClick={() => onYamlApply(msg.yaml!, 'new')}
                          title="建立一個新的工作流來放這份 YAML，不碰目前的"
                          className={`flex items-center justify-center gap-1 py-1 rounded-lg text-xs font-medium transition-colors ${
                            msg.yamlError
                              ? 'bg-amber-500 hover:bg-amber-400 text-white'
                              : 'bg-emerald-500 hover:bg-emerald-400 text-white'
                          }`}
                        >
                          ＋ 建立新工作流
                        </button>
                        <button
                          onClick={() => {
                            if (!confirm('這會覆蓋目前工作流的內容（無法還原）。確定要繼續嗎？')) return
                            onYamlApply(msg.yaml!, 'overwrite')
                          }}
                          title="用這份 YAML 覆蓋目前工作流（會彈確認）"
                          className="flex items-center justify-center gap-1 py-1 rounded-lg text-xs font-medium border border-gray-300 bg-white hover:bg-gray-50 text-gray-600 transition-colors"
                        >
                          ⚠ 覆蓋目前
                        </button>
                      </div>
                    )}
                  </div>
                </div>
              ))}
              {loading && (
                <div className="flex items-center gap-2 text-xs text-gray-400 pl-7">
                  <Loader2 className="w-3 h-3 animate-spin" /> 思考中…
                </div>
              )}
              <div ref={chatEndRef} />
            </div>

            {/* Input */}
            <div className="p-2 border-t border-gray-100 flex gap-1.5 items-end">
              <textarea
                value={input}
                onChange={e => setInput(e.target.value)}
                placeholder="描述你的工作流…（Enter 換行）"
                disabled={loading}
                rows={2}
                className="flex-1 border border-gray-200 rounded-xl px-2.5 py-1.5 text-xs outline-none focus:border-indigo-400 transition-colors disabled:bg-gray-50 resize-none"
              />
              <button
                onClick={handleSend}
                disabled={!input.trim() || loading}
                className="w-7 h-7 flex items-center justify-center bg-indigo-600 text-white rounded-xl hover:bg-indigo-700 disabled:opacity-40 transition-colors shrink-0"
              >
                <Send className="w-3 h-3" />
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
