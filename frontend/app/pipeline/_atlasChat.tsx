'use client'
import { useState, useRef, useEffect } from 'react'
import { Bot, ChevronUp, ChevronDown, Send, Loader2, Sparkles, FolderOpen, Plus as PlusIcon, X, Minus } from 'lucide-react'
import { toast } from 'sonner'
import ReactMarkdown from 'react-markdown'
import rehypeRaw from 'rehype-raw'
import { useWorkflowStore } from './_store'
import {
  pipelineChatStream,
  getEnvPaths, type EnvPaths,
  getWorkflowChat, appendWorkflowChat, clearWorkflowChat,
} from '@/lib/api'

// ── AI Chat Message Type ─────────────────────────────────────────────────────
interface ToolBlock {
  name: string         // tool 名稱(list_workflows / get_run_log 等)
  args: Record<string, unknown>
  status: 'running' | 'done'
  preview?: string     // tool_end 時填、回傳前 200 字
}

export interface ChatMsg {
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
export function buildWelcomeMessage(env: EnvPaths): string {
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
單一步驟：用多代理節點、角色挑「**資料分析師(data_analyst)**」、最多輪數設 6-8、任務描述寫清楚目標即可（不用拆步驟）。多代理會自己 read → run_python → 看結果 → 再 read… 直到產出 \`analysis.md\`。`
  const ex7Note = '\n\n> 🧠 **何時用多代理 vs AI 技能**：每天跑、邏輯固定（讀 X 算 Y 寫 Z）→ 用 **AI 技能 + Recipe**（第二次起零 token）；結構不固定、邊想邊改、要試錯（探索/研究/debug）→ 用 **多代理**（每次重新推理、能根據中間結果調整、token 用量是 skill 的 2-5 倍）。'

  // 範例 8:變數系統 — 動態日期 / 跨節點傳值(讓 workflow 可重用、配 cron 跑一輩子)
  const ex8 = `**範例 8(動態啟動參數 + 跨節點傳值)**
任務:「我想讓某個爬蟲每天自動跑、檔名帶日期、結果寄給老闆」、或「UIA 從 ERP 抓到的訂單號要餵給後面所有節點用」。

兩種變數寫法:
1. **啟動參數**:YAML 寫 \`{{ input.date }}\`、跑的時候帶值(\`/run daily date=today\` 或前端 Run 對話框填),一條 YAML 配 cron 跑一輩子
2. **跨節點傳值**:UIA 抓欄位用 \`save_as: order_id\`、後面節點寫 \`{{ steps.uia_step.output.order_id }}\` 直接拿

效益:不用每天進來改死值、同一條流程能服務多客戶、抓到的值不必繞剪貼簿。`
  const ex8Note = '\n\n> 💡 **何時該用變數**:看到「每天 / 每週」「不同客戶」「上一步抓到的 X 餵給下一步」就用 \`{{ }}\`;一次性寫死腳本不用。\n> 📚 變數來源三種:\`{{ steps.X.output.Y }}\`(上游節點輸出)/ \`{{ input.X }}\`(啟動參數)/ \`{{ env.X }}\`(環境變數)。'

  const examples = [
    wrap('📋 範例 1：Python 腳本串接', ex1),
    wrap('📋 範例 2：Python 腳本 + AI 技能', ex2),
    wrap('📋 範例 3：Python + AI + 人工確認', ex3),
    wrap('📋 範例 4：網頁爬蟲 + AI 摘要 + 人工把關 + Outlook 寄信', ex4),
    wrap('📋 範例 5：AI 技能 + 視覺驗證', ex5 + ex5Note),
    wrap('📋 範例 6：啟動既有 Python 專案 + AI 驗證 + 人工確認', ex6 + ex6Note),
    wrap('📋 範例 7：多代理 — 探索式分析', ex7 + ex7Note),
    wrap('📋 範例 8:動態啟動參數 + 跨節點傳值(變數系統)', ex8 + ex8Note),
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

export function cleanLatexInChat(text: string): string {
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

// ── AtlasChat Props ─────────────────────────────────────────────────────────
export type AtlasChatMode = 'sidebar' | 'hero' | 'mini' | 'drawer'

export interface AtlasChatProps {
  /** UI 呈現模式。
   * - `sidebar`(預設,Phase 1 唯一實作):嵌在左側 sidebar 底部、可摺疊
   * - `hero`:Phase 3 將實作 — 首頁中央大畫面
   * - `mini`:Phase 4 將實作 — 縮小成 floating button
   * - `drawer`:Phase 4 將實作 — 從旁邊滑出的抽屜
   */
  mode?: AtlasChatMode
  /** 套用 LLM 產生的 YAML 到 canvas(建立新工作流 / 覆寫目前)。 */
  onYamlApply: (yaml: string, mode: 'new' | 'overwrite') => void
}

// ── AtlasChat Component ─────────────────────────────────────────────────────
// AI 助手聊天面板。原本內嵌於 _sidebar.tsx,Phase 1 抽出獨立元件、行為 100% 不變。
// Phase 2-5 會在這基礎上加 hero / mini / drawer 三種 mode。
export default function AtlasChat({ mode = 'sidebar', onYamlApply }: AtlasChatProps) {
  // 從 zustand store 拿目前綁定工作流的資訊(顯示「對話綁定:xxx」用)
  const { workflows, activeId } = useWorkflowStore()

  const [showChat, setShowChat] = useState(false)
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

  // 自動滾到底部
  useEffect(() => {
    if (showChat) chatEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, showChat])

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

  // ── Hero 畫面(Phase 3)──────────────────────────────────────────────────
  // 首頁中央大畫面、玻璃感(可透出 canvas)、4 個範例卡片(送第一則後收起)、
  // 內嵌 chat history、輸入框、底部 2 個 CTA。
  // 點範例 → 文字塞進輸入框(不立即送出);送出 → 訊息在 hero 內展開、不切 sidebar;
  // 只有 ESC / 右上 X / CTA / YAML 套用才會切到 sidebar。
  //
  // Hero 是「全新對話」介面:不繼承 parent 的 messages、不讀 localStorage 歷史、
  // 不把訊息 persist 進 localStorage / backend。reload 後就消失,sidebar 模式
  // 的歷史照常持久化(workflow-bound)。
  if (mode === 'hero') {
    return <HeroMode
      envPaths={envPaths}
      onYamlApply={onYamlApply}
    />
  }
  if (mode === 'mini') {
    // TODO Phase 4 實作:縮小 floating button(右下角 / 右上角)、點開展 drawer
    return null
  }
  if (mode === 'drawer') {
    // TODO Phase 4 實作:從旁邊滑出的抽屜、含過渡動畫
    return null
  }

  // ── mode === 'sidebar'(預設、Phase 1 唯一實作的 mode) ─────────────────
  // 展開時以 absolute 覆蓋在 sidebar 下緣,佔 75% 高度(約蓋住工作流列表),收合時回到底部單列按鈕
  return (
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
  )
}

// ── HeroMode 子元件(Phase 3)─────────────────────────────────────────────
// Atlas 首頁中央大畫面。獨立子元件、由父 AtlasChat 在 mode==='hero' 時 render。
// 為什麼拆出來:Hero 用獨立 input state(進 sidebar mode 不繼承)、避免 sidebar
// instance 的訊息流被 hero 干擾。共享 envPaths / handleSend 由 props 傳入。
//
// 4 個範例卡片(對應 buildWelcomeMessage 內的 ex1/ex4/ex6/ex7):
//   A 📋 Python 腳本串接   → 把幾個 .py 串成一條工作流
//   B 🌐 爬蟲 + AI + Outlook → 抓 → 摘要 → 確認 → 寄信完整鏈
//   C 🐍 啟動既有 Python 專案 → 把自家專案接進來自動跑
//   D 🧠 多代理探索分析     → 不確定怎麼做、讓 AI 邊想邊改
interface HeroModeProps {
  envPaths: EnvPaths | null
  onYamlApply: (yaml: string, mode: 'new' | 'overwrite') => void
}

// 4 個範例卡片的 metadata(短描述 = 卡片顯示;long = hover tooltip + 點擊塞進輸入框)
interface ExampleCard {
  key: 'A' | 'B' | 'C' | 'D'
  emoji: string
  title: string
  subtitle: string
  // 點擊後塞進輸入框的詳細範例描述(讓使用者直接送或微調)
  prompt: (env: EnvPaths | null) => string
}

const HERO_EXAMPLES: ExampleCard[] = [
  {
    key: 'A',
    emoji: '📋',
    title: 'Python 腳本串接',
    subtitle: '把幾個 .py 串成一條工作流',
    prompt: (env) => {
      const dir = env?.finance_example_dir
      if (env?.has_finance_example && dir) {
        return `把以下 Python 腳本串成一條工作流:
第一步:執行 \`python ${dir}\\stage1_generate_transactions.py\`,輸出到 \`ai_output/q1_finance/raw_transactions.xlsx\`
第二步:執行 \`python ${dir}\\stage2_clean_data.py\`,讀取上一步的 Excel,輸出到 \`ai_output/q1_finance/cleaned_transactions.xlsx\`
第三步:執行 \`python ${dir}\\stage3_analyze_finance.py\`,做財務彙總,輸出到 \`ai_output/q1_finance/financial_summary.xlsx\`
第四步:執行 \`python ${dir}\\stage4_generate_report.py\`,產出 \`ai_output/q1_finance/Q1_financial_report.xlsx\``
      }
      return `把以下 Python 腳本串成一條工作流:
第一步:執行 \`python 你的腳本.py\`,輸出到 \`ai_output/daily_report/raw.csv\`
第二步:執行 \`python 分析腳本.py\`,讀取上一步的 csv,輸出到 \`ai_output/daily_report/result.xlsx\``
    },
  },
  {
    key: 'B',
    emoji: '🌐',
    title: '爬蟲 + AI + Outlook',
    subtitle: '抓 → 摘要 → 確認 → 寄信完整鏈',
    prompt: () =>
      `我想做一條每天跑的工作流:
第一步(網頁爬蟲):抓 \`https://www.reddit.com/r/ASUS/\` 列表頁
第二步(AI 技能):抽前 10 篇連結各自展開抓內文,每篇 80 字內摘要,輸出 \`ai_output/reddit_asus/daily.md\`
第三步(人工確認):把摘要傳到 Telegram,我看過 OK 才繼續
第四步(Outlook):把 daily.md 當附件用 send_with_attachment 模板寄給 boss@x.com`,
  },
  {
    key: 'C',
    emoji: '🐍',
    title: '啟動既有 Python 專案',
    subtitle: '把自家專案接進來自動跑',
    prompt: () =>
      `我有一個 Python 專案,想接到工作流自動化跑:
1. 跑專案的 main.py、產出檔案到工作流目錄
2. AI 驗證一下產出檔內容對不對
3. 確認沒問題後 Telegram 通知我做最終放行

(專案放在 external_projects/<名稱>/ 底下,請問我要哪個專案)`,
  },
  {
    key: 'D',
    emoji: '🧠',
    title: '多代理探索分析',
    subtitle: '不確定怎麼做、讓 AI 邊想邊改',
    prompt: () =>
      `任務:「我有 \`sales.xlsx\`,想看看 Q1 哪幾個品類賣最差、找出共通原因」這種「不確定要看什麼指標、邊看邊找」的場景。
單一步驟:用多代理節點、角色挑「資料分析師(data_analyst)」、最多輪數設 6-8、任務描述寫清楚目標即可(不用拆步驟)。多代理會自己 read → run_python → 看結果 → 再 read… 直到產出 \`analysis.md\`。`,
  },
]

function HeroMode({ envPaths, onYamlApply }: HeroModeProps) {
  const setChatUIState = useWorkflowStore(s => s.setChatUIState)
  const setHasInteracted = useWorkflowStore(s => s.setHasInteracted)
  const activeId = useWorkflowStore(s => s.activeId)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const chatEndRef = useRef<HTMLDivElement>(null)
  // 出場淡入(opacity 0 → 1,300ms)— 用 mounted flag + CSS transition
  const [mounted, setMounted] = useState(false)
  // 退場淡出(opacity → 0 + scale 0.98,200ms)— 觸發後等動畫結束才切 state
  const [closing, setClosing] = useState(false)

  // ── Hero 專屬 ephemeral state ───────────────────────────────────────────
  // Hero 是「全新對話」介面 — 不繼承 parent messages、不讀 localStorage 歷史、
  // 不 persist 出去。reload 後就消失,讓進站體驗永遠像第一次見面。
  // (sidebar 模式仍會走 parent AtlasChat 的 workflow-bound 歷史)
  const [heroMessages, setHeroMessages] = useState<ChatMsg[]>([])
  const [heroInput, setHeroInput] = useState('')
  const [heroLoading, setHeroLoading] = useState(false)

  useEffect(() => {
    // 進場:下一個 frame 設 mounted = true、讓 opacity 0 → 1 過渡
    const t = requestAnimationFrame(() => setMounted(true))
    return () => cancelAnimationFrame(t)
  }, [])

  // hasStarted:對話是否已展開(本次 session 有任何 user 訊息)
  // - 初始(heroMessages 空)→ false:顯示歡迎大標 + 4 卡片 + CTA
  // - 送出第一則訊息後 → true:卡片 / CTA 收起、改顯示 chat history scroll area
  const hasStarted = heroMessages.some(m => m.role === 'user')

  // hasStarted 切換時、自動滾到最新訊息
  useEffect(() => {
    if (hasStarted) chatEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [heroMessages, hasStarted])

  // 切到 sidebar mode 的統一 helper:含退場動畫
  const exitToSidebar = (markInteracted: boolean = false) => {
    if (closing) return
    setClosing(true)
    if (markInteracted) setHasInteracted(true)
    // 等 fade out 動畫結束才真正切 state
    setTimeout(() => setChatUIState('sidebar'), 200)
  }

  // ESC 鍵 → 切 sidebar(逃生口)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        exitToSidebar(false)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [closing]) // eslint-disable-line react-hooks/exhaustive-deps

  // 點範例卡片 → 把對應 prompt 塞進輸入框(不立即送出、focus 給使用者改)
  const onCardClick = (card: ExampleCard) => {
    setHeroInput(card.prompt(envPaths))
    // 等下一個 tick 再 focus、textarea 才已經有值
    setTimeout(() => inputRef.current?.focus(), 0)
  }

  // Hero-local handleSend:走 pipelineChatStream、但完全不 persist(不寫 localStorage、
  // 不呼 appendWorkflowChat)。訊息只活在這個 HeroMode component 的 state 裡、
  // reload 就消失。workflow_id 仍綁 activeId(讓 AI 看得到當前 yaml/canvas 上下文)。
  const heroHandleSend = async () => {
    const text = heroInput.trim()
    if (!text || heroLoading) return
    const userMsg: ChatMsg = { role: 'user', content: text }
    const baseMsgs = heroMessages
    const newMsgs = [...baseMsgs, userMsg]
    const assistantBubble: ChatMsg = {
      role: 'assistant',
      content: '',
      streaming: true,
      toolBlocks: [],
    }
    setHeroMessages([...newMsgs, assistantBubble])
    setHeroInput('')
    setHeroLoading(true)

    let accumulated = ''
    let finalHasYaml = false
    let finalYaml: string | null = null
    let finalYamlError: string | null = null
    try {
      await pipelineChatStream(
        newMsgs.map(m => ({ role: m.role, content: m.content })),
        activeId ?? null,
        (ev) => {
          if (ev.type === 'token') {
            accumulated += ev.text
            setHeroMessages(prev => {
              const copy = [...prev]
              const last = copy[copy.length - 1]
              if (last && last.role === 'assistant' && last.streaming) {
                copy[copy.length - 1] = { ...last, content: accumulated }
              }
              return copy
            })
          } else if (ev.type === 'tool_start') {
            setHeroMessages(prev => {
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
            setHeroMessages(prev => {
              const copy = [...prev]
              const last = copy[copy.length - 1]
              if (last && last.role === 'assistant' && last.streaming && last.toolBlocks?.length) {
                const blocks = [...last.toolBlocks]
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
            accumulated = ev.reply || accumulated
          } else if (ev.type === 'error') {
            throw new Error(ev.detail || '串流錯誤')
          }
        },
      )
      setHeroMessages(prev => {
        const copy = [...prev]
        const last = copy[copy.length - 1]
        if (last && last.role === 'assistant' && last.streaming) {
          copy[copy.length - 1] = {
            role: 'assistant',
            content: accumulated,
            hasYaml: finalHasYaml,
            yaml: finalYaml,
            yamlError: finalYamlError,
            toolBlocks: last.toolBlocks,
            streaming: false,
          }
        }
        return copy
      })
      if (finalYamlError) {
        const errStr: string = finalYamlError
        toast.error(`產生的 YAML 有語法問題:${errStr.slice(0, 120)}`)
      }
    } catch (e) {
      const errMsg = e instanceof Error ? e.message : '未知錯誤'
      toast.error(`AI 回應失敗:${errMsg.slice(0, 220)}`)
      setHeroMessages(prev => {
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
      setHeroLoading(false)
    }
  }

  // 送出 → 走 hero-local handleSend、訊息只更新 heroMessages、不 persist
  // 標記 hasInteracted,讓下次進站直接走 sidebar mode(不要再彈 hero)
  const onSubmit = async () => {
    if (!heroInput.trim() || heroLoading) return
    setHasInteracted(true)
    await heroHandleSend()
  }

  // Enter 送出 / Shift+Enter 換行
  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      onSubmit()
    }
  }

  // 點 overlay 灰色邊緣不關閉(避免誤點、必須主動操作 CTA 才關)
  const onOverlayClick = (e: React.MouseEvent) => {
    // 不做任何事 — 留著當 click sink
    e.stopPropagation()
  }

  // 點「跑現有工作流」CTA — Phase 5 才接 modal、現在先 toast + 切 sidebar
  const onRunExisting = () => {
    toast.info('Phase 5 將開啟「選擇現有工作流」對話框')
    exitToSidebar(true)
  }

  // 點「開啟空白畫布」CTA — 純切 sidebar、不發訊息
  const onBlankCanvas = () => {
    exitToSidebar(true)
  }

  // 右上「最小化」按鈕:把 hero 收到 sidebar、保留對話內容
  const onMinimize = () => {
    exitToSidebar(true)
  }

  // YAML 套用包裝:套用後 hero 不關、繼續在 hero 內對話
  // (若想立刻去畫布看結果、使用者可按最小化按鈕)
  const handleYamlApplyInHero = (yaml: string, mode: 'new' | 'overwrite') => {
    onYamlApply(yaml, mode)
    // 套 YAML 是「進畫布」的訊號、自動切回 sidebar 讓使用者看到畫布
    exitToSidebar(true)
  }

  // 整體 fade in/out 樣式:mounted false 或 closing true → opacity 0
  const containerOpacity = (!mounted || closing) ? 'opacity-0' : 'opacity-100'
  const cardScale = closing ? 'scale-[0.98]' : 'scale-100'

  return (
    <div
      onClick={onOverlayClick}
      // Overlay 用「柔和 focal blur」:backdrop-blur-md 讓 canvas 節點輪廓清楚、
      // 細節略糊、視線自動聚焦到前景 hero card。bg-slate-950/10 一層極淡黑紗、
      // 微微壓暗背景但不擋住 canvas。過去 25% + blur-2xl 太重像不透明擋板、
      // 改為 transparent 又完全沒模糊讓 canvas razor sharp 搶眼 — 這次取中間值。
      className={`fixed inset-0 z-50 bg-slate-950/10 backdrop-blur-md transition-opacity duration-300 ${containerOpacity}`}
    >
      {/* 中央 glass card — hasStarted 後變寬變高、容納 chat history
          毛玻璃兩層 blur:overlay 的 backdrop-blur-md(背景柔焦)+ card 自己的
          backdrop-blur-2xl(卡片底下再加強毛玻璃感)。卡片基底用 bg-slate-900/60
          (深底色 60% 不透明)讓內部文字、cards、輸入框內容對比足夠、清楚可讀;
          border-white/25 略強描邊做毛玻璃邊界;shadow-2xl shadow-black/50 給深度感。*/}
      <div
        className={`absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 bg-clip-padding backdrop-blur-2xl bg-slate-900/60 border border-white/25 rounded-3xl shadow-2xl shadow-black/50 transition-all duration-300 ${cardScale} ${
          hasStarted
            ? 'w-[92vw] max-w-[820px] h-[82vh] max-h-[820px] flex flex-col px-5 py-4 sm:px-7 sm:py-5'
            : 'w-[90vw] max-w-[720px] px-6 py-8 sm:px-10 sm:py-12'
        }`}
        onClick={e => e.stopPropagation()}
      >
        {/* 右上控制列(始終存在):最小化 + 關閉 */}
        <div className={`absolute right-3 top-3 flex items-center gap-1 ${hasStarted ? 'z-10' : ''}`}>
          <button
            onClick={onMinimize}
            title="最小化到 sidebar(對話保留)"
            className="w-8 h-8 flex items-center justify-center rounded-full text-white/40 hover:text-white/90 hover:bg-white/10 transition-colors"
          >
            <Minus className="w-4 h-4" />
          </button>
          <button
            onClick={() => exitToSidebar(false)}
            title="關閉(ESC)"
            className="w-8 h-8 flex items-center justify-center rounded-full text-white/40 hover:text-white/90 hover:bg-white/10 transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* === 初始畫面(hasStarted=false):大歡迎 + 4 卡片 + CTA ============= */}
        {!hasStarted && (
          <>
            {/* Atlas logo / 標題 — 漸層 white → sky 而非紫色 */}
            {/* 從 38px 放大到 72px(text-7xl ≈ 72px),font-light 配大字級看起來
                像 macOS Big Sur logo lockup。Sparkles 圖示同比例放大、與字 baseline 對齊。*/}
            <div className="text-center mb-6">
              <h1
                className="text-7xl sm:text-[80px] font-light tracking-wide bg-gradient-to-r from-white to-sky-200 bg-clip-text text-transparent leading-none"
                style={{ fontFamily: "'Inter', 'Noto Sans TC', sans-serif" }}
              >
                <Sparkles className="inline w-14 h-14 sm:w-16 sm:h-16 mr-3 -mt-3 text-sky-200/80" />
                Atlas
              </h1>
            </div>

            {/* 歡迎大字 */}
            <div className="text-center mb-7">
              <h2 className="text-[22px] sm:text-[24px] font-medium text-white leading-snug">
                歡迎回來、想要我替您執行什麼任務?
              </h2>
            </div>

            {/* 4 個範例卡片 — grid 2×2,hover 用 sky 而非紫 */}
            <div className="grid grid-cols-2 gap-3 mb-6">
              {HERO_EXAMPLES.map(card => (
                <button
                  key={card.key}
                  onClick={() => onCardClick(card)}
                  title={card.prompt(envPaths)}
                  className="text-left bg-slate-800/40 hover:bg-slate-700/60 border border-white/15 hover:border-sky-300/40 rounded-2xl p-4 cursor-pointer transition-all hover:scale-[1.02] focus:outline-none focus:ring-2 focus:ring-sky-300/40"
                >
                  <div className="text-[28px] mb-1.5 leading-none">{card.emoji}</div>
                  <div className="text-[14px] font-semibold text-white mb-0.5">{card.title}</div>
                  <div className="text-[12px] text-white/70 leading-snug">{card.subtitle}</div>
                </button>
              ))}
            </div>
          </>
        )}

        {/* === 對話模式(hasStarted=true):縮小 logo + chat history ========== */}
        {hasStarted && (
          <>
            {/* 縮小的 logo 列(取代大歡迎標題)*/}
            <div className="flex items-center gap-2 mb-3 pl-1 pr-20">
              <Sparkles className="w-4 h-4 text-sky-200/80 shrink-0" />
              <span className="text-[15px] font-medium bg-gradient-to-r from-white to-sky-200 bg-clip-text text-transparent">
                Atlas
              </span>
              <span className="text-[11px] text-white/40 ml-1">主要對話介面</span>
            </div>

            {/* Chat history scroll area — 佔卡片大部分高度 */}
            <div className="flex-1 min-h-0 overflow-y-auto pr-1 space-y-3 [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-thumb]:bg-white/10 [&::-webkit-scrollbar-thumb]:rounded-full">
              {heroMessages.map((msg, i) => (
                <div key={i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                  {msg.role === 'assistant' && (
                    <div className="w-6 h-6 rounded-full bg-sky-400/15 border border-sky-300/20 flex items-center justify-center shrink-0 mt-1 mr-2">
                      <Bot className="w-3 h-3 text-sky-200" />
                    </div>
                  )}
                  {/* AI bubble:從 bg-white/[0.07] 加深到 bg-slate-800/50,border 從
                      white/[0.08] 強化到 white/20,文字 white/95 — 提高對比、可清楚閱讀。
                      User bubble(sky-500/80)維持不動。*/}
                  <div
                    className={`max-w-[85%] min-w-0 rounded-2xl px-3 py-2 text-[13px] leading-relaxed break-words overflow-hidden ${
                      msg.role === 'user'
                        ? 'bg-sky-500/80 text-white rounded-br-sm shadow-md shadow-sky-900/30'
                        : 'bg-slate-800/50 border border-white/20 text-white/95 rounded-bl-sm'
                    }`}
                    style={{ overflowWrap: 'anywhere', wordBreak: 'break-word' }}
                  >
                    {/* Tool blocks(串流時顯示工具呼叫進度)*/}
                    {msg.role === 'assistant' && msg.toolBlocks && msg.toolBlocks.length > 0 && (
                      <div className="mb-1.5 space-y-1">
                        {msg.toolBlocks.map((tb, ti) => (
                          <div
                            key={ti}
                            className={`text-[11px] px-2 py-1 rounded border ${
                              tb.status === 'running'
                                ? 'bg-sky-400/10 border-sky-300/20 text-sky-100'
                                : 'bg-white/[0.04] border-white/[0.06] text-white/60'
                            }`}
                          >
                            {tb.status === 'running' ? (
                              <span className="flex items-center gap-1.5">
                                <Loader2 className="w-3 h-3 animate-spin shrink-0" />
                                <span className="font-mono">{tb.name}</span>
                                <span className="text-[10px] text-sky-200/70 truncate">
                                  {Object.entries(tb.args).slice(0, 2).map(([k, v]) =>
                                    `${k}=${typeof v === 'string' ? `"${v.slice(0, 30)}"` : JSON.stringify(v).slice(0, 30)}`
                                  ).join(', ')}
                                </span>
                              </span>
                            ) : (
                              <span className="flex items-center gap-1.5">
                                <span className="text-emerald-300 shrink-0">✓</span>
                                <span className="font-mono">{tb.name}</span>
                                {tb.preview && (
                                  <span className="text-[10px] text-white/40 truncate" title={tb.preview}>
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
                      <div className="prose prose-sm prose-invert max-w-none prose-p:my-1 prose-pre:text-xs prose-pre:whitespace-pre-wrap prose-pre:bg-black/30 prose-code:break-all prose-code:bg-white/10 prose-code:px-1 prose-code:rounded prose-headings:text-white prose-strong:text-white prose-a:text-sky-300">
                        <ReactMarkdown rehypePlugins={[rehypeRaw]}>{cleanLatexInChat(msg.content.replace(/YAML_READY\n```yaml[\s\S]*?```/g, '(已偵測到 YAML ↓)'))}</ReactMarkdown>
                        {msg.streaming && (
                          <span className="inline-block w-1.5 h-3 ml-0.5 bg-sky-300 animate-pulse align-middle" />
                        )}
                      </div>
                    ) : (
                      <span className="whitespace-pre-wrap">{msg.content}</span>
                    )}
                    {msg.hasYaml && msg.yamlError && (
                      <div className="mt-1.5 p-2 rounded-lg bg-red-500/15 border border-red-300/30 text-[11px] text-red-100 leading-relaxed">
                        ⚠️ YAML 有問題,建議請 AI 修正後再套用:<br/>
                        <code className="break-all">{msg.yamlError}</code>
                      </div>
                    )}
                    {msg.hasYaml && msg.yaml && (
                      <div className="mt-2 grid grid-cols-2 gap-1.5">
                        <button
                          onClick={() => handleYamlApplyInHero(msg.yaml!, 'new')}
                          title="建立一個新的工作流來放這份 YAML"
                          className={`flex items-center justify-center gap-1 py-1.5 rounded-lg text-xs font-medium transition-colors ${
                            msg.yamlError
                              ? 'bg-amber-500/80 hover:bg-amber-400 text-white'
                              : 'bg-emerald-500/80 hover:bg-emerald-400 text-white'
                          }`}
                        >
                          ＋ 建立新工作流
                        </button>
                        <button
                          onClick={() => {
                            if (!confirm('這會覆蓋目前工作流的內容(無法還原)。確定要繼續嗎?')) return
                            handleYamlApplyInHero(msg.yaml!, 'overwrite')
                          }}
                          title="用這份 YAML 覆蓋目前工作流"
                          className="flex items-center justify-center gap-1 py-1.5 rounded-lg text-xs font-medium border border-white/20 bg-white/[0.04] hover:bg-white/[0.08] text-white/80 transition-colors"
                        >
                          ⚠ 覆蓋目前
                        </button>
                      </div>
                    )}
                  </div>
                </div>
              ))}
              {heroLoading && (
                <div className="flex items-center gap-2 text-xs text-white/40 pl-8">
                  <Loader2 className="w-3 h-3 animate-spin" /> 思考中…
                </div>
              )}
              <div ref={chatEndRef} />
            </div>
          </>
        )}

        {/* 輸入框 + 送出(兩種狀態都有,但 hasStarted 後緊貼底部)*/}
        <div className={`relative ${hasStarted ? 'mt-3' : 'mb-4'}`}>
          <textarea
            ref={inputRef}
            value={heroInput}
            onChange={e => setHeroInput(e.target.value)}
            onKeyDown={onKeyDown}
            disabled={heroLoading}
            rows={hasStarted ? 2 : 3}
            placeholder={hasStarted
              ? '繼續對話…(Enter 送出 / Shift+Enter 換行)'
              : '想做什麼?跟我說...(例:每天早上 9 點抓 Reddit r/ASUS 的熱門貼文、AI 摘要、Telegram 通知我)'}
            className="w-full bg-slate-800/40 border border-white/20 rounded-2xl px-5 py-3.5 pr-14 text-white placeholder-white/55 focus:bg-slate-800/60 focus:border-sky-300/50 outline-none transition resize-none text-[14px] leading-relaxed disabled:opacity-50"
          />
          <button
            onClick={onSubmit}
            disabled={!heroInput.trim() || heroLoading}
            className={`absolute right-3 bottom-3 w-10 h-10 flex items-center justify-center rounded-xl transition-all ${
              heroInput.trim() && !heroLoading
                ? 'bg-sky-400 hover:bg-sky-300 text-white shadow-lg shadow-sky-900/30 cursor-pointer'
                : 'bg-white/[0.08] text-white/30 cursor-not-allowed'
            }`}
            title="送出(Enter)"
          >
            {heroLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
          </button>
        </div>

        {/* 底部次要 CTA(僅初始畫面顯示)*/}
        {!hasStarted && (
          <>
            <div className="flex gap-3 justify-center">
              <button
                onClick={onRunExisting}
                className="flex items-center gap-1.5 px-4 py-2 rounded-xl text-[13px] text-white/75 hover:text-white hover:bg-white/[0.12] transition-colors"
              >
                <FolderOpen className="w-3.5 h-3.5" />
                跑現有工作流
              </button>
              <button
                onClick={onBlankCanvas}
                className="flex items-center gap-1.5 px-4 py-2 rounded-xl text-[13px] text-white/75 hover:text-white hover:bg-white/[0.12] transition-colors"
              >
                <PlusIcon className="w-3.5 h-3.5" />
                開啟空白畫布
              </button>
            </div>

            {/* 底部小提示 — ESC 逃生 */}
            <div className="mt-4 text-center text-[11px] text-white/50">
              按 ESC 跳過、或選擇上方任一選項繼續
            </div>
          </>
        )}
      </div>
    </div>
  )
}
