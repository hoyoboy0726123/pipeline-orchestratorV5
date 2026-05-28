'use client'
import { useState, useRef, useEffect } from 'react'
import { Bot, ChevronUp, ChevronDown, Send, Loader2, X, Minus, RotateCcw } from 'lucide-react'
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
  // 用於「新對話」按鈕觸發 Hero overlay 重新出現(跟 Hero 連動、是同一入口)
  const setChatUIState = useWorkflowStore(s => s.setChatUIState)

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
  // 等於從零開始;想討論其他工作流可直接從左邊清單切過去(切了就重新綁定)。
  //
  // 額外行為(User 反饋):新對話 = 跟 Hero 連動、開啟 Hero overlay。
  // 因為「新對話」跟 Hero 本質是同一個入口(全新空白對話的開始)、
  // 點下去應該回到那個視覺最強的 Hero 介面、提供範例卡片 / CTA 給使用者選下一步。
  const handleClearChat = async () => {
    if (loading) return
    if (!confirm(
      '開啟新對話?\n\n' +
      '• 畫布與 YAML 不變\n' +
      '• Sidebar 的對話會清空、且暫時與當前工作流解綁\n' +
      '• 會彈出 Hero 對話視窗(類似首頁、有範例卡片)\n' +
      '• 想回到原工作流的討論:從左邊清單切換工作流即可重新綁定'
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
    // 跟 Hero 連動 — 切到 hero state、Hero overlay 自動出現
    setChatUIState('hero')
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
                      <ReactMarkdown rehypePlugins={[rehypeRaw]}>{cleanLatexInChat(msg.content.replace(/YAML_READY\n/g, ''))}</ReactMarkdown>
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
                        disabled={!!msg.yamlError}
                        title={msg.yamlError
                          ? 'YAML 有解析錯誤、無法套用、請請 AI 重新產出完整 YAML'
                          : '建立一個新的工作流來放這份 YAML，不碰目前的'}
                        className={`flex items-center justify-center gap-1 py-1 rounded-lg text-xs font-medium transition-colors ${
                          msg.yamlError
                            ? 'bg-gray-200 text-gray-400 cursor-not-allowed'
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
                        disabled={!!msg.yamlError}
                        title={msg.yamlError
                          ? 'YAML 有解析錯誤、無法套用、請請 AI 重新產出完整 YAML'
                          : '用這份 YAML 覆蓋目前工作流（會彈確認）'}
                        className={`flex items-center justify-center gap-1 py-1 rounded-lg text-xs font-medium transition-colors ${
                          msg.yamlError
                            ? 'bg-gray-200 text-gray-400 cursor-not-allowed border border-gray-300'
                            : 'border border-gray-300 bg-white hover:bg-gray-50 text-gray-600'
                        }`}
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

// ── HeroMode 子元件(Phase 3 — Soft Architectural Grid 風格重做)─────────
// Atlas 首頁中央大畫面。獨立子元件、由父 AtlasChat 在 mode==='hero' 時 render。
// 視覺定位:Soft Architectural Grid — 米色 + 32px 規則網格 + 純白卡片 + 4px hard
// shadow + brutalist 邊框、字型混 Bricolage Grotesque / JetBrains Mono / Plus
// Jakarta Sans。無圓角、無漸層、無 emoji(取代舊 dark glass overlay + sky 配色)。
//
// 8 個 ACTIONS 卡片對齊 V5 真實功能(對應 buildWelcomeMessage 內的 ex1-ex8):
//   chain        Python 腳本串接
//   scrape-ai    爬蟲 + AI + Outlook
//   existing     啟動既有 Python 專案
//   multiagent   多代理探索分析
//   schedule     排程定時任務
//   notify       人工確認與通知串
//   db-report    資料分析 → AI 報表
//   monitor      網頁變化偵測
interface HeroModeProps {
  envPaths: EnvPaths | null
  onYamlApply: (yaml: string, mode: 'new' | 'overwrite') => void
}

// ── Atlas Soft Architectural Grid — design tokens ───────────────────────
// 直接使用 design handoff README 的色票 / 字級 / 間距、不抽 CSS variable
// (避免污染 globals.css、HeroMode 是相對隔離的 overlay,單檔內聚就好)
const ATLAS_PAL = {
  bg:       '#F6F4EE',                    // 主背景(米色)
  bgCard:   '#FFFFFF',                    // 卡片底色
  ink:      '#16170F',                    // 主文字(近黑)
  inkSoft:  '#67685E',                    // 次要文字(60% 暖灰)
  rule:     'rgba(22,23,15,0.10)',        // 邊框與網格線
  forest:   '#3E5C4B',                    // 主色 — 強調連結、hover 邊框、accent
  brick:    '#B85A2E',
  sand:     '#D6B16D',
  dusk:     '#5470A1',
} as const

const FONT_DISPLAY = "'Bricolage Grotesque', sans-serif"
const FONT_BODY    = "'Plus Jakarta Sans', system-ui, sans-serif"
const FONT_MONO    = "'JetBrains Mono', monospace"

// ── ActionGlyph — 8 個幾何 SVG icon(逐字抄 reference HTML) ─────────────
type GlyphPalette = { fg: string; a1: string; a2: string; a3: string; a4: string }

function ActionGlyph({ id, size = 28, palette: c }: { id: ActionId; size?: number; palette: GlyphPalette }) {
  const props = { width: size, height: size, viewBox: '0 0 32 32', fill: 'none' as const, 'aria-hidden': true }
  switch (id) {
    case 'chain':
      return (
        <svg {...props}>
          <rect x="3" y="13" width="9" height="6" rx="2" fill={c.a1} />
          <rect x="11.5" y="13" width="9" height="6" rx="2" fill={c.a2} />
          <rect x="20" y="13" width="9" height="6" rx="2" fill={c.a3} />
        </svg>
      )
    case 'scrape-ai':
      return (
        <svg {...props}>
          <circle cx="10" cy="10" r="6" fill={c.a2} />
          <circle cx="22" cy="16" r="6" fill={c.a1} />
          <circle cx="14" cy="22" r="6" fill={c.a3} />
        </svg>
      )
    case 'existing':
      return (
        <svg {...props}>
          <rect x="5" y="5" width="22" height="22" rx="3" fill="none" stroke={c.fg} strokeWidth="1.5" />
          <rect x="9" y="9" width="14" height="3" rx="1" fill={c.a2} />
          <rect x="9" y="14" width="9" height="3" rx="1" fill={c.a3} />
          <rect x="9" y="19" width="11" height="3" rx="1" fill={c.a1} />
        </svg>
      )
    case 'multiagent':
      return (
        <svg {...props}>
          <circle cx="9" cy="9" r="4.5" fill={c.a1} />
          <circle cx="23" cy="9" r="4.5" fill={c.a3} />
          <circle cx="16" cy="22" r="4.5" fill={c.a2} />
          <path d="M9 9 L23 9 M9 9 L16 22 M23 9 L16 22" stroke={c.fg} strokeWidth="1" opacity="0.4" />
        </svg>
      )
    case 'research':
      // 放大鏡 + 書本(研究)
      return (
        <svg {...props}>
          <rect x="6" y="6" width="14" height="18" rx="1.5" fill={c.a2} />
          <path d="M10 11 L17 11 M10 14 L17 14 M10 17 L15 17" stroke={c.fg} strokeWidth="1.2" strokeLinecap="round" opacity="0.6" />
          <circle cx="22" cy="22" r="5" fill="none" stroke={c.a1} strokeWidth="2.4" />
          <path d="M26 26 L29 29" stroke={c.a1} strokeWidth="2.4" strokeLinecap="round" />
        </svg>
      )
    case 'compete':
      // 矩陣 / 對比表(競品)
      return (
        <svg {...props}>
          <rect x="5" y="5" width="9" height="9" rx="1" fill={c.a1} />
          <rect x="18" y="5" width="9" height="9" rx="1" fill={c.a2} />
          <rect x="5" y="18" width="9" height="9" rx="1" fill={c.a3} />
          <rect x="18" y="18" width="9" height="9" rx="1" fill="none" stroke={c.fg} strokeWidth="1.5" />
        </svg>
      )
    case 'db-report':
      return (
        <svg {...props}>
          <ellipse cx="16" cy="8" rx="9" ry="3" fill={c.a2} />
          <path d="M7 8 V15 C7 17 11 18 16 18 C21 18 25 17 25 15 V8" fill={c.a3} />
          <path d="M7 15 V22 C7 24 11 25 16 25 C21 25 25 24 25 22 V15" fill={c.a1} />
        </svg>
      )
    case 'monitor':
      return (
        <svg {...props}>
          <circle cx="14" cy="14" r="8" fill="none" stroke={c.fg} strokeWidth="1.5" />
          <circle cx="14" cy="14" r="3" fill={c.a1} />
          <path d="M20 20 L26 26" stroke={c.fg} strokeWidth="2.4" strokeLinecap="round" />
        </svg>
      )
  }
}

// ── AtlasMark — 跟 sidebar 一致的 indigo gradient + 山峰 SVG ──────────
// (之前用 brutalist 幾何版本、跟畫布 sidebar 樣式不一致、改成跟 sidebar 同款)
function AtlasMark({ size = 32 }: { size?: number }) {
  return (
    <div
      className="rounded-lg bg-gradient-to-br from-indigo-500 via-indigo-600 to-purple-600 flex items-center justify-center shrink-0 shadow-md"
      style={{ width: size, height: size }}
    >
      <svg
        viewBox="0 0 24 24"
        width={size * 0.75}
        height={size * 0.75}
        fill="none"
        stroke="white"
        strokeWidth="2.8"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-label="Atlas"
      >
        <path d="M4 21 L12 3 L20 21" />
        <path d="M7.8 14 L16.2 14" />
      </svg>
    </div>
  )
}

// ── 8 個 ACTIONS(對齊 V5 真實功能)─────────────────────────────────────
type ActionId =
  | 'chain' | 'scrape-ai' | 'existing' | 'multiagent'
  | 'research' | 'compete' | 'db-report' | 'monitor'

interface AtlasAction {
  id: ActionId
  title: string
  desc: string
  tag: string
  examples: [string, string, string]
}

// 卡片內容對齊 V5 真實節點集合(網頁爬蟲 / AI 技能 / 多代理 / 人工確認 / Outlook
// / 視覺驗證 / 啟動既有 Python 專案 等)、與 buildWelcomeMessage 的範例呼應。
//
// ⚠ 所有 example 都設計成「點下去 AI 助手能寫出可直接跑通的 YAML」、不依賴 user
// 提供額外檔案。需要資料的 case(資料分析、多代理)由 AI 助手在第一步生假資料 demo。
const ATLAS_ACTIONS: AtlasAction[] = [
  {
    id: 'chain', title: 'Python 腳本串接', desc: '把幾個 .py 串成一條工作流', tag: 'Workflow',
    examples: [
      'AI 生 3 個示範腳本(產資料 → 清洗 → 報表)串成一條 script 工作流',
      '示範腳本串接 + 第 2 步後插 AI 健康判讀 + 條件分流',
      '示範腳本串接 + 失敗 Telegram 通知',
    ],
  },
  {
    id: 'scrape-ai', title: '爬蟲 + AI + Outlook', desc: '抓 → 摘要 → 確認 → 寄信', tag: 'Pipeline',
    examples: [
      '每天抓 Reddit r/ASUS 熱門 → AI 摘要 → Telegram 確認 → Outlook 寄信',
      '抓 Hacker News top 10 → 中文翻譯 + 重點 → Outlook 草稿',
      '抓 PTT 股版前 10 篇 → 重點整理 → Telegram 推送',
    ],
  },
  {
    id: 'existing', title: '啟動既有 Python 專案', desc: 'AI 讀源碼、拆 CLI 參數、ask_user 互動', tag: 'Import',
    examples: [
      '跑 V5 內建 interactive_demo 報表工具、AI 讀源碼判斷 CLI 參數',
      '對既有 GUI/CLI 專案、AI 用 ask_user 收 user 選擇再組合參數跑',
      '把 main_cli.py 包成可互動工作流、選報表類型 / 格式 / 期間',
    ],
  },
  {
    id: 'multiagent', title: '多代理探索分析', desc: '不確定怎麼做、讓 AI 邊想邊改', tag: 'Agent',
    examples: [
      'AI 先生假 sales.xlsx(100 筆) → data_analyst 探索式分析',
      'AI 生假 PR 描述 → critic 審查 + 改善建議',
      '指定研究主題 → researcher 收料 + report_writer 寫成報告',
    ],
  },
  {
    id: 'research', title: '網路研究報告', desc: 'web_search 收料、寫深度報告', tag: 'Research',
    examples: [
      '研究「2026 AI 筆電市場」→ researcher 收料寫深度報告',
      '查「最新 LLM benchmark 排名」→ trend_analyst 趨勢分析',
      '彙整 2026 H1 NVIDIA 技術發表 → 中文研究報告',
    ],
  },
  {
    id: 'compete', title: '競品深度分析', desc: '多家對比、矩陣表 + 強弱', tag: 'Compete',
    examples: [
      'ASUS vs MSI vs Lenovo 電競筆電矩陣比較',
      '抓 3 家定價頁面 → data_differ → 變動 Telegram 通知',
      'iPhone 16 vs Galaxy S25 vs Pixel 9 → 規格 + 口碑 + 價格矩陣',
    ],
  },
  {
    id: 'db-report', title: '資料分析 → AI 報表', desc: 'pandas + AI 寫洞察', tag: 'Insight',
    examples: [
      'AI 先生 6 個月假銷售 csv → pandas 分析 → 中文 markdown 報告',
      'AI 生假客戶回饋 20 筆 → 自動分類 → 主題摘要報告',
      'AI 生假股價時序 12 個月 → 趨勢圖 + 洞察',
    ],
  },
  {
    id: 'monitor', title: '網頁變化偵測', desc: '定時抓網頁、變動就通知', tag: 'Watch',
    examples: [
      '監控 PChome ASUS 筆電專區價格變動、變動就 Telegram 通知',
      'ASUS 官方公告頁(asus.com/news/)有新文章 → 自動寄信',
      'PTT 看板出現關鍵字「ROG」就推播提醒',
    ],
  },
]

function HeroMode({ envPaths: _envPaths, onYamlApply }: HeroModeProps) {
  const setChatUIState = useWorkflowStore(s => s.setChatUIState)
  const setHasInteracted = useWorkflowStore(s => s.setHasInteracted)
  const activeId = useWorkflowStore(s => s.activeId)
  const inputRef = useRef<HTMLInputElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
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
  // Soft Architectural Grid 卡片 hover 狀態 — 單一 hoveredId、同時只能有一張卡 active
  const [hoveredCard, setHoveredCard] = useState<ActionId | null>(null)

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

  // 點範例卡片 → 把指定 example 塞進輸入框(不立即送出、focus 給使用者改)
  // exampleIdx 可指定要用哪個 example(預設 0、給「點 card 整體」用)
  // 點「具體某行 example」時、handler 會傳對應 idx、不再永遠用第一個
  const onCardClick = (action: AtlasAction, exampleIdx: number = 0) => {
    setHeroInput(action.examples[exampleIdx] ?? action.examples[0])
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
  // (Soft Architectural Grid 設計不顯示此 CTA、保留 handler 給未來用)
  const _onRunExisting = () => {
    toast.info('Phase 5 將開啟「選擇現有工作流」對話框')
    exitToSidebar(true)
  }

  // Footer:[+] new canvas — 純切 sidebar、不發訊息(切到空白畫布)
  const onNewCanvas = () => {
    exitToSidebar(true)
  }

  // Footer:[⌥] import .py — Phase 5 才接、現在 placeholder
  const onImportPy = () => {
    // eslint-disable-next-line no-console
    console.log('[Hero] import .py — TODO: open file picker (Phase 5)')
    toast.info('import .py 功能即將上線(Phase 5)')
  }

  // Footer:[⌘K] command — Phase 5 才接 command palette
  const onCmdK = () => {
    // eslint-disable-next-line no-console
    console.log('[Hero] command palette — TODO (Phase 5)')
    toast.info('Command palette 即將上線(Phase 5)')
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

  // ── Soft Architectural Grid 配色 / 字型 ────────────────────────────────
  // 這些 inline style 直接打在 element 上、不走 Tailwind。原因:
  // 1. brutalist 設計用很多自訂顏色與精確字級、用 Tailwind 反而要寫一堆 arbitrary value
  // 2. inline style 與 reference HTML 一比就能對齊、改 design tokens 直觀
  // 3. HeroMode 是相對隔離的 overlay、不影響 globals.css
  const glyphPal: GlyphPalette = {
    fg: ATLAS_PAL.ink, a1: ATLAS_PAL.forest, a2: ATLAS_PAL.brick, a3: ATLAS_PAL.sand, a4: ATLAS_PAL.dusk,
  }

  return (
    <div
      onClick={onOverlayClick}
      // Layer 1 — Backdrop:全螢幕半透明黑 + backdrop-blur,讓底下 canvas 模糊可見
      // (取代舊「整片米色蓋滿 viewport」設計、改回真正的 overlay 觀感)
      // 點 backdrop 不關 hero(避免誤關)— onOverlayClick 是 click sink
      className={`fixed inset-0 z-50 bg-slate-950/20 backdrop-blur-md flex items-center justify-center transition-opacity duration-300 ${containerOpacity}`}
      style={{ color: ATLAS_PAL.ink, fontFamily: FONT_BODY }}
    >
      {/* Layer 2 — Centered Hero Panel:
          - max-w-[1100px] w-[92vw] / max-h-[88vh]:不撐破 viewport、自然置中
          - 米色背景 + 32px 規則網格(設計風保留)
          - 圓角 0(brutalist)+ 1px 邊框 + shadow-2xl(深度感、像紙片浮著)
          - overflow-hidden + flex flex-col:內容超過 panel 不溢出、垂直排列 */}
      <div
        className={`relative w-[92vw] max-w-[1100px] max-h-[88vh] overflow-hidden flex flex-col rounded-none shadow-2xl shadow-black/30 transition-all duration-300 ${cardScale}`}
        style={{
          backgroundColor: ATLAS_PAL.bg,
          backgroundImage:
            `linear-gradient(${ATLAS_PAL.rule} 1px, transparent 1px),` +
            `linear-gradient(90deg, ${ATLAS_PAL.rule} 1px, transparent 1px)`,
          backgroundSize: '32px 32px',
          backgroundPosition: '-1px -1px',
          border: `1px solid ${ATLAS_PAL.rule}`,
          padding: '24px 36px',
        }}
        onClick={e => e.stopPropagation()}
      >
        {/* ================= Top bar ================= */}
        {/* 左:Atlas mark + wordmark;右:三個 stage chip (1·觸發 / 2·處理 / 3·輸出)
            外加最小化 / 關閉(取代舊 absolute 右上 icon)
            marginBottom 從 36 縮到 20、塞進 panel 上方 */}
        <div className="flex items-center justify-between shrink-0" style={{ marginBottom: 20, position: 'relative', zIndex: 1 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <AtlasMark size={32} />
            <span className="font-bold text-gray-900 text-base tracking-tight">Atlas</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            {['1·觸發', '2·處理', '3·輸出'].map((s, i) => (
              <div
                key={i}
                style={{
                  fontFamily: FONT_MONO, fontSize: 10.5, letterSpacing: '0.06em',
                  padding: '5px 10px', border: `1px solid ${ATLAS_PAL.rule}`,
                  background: ATLAS_PAL.bgCard, color: ATLAS_PAL.inkSoft,
                }}
              >{s}</div>
            ))}
            {/* 重新開始(清空對話):user 想開新對話時用、不關閉視窗 */}
            <button
              onClick={() => {
                if (heroMessages.length === 0) return
                if (!confirm('清空目前對話、重新開始?')) return
                setHeroMessages([])
              }}
              title="清空對話、重新開始"
              disabled={heroMessages.length === 0}
              style={{
                fontFamily: FONT_MONO, fontSize: 10.5, letterSpacing: '0.06em',
                padding: '5px 10px', border: `1px solid ${ATLAS_PAL.rule}`,
                background: ATLAS_PAL.bgCard, color: ATLAS_PAL.inkSoft,
                cursor: heroMessages.length === 0 ? 'not-allowed' : 'pointer',
                opacity: heroMessages.length === 0 ? 0.5 : 1,
                marginLeft: 4,
              }}
            >
              <RotateCcw className="w-3.5 h-3.5 inline-block" style={{ verticalAlign: 'middle' }} />
            </button>
            {/* 最小化 + 關閉:用 mono 文字而非 lucide icon、貼合 brutalist 風格 */}
            <button
              onClick={onMinimize}
              title="最小化到 sidebar(對話保留)"
              style={{
                fontFamily: FONT_MONO, fontSize: 10.5, letterSpacing: '0.06em',
                padding: '5px 10px', border: `1px solid ${ATLAS_PAL.rule}`,
                background: ATLAS_PAL.bgCard, color: ATLAS_PAL.inkSoft, cursor: 'pointer',
              }}
            >
              <Minus className="w-3.5 h-3.5 inline-block" style={{ verticalAlign: 'middle' }} />
            </button>
            <button
              onClick={() => exitToSidebar(false)}
              title="關閉(ESC)"
              style={{
                fontFamily: FONT_MONO, fontSize: 10.5, letterSpacing: '0.06em',
                padding: '5px 10px', border: `1px solid ${ATLAS_PAL.rule}`,
                background: ATLAS_PAL.bgCard, color: ATLAS_PAL.inkSoft, cursor: 'pointer',
              }}
            >
              <X className="w-3.5 h-3.5 inline-block" style={{ verticalAlign: 'middle' }} />
            </button>
          </div>
        </div>

        {/* ================= Initial state(!hasStarted):Hero + Cards grid + Input + Footer =================
            整個 !hasStarted 區塊用 flex flex-col flex-1 min-h-0、讓內容垂直排列、
            cards grid 可在 panel 內 scroll、input + footer 永遠停在 panel 底部 */}
        {!hasStarted && (
          <div className="flex flex-col flex-1 min-h-0">
            {/* Hero header:breadcrumb + h1(h1 從 56 縮到 42、給 input/cards 留空間)*/}
            <div className="shrink-0" style={{ marginBottom: 16, position: 'relative', zIndex: 1, maxWidth: 760 }}>
              <div style={{
                display: 'flex', gap: 10, alignItems: 'center', marginBottom: 10,
                fontFamily: FONT_MONO, fontSize: 11, letterSpacing: '0.04em', color: ATLAS_PAL.inkSoft,
              }}>
                <span style={{ display: 'inline-block', width: 8, height: 8, background: ATLAS_PAL.forest }} />
                home / start a workflow
              </div>
              <h1 style={{
                fontFamily: FONT_DISPLAY, fontWeight: 500, fontSize: 42,
                lineHeight: 1.05, letterSpacing: '-0.03em', margin: 0, color: ATLAS_PAL.ink,
              }}>
                選一個方向開始,
                或<span style={{
                  color: ATLAS_PAL.forest,
                  textDecoration: 'underline',
                  textUnderlineOffset: 6,
                  textDecorationThickness: 2,
                }}>用一句話</span>描述你要的工作流。
              </h1>
            </div>

            {/* 4×2 cards grid(<1000px 小螢幕時自動降成 2 欄 4 列)
                用 min-h-0 + overflow-y-auto:cards 區若超過剩餘空間就 scroll、不擠到 input */}
            <div
              role="list"
              className="grid grid-cols-2 lg:grid-cols-4 flex-1 min-h-0 overflow-y-auto"
              style={{
                gap: 10,
                position: 'relative',
                zIndex: 1,
                marginBottom: 14,
                gridAutoRows: 'min-content',
              }}
            >
              {ATLAS_ACTIONS.map(a => {
                const isHover = hoveredCard === a.id
                return (
                  <button
                    key={a.id}
                    role="listitem"
                    aria-expanded={isHover}
                    onMouseEnter={() => setHoveredCard(a.id)}
                    onMouseLeave={() => setHoveredCard(null)}
                    onFocus={() => setHoveredCard(a.id)}
                    onBlur={() => setHoveredCard(null)}
                    onClick={() => onCardClick(a)}
                    style={{
                      background: ATLAS_PAL.bgCard,
                      border: `1px solid ${isHover ? ATLAS_PAL.ink : ATLAS_PAL.rule}`,
                      padding: 12, cursor: 'pointer',
                      transition: 'border-color 180ms, box-shadow 200ms, transform 200ms',
                      position: 'relative', minHeight: 160,
                      display: 'flex', flexDirection: 'column', gap: 10,
                      boxShadow: isHover ? `4px 4px 0 0 ${ATLAS_PAL.ink}` : 'none',
                      transform: isHover ? 'translate(-3px,-3px)' : 'translate(0,0)',
                      textAlign: 'left',
                      borderRadius: 0,
                      color: ATLAS_PAL.ink,
                      fontFamily: FONT_BODY,
                    }}
                  >
                    {/* Top row:glyph 左、tag 右 */}
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                      <ActionGlyph id={a.id} size={28} palette={glyphPal} />
                      <div style={{
                        fontFamily: FONT_MONO, fontSize: 9.5, letterSpacing: '0.06em',
                        color: ATLAS_PAL.inkSoft, padding: '2px 6px',
                        border: `1px solid ${ATLAS_PAL.rule}`,
                      }}>{a.tag}</div>
                    </div>

                    {/* Body — 兩層 absolute,cross-fade 切換 */}
                    <div style={{ flex: 1, position: 'relative', overflow: 'hidden' }}>
                      {/* Default layer:title + desc */}
                      <div style={{
                        opacity: isHover ? 0 : 1,
                        transition: 'opacity 180ms',
                        position: 'absolute', inset: 0,
                      }}>
                        <div style={{
                          fontFamily: FONT_DISPLAY, fontWeight: 600, fontSize: 17,
                          lineHeight: 1.2, letterSpacing: '-0.01em', marginBottom: 6,
                        }}>{a.title}</div>
                        <div style={{
                          fontSize: 12.5, color: ATLAS_PAL.inkSoft, lineHeight: 1.45,
                        }}>{a.desc}</div>
                      </div>
                      {/* Hover layer:title↗ + 3 examples */}
                      <div style={{
                        opacity: isHover ? 1 : 0,
                        transition: 'opacity 220ms 60ms',
                        position: 'absolute', inset: 0,
                        display: 'flex', flexDirection: 'column', gap: 6,
                      }}>
                        <div style={{
                          fontFamily: FONT_DISPLAY, fontWeight: 600, fontSize: 13.5,
                          lineHeight: 1.1, marginBottom: 4,
                        }}>{a.title} ↗</div>
                        {a.examples.map((ex, k) => (
                          <div
                            key={k}
                            onClick={(e) => {
                              // 阻止 bubble 到外層卡片 button、不會永遠抓 examples[0]
                              e.stopPropagation()
                              onCardClick(a, k)
                            }}
                            onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.background = ATLAS_PAL.bg }}
                            onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.background = 'transparent' }}
                            style={{
                              fontSize: 11.5, lineHeight: 1.35, color: ATLAS_PAL.ink,
                              display: 'flex', gap: 6,
                              padding: '4px 6px',
                              marginLeft: -6, marginRight: -6,
                              borderBottom: k === a.examples.length - 1 ? 'none' : `1px solid ${ATLAS_PAL.rule}`,
                              cursor: 'pointer',
                              transition: 'background 120ms',
                            }}
                          >
                            <span style={{
                              color: ATLAS_PAL.forest, fontFamily: FONT_MONO,
                              fontSize: 10, flexShrink: 0,
                            }}>+</span>
                            <span>{ex}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  </button>
                )
              })}
            </div>

            {/* OR / 描述一句 divider */}
            <div style={{ position: 'relative', zIndex: 1 }}>
              <div style={{ display: 'flex', alignItems: 'center', marginBottom: 10 }}>
                <div style={{ height: 1, background: ATLAS_PAL.ink, flex: 1 }} />
                <div style={{
                  fontFamily: FONT_MONO, fontSize: 10.5, letterSpacing: '0.14em',
                  color: ATLAS_PAL.inkSoft, padding: '0 12px', textTransform: 'uppercase',
                }}>OR / 描述一句</div>
                <div style={{ height: 1, background: ATLAS_PAL.ink, flex: 1 }} />
              </div>

              {/* Input row(brutalist:純黑邊框、無圓角)*/}
              <div style={{
                background: ATLAS_PAL.bgCard, border: `1px solid ${ATLAS_PAL.ink}`,
                display: 'flex', alignItems: 'center', padding: '14px 16px', gap: 14,
              }}>
                <span style={{ fontFamily: FONT_MONO, fontSize: 12, color: ATLAS_PAL.forest }}>{'>'}</span>
                <input
                  ref={inputRef}
                  value={heroInput}
                  onChange={e => setHeroInput(e.target.value)}
                  onKeyDown={e => {
                    if (e.key === 'Enter' && !e.shiftKey) {
                      e.preventDefault()
                      onSubmit()
                    }
                  }}
                  disabled={heroLoading}
                  placeholder="每天早上 9 點抓 Reddit 熱門 → AI 摘要 → Telegram 通知"
                  style={{
                    flex: 1, border: 'none', outline: 'none', background: 'transparent',
                    fontFamily: FONT_MONO, fontSize: 13.5, color: ATLAS_PAL.ink,
                  }}
                />
                <button
                  onClick={onSubmit}
                  disabled={!heroInput.trim() || heroLoading}
                  style={{
                    background: heroInput.trim() && !heroLoading ? ATLAS_PAL.ink : ATLAS_PAL.inkSoft,
                    color: ATLAS_PAL.bg, padding: '8px 16px',
                    fontFamily: FONT_MONO, fontSize: 11.5, letterSpacing: '0.04em',
                    cursor: heroInput.trim() && !heroLoading ? 'pointer' : 'not-allowed',
                    border: 'none', borderRadius: 0,
                    display: 'inline-flex', alignItems: 'center', gap: 6,
                  }}
                >
                  {heroLoading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : null}
                  run ↵
                </button>
              </div>
            </div>

            {/* Footer hints(panel 內底部、shrink-0 不被擠掉)*/}
            <div className="shrink-0" style={{
              paddingTop: 14,
              display: 'flex', justifyContent: 'space-between',
              fontFamily: FONT_MONO, fontSize: 10.5, letterSpacing: '0.06em',
              color: ATLAS_PAL.inkSoft, position: 'relative', zIndex: 1,
            }}>
              <div style={{ display: 'flex', gap: 20 }}>
                <button
                  onClick={onNewCanvas}
                  style={{ fontFamily: FONT_MONO, fontSize: 10.5, letterSpacing: '0.06em', color: ATLAS_PAL.inkSoft, background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}
                >[+] new canvas</button>
                <button
                  onClick={onImportPy}
                  style={{ fontFamily: FONT_MONO, fontSize: 10.5, letterSpacing: '0.06em', color: ATLAS_PAL.inkSoft, background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}
                >[⌥] import .py</button>
                <button
                  onClick={onCmdK}
                  style={{ fontFamily: FONT_MONO, fontSize: 10.5, letterSpacing: '0.06em', color: ATLAS_PAL.inkSoft, background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}
                >[⌘K] command</button>
              </div>
              <span>esc · skip</span>
            </div>
          </div>
        )}

        {/* ================= Chat mode(hasStarted=true):chat history + input + footer ================= */}
        {/* 進入對話後、卡片區收起、改顯示 chat scroll;視覺保持 brutalist 風格(白卡 + 黑邊)
            外層 flex flex-col flex-1 min-h-0:讓 chat scroll area 真正能 flex-1、input 永遠在底 */}
        {hasStarted && (
          <div className="flex flex-col flex-1 min-h-0">
            {/* Breadcrumb-style header(對話模式縮小)*/}
            <div className="shrink-0" style={{
              display: 'flex', gap: 10, alignItems: 'center', marginBottom: 12,
              fontFamily: FONT_MONO, fontSize: 11, letterSpacing: '0.04em', color: ATLAS_PAL.inkSoft,
              position: 'relative', zIndex: 1,
            }}>
              <span style={{ display: 'inline-block', width: 8, height: 8, background: ATLAS_PAL.forest }} />
              home / talking to atlas
            </div>

            {/* Chat history scroll area */}
            <div
              className="flex-1 min-h-0 overflow-y-auto pr-1 space-y-3 [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-thumb]:bg-black/10 [&::-webkit-scrollbar-thumb]:rounded-full"
              style={{ position: 'relative', zIndex: 1 }}
            >
              {heroMessages.map((msg, i) => (
                <div key={i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                  {msg.role === 'assistant' && (
                    <div
                      className="shrink-0 mt-1 mr-2 flex items-center justify-center"
                      style={{
                        width: 22, height: 22,
                        background: ATLAS_PAL.bgCard,
                        border: `1px solid ${ATLAS_PAL.rule}`,
                      }}
                    >
                      <span style={{
                        display: 'inline-block', width: 8, height: 8, background: ATLAS_PAL.forest,
                      }} />
                    </div>
                  )}
                  <div
                    className="max-w-[85%] min-w-0 break-words overflow-hidden"
                    style={{
                      background: msg.role === 'user' ? ATLAS_PAL.ink : ATLAS_PAL.bgCard,
                      color: msg.role === 'user' ? ATLAS_PAL.bg : ATLAS_PAL.ink,
                      border: msg.role === 'user' ? 'none' : `1px solid ${ATLAS_PAL.rule}`,
                      padding: '10px 14px',
                      fontFamily: FONT_BODY,
                      fontSize: 13,
                      lineHeight: 1.5,
                      borderRadius: 0,
                      overflowWrap: 'anywhere', wordBreak: 'break-word',
                    }}
                  >
                    {msg.role === 'assistant' && msg.toolBlocks && msg.toolBlocks.length > 0 && (
                      <div className="mb-2 space-y-1">
                        {msg.toolBlocks.map((tb, ti) => (
                          <div
                            key={ti}
                            style={{
                              fontFamily: FONT_MONO, fontSize: 11,
                              padding: '4px 8px',
                              border: `1px solid ${tb.status === 'running' ? ATLAS_PAL.forest : ATLAS_PAL.rule}`,
                              background: tb.status === 'running' ? '#EEF2EC' : '#F7F6F2',
                              color: ATLAS_PAL.ink,
                            }}
                          >
                            {tb.status === 'running' ? (
                              <span className="flex items-center gap-1.5">
                                <Loader2 className="w-3 h-3 animate-spin shrink-0" />
                                <span>{tb.name}</span>
                                <span style={{ fontSize: 10, color: ATLAS_PAL.inkSoft }} className="truncate">
                                  {Object.entries(tb.args).slice(0, 2).map(([k, v]) =>
                                    `${k}=${typeof v === 'string' ? `"${v.slice(0, 30)}"` : JSON.stringify(v).slice(0, 30)}`
                                  ).join(', ')}
                                </span>
                              </span>
                            ) : (
                              <span className="flex items-center gap-1.5">
                                <span style={{ color: ATLAS_PAL.forest, flexShrink: 0 }}>+</span>
                                <span>{tb.name}</span>
                                {tb.preview && (
                                  <span style={{ fontSize: 10, color: ATLAS_PAL.inkSoft }} className="truncate" title={tb.preview}>
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
                      <div className="prose prose-sm max-w-none prose-p:my-1 prose-pre:text-xs prose-pre:whitespace-pre-wrap prose-code:break-all">
                        <ReactMarkdown rehypePlugins={[rehypeRaw]}>{cleanLatexInChat(msg.content.replace(/YAML_READY\n/g, ''))}</ReactMarkdown>
                        {msg.streaming && (
                          <span
                            className="inline-block align-middle ml-0.5 animate-pulse"
                            style={{ width: 6, height: 12, background: ATLAS_PAL.forest }}
                          />
                        )}
                      </div>
                    ) : (
                      <span className="whitespace-pre-wrap">{msg.content}</span>
                    )}
                    {msg.hasYaml && msg.yamlError && (
                      <div
                        className="mt-2"
                        style={{
                          padding: 8,
                          border: `1px solid ${ATLAS_PAL.brick}`,
                          background: '#FBEFE7',
                          color: ATLAS_PAL.brick,
                          fontSize: 11,
                          lineHeight: 1.5,
                        }}
                      >
                        YAML 有問題,建議請 AI 修正後再套用:<br />
                        <code className="break-all">{msg.yamlError}</code>
                      </div>
                    )}
                    {msg.hasYaml && msg.yaml && (
                      // Hero 是新對話入口、永遠建新工作流、不該有「覆蓋」(沒目前 workflow 可覆蓋)
                      // overwrite 按鈕只放 sidebar mode 內、Hero 移除
                      <div className="mt-2">
                        <button
                          onClick={() => handleYamlApplyInHero(msg.yaml!, 'new')}
                          disabled={!!msg.yamlError}
                          title={msg.yamlError
                            ? 'YAML 有解析錯誤、無法套用、請請 AI 重新產出完整 YAML'
                            : '建立一個新的工作流來放這份 YAML'}
                          className="w-full"
                          style={{
                            background: msg.yamlError ? '#D5D2CC' : ATLAS_PAL.forest,
                            color: msg.yamlError ? '#8B8680' : ATLAS_PAL.bg,
                            fontFamily: FONT_MONO, fontSize: 11, letterSpacing: '0.04em',
                            padding: '10px 12px',
                            cursor: msg.yamlError ? 'not-allowed' : 'pointer',
                            borderRadius: 0, border: 'none',
                          }}
                        >+ new workflow</button>
                      </div>
                    )}
                  </div>
                </div>
              ))}
              {heroLoading && (
                <div
                  className="flex items-center gap-2 pl-8"
                  style={{ fontFamily: FONT_MONO, fontSize: 11, color: ATLAS_PAL.inkSoft }}
                >
                  <Loader2 className="w-3 h-3 animate-spin" /> thinking…
                </div>
              )}
              <div ref={chatEndRef} />
            </div>

            {/* Chat-mode input(同 brutalist 樣式、shrink-0 永遠停在 panel 底部)*/}
            <div className="shrink-0" style={{
              background: ATLAS_PAL.bgCard, border: `1px solid ${ATLAS_PAL.ink}`,
              display: 'flex', alignItems: 'flex-start', padding: '12px 14px', gap: 12,
              marginTop: 12, position: 'relative', zIndex: 1,
            }}>
              <span style={{ fontFamily: FONT_MONO, fontSize: 12, color: ATLAS_PAL.forest, paddingTop: 2 }}>{'>'}</span>
              <textarea
                ref={textareaRef}
                value={heroInput}
                onChange={e => setHeroInput(e.target.value)}
                onKeyDown={onKeyDown}
                disabled={heroLoading}
                rows={2}
                placeholder="繼續對話…(Enter 送出 / Shift+Enter 換行)"
                style={{
                  flex: 1, border: 'none', outline: 'none', background: 'transparent',
                  fontFamily: FONT_MONO, fontSize: 13, color: ATLAS_PAL.ink, resize: 'none',
                  lineHeight: 1.45,
                }}
              />
              <button
                onClick={onSubmit}
                disabled={!heroInput.trim() || heroLoading}
                style={{
                  background: heroInput.trim() && !heroLoading ? ATLAS_PAL.ink : ATLAS_PAL.inkSoft,
                  color: ATLAS_PAL.bg, padding: '8px 16px',
                  fontFamily: FONT_MONO, fontSize: 11.5, letterSpacing: '0.04em',
                  cursor: heroInput.trim() && !heroLoading ? 'pointer' : 'not-allowed',
                  border: 'none', borderRadius: 0,
                  display: 'inline-flex', alignItems: 'center', gap: 6,
                }}
              >
                {heroLoading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Send className="w-3.5 h-3.5" />}
                run ↵
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
