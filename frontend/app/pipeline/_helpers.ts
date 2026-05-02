import type { Node, Edge } from '@xyflow/react'

// ── 資料型別 ─────────────────────────────────────────────────────────────────

/** 腳本節點：執行用戶寫好的腳本或指令 */
export interface StepData extends Record<string, unknown> {
  name: string
  batch: string
  workingDir: string
  outputPath: string
  expect: string
  skillMode?: boolean   // optional — 僅在 YAML 序列化時使用，節點類型由 node.type 決定
  readonly?: boolean    // optional — skill 唯讀驗證模式
  skill?: string        // optional — 掛載的 Claude Code skill 名稱
  askMode?: boolean     // optional — 詢問模式（LLM 積極問使用者）
  humanConfirm?: boolean           // optional — 人工確認步驟
  humanConfirmMessage?: string     // optional — 確認訊息
  humanConfirmNotifyTelegram?: boolean  // optional — 是否 Telegram 通知
  humanConfirmScreenshot?: boolean     // optional — 是否自動截圖
  humanConfirmPreview?: boolean        // optional — 是否 render 上一步驟輸出檔案預覽
  humanConfirmSendPrevOutput?: boolean // optional — 抵達節點時自動把上一步輸出檔當 document 傳到 TG
  // 桌面自動化節點（computer_use）
  computerUse?: boolean                  // optional — 桌面自動化步驟
  computerUseActions?: ComputerUseAction[]  // optional — 動作序列
  computerUseAssetsDir?: string          // optional — 錨點圖片資料夾
  computerUseFailFast?: boolean          // optional — 遇錯立即中止
  cvThreshold?: number                   // CV 比對門檻：0.50 寬鬆 / 0.80 標準 / 0.90 嚴格
  cvSearchOnlyNear?: boolean             // true = 只搜錄製座標附近
  cvSearchRadius?: number                // 附近搜尋半徑（px），預設 400
  cvTriggerHover?: boolean               // true = 比對前先觸發 hover 效果（匹配錄製時的 hover 狀態）
  cvHoverWaitMs?: number                 // hover 等待時間（ms）：200 或 400
  cvCoordFallback?: boolean              // true = CV 失敗時退回錄製座標硬點（預設 false = 失敗就停）
  ocrThreshold?: number                  // OCR 最小 conf 門檻（預設 0.6）
  ocrCvFallback?: boolean                // true = OCR 失敗接著 CV 比對（預設 false = 失敗就停）
  // 視覺驗證節點（visual_validation）
  visualValidation?: boolean             // optional — 視覺驗證步驟
  vvSource?: 'prev_output' | 'current_screen'
  vvPrompt?: string
  vvSearchRegion?: number[]              // [left, top, width, height]，空陣列 = 看整個螢幕
  // Outlook 自動化節點（outlook_automation）
  outlookAutomation?: boolean            // optional — Outlook 自動化步驟
  outlookTemplate?: string               // 選單模板 ID（空字串 = 自由輸入需求）
  outlookFreeText?: string               // 自由輸入需求（template 為空時用）
  outlookParams?: Record<string, unknown> // 模板參數（subject/sender/since/until/to ...）
  // 網頁爬蟲節點（web_crawler）
  webCrawler?: boolean                   // optional — 網頁爬蟲步驟
  wcMode?: 'web' | 'video'               // 模式：web=Crawl4AI / video=yt-dlp
  wcUrl?: string                         // [web mode] 向後相容單 URL；wcUrls 為空時用這個
  wcUrls?: string[]                      // [web mode] 多 URL 列表；非空時走多檔輸出
  wcJsRender?: boolean                   // [web mode] 啟用 JS 渲染（SPA 必須開）
  wcWaitForSelector?: string             // [web mode] 等指定 CSS selector 出現再抓
  wcCloudflareFallback?: boolean         // [web mode] CF 偵測到時 fallback FlareSolverr
  wcCookies?: string                     // 登入 cookies（多種格式可接，兩個模式共用）
  wcInteractions?: WebCrawlerAction[]    // [web mode] JS 互動序列
  wcDownloadAssets?: boolean             // [web mode] 把附件下載到 assets/
  // 智慧滾動（取代寫死的「滾 2 次」）— 預設自動偵測 scrollHeight 不變才停
  wcScrollCount?: number                 // [web mode] 0=自動 / >0=固定滾 N 次
  wcTargetPostCount?: number             // [web mode] 0=不設目標 / >0=滾到至少 N 個貼文連結後停
  // 影片模式
  wcVideoUrl?: string                    // [video mode] YouTube/Vimeo/Bilibili URL
  wcVideoQuality?: string                // [video mode] best / 1080p / 720p / 480p / 360p
  wcVideoMaxFilesizeMb?: number          // [video mode] 檔案大小上限（MB）
  wcVideoMaxDurationMin?: number         // [video mode] 影片長度上限（分鐘，0=不限）
  wcVideoSubs?: boolean                  // [video mode] 是否下載字幕
  wcVideoSubsLangs?: string              // [video mode] 字幕語言偏好（逗號分隔）
  wcVideoSaveInfoJson?: boolean          // [video mode] 是否寫 video.info.json（預設 OFF）
  timeout: number
  retry: number
  index: number
  status: 'idle' | 'running' | 'success' | 'failed'
  errorMsg: string
}

/** 技能節點：LLM 自動撰寫並執行程式碼 */
export interface SkillData extends Record<string, unknown> {
  name: string
  taskDescription: string
  workingDir: string
  outputPath: string
  expectedOutput: string
  readonly: boolean
  skill: string         // 掛載的 Claude Code skill 名稱（空字串 = 不掛載）
  askMode: boolean      // 詢問模式：LLM 遇到任何不確定就主動 ask_user 問用戶
  timeout: number
  retry: number
  index: number
  status: 'idle' | 'running' | 'success' | 'failed'
  errorMsg: string
}

/** AI 驗證節點：輕量 LLM 快速驗證前一步輸出 */
export interface AiValidationData extends Record<string, unknown> {
  expectText: string
  targetPath: string
  skillMode: boolean   // 保留：控制驗證時是否可執行程式碼
  index: number
}

/** 人工確認節點：暫停 Pipeline 等待人為確認 */
export interface HumanConfirmData extends Record<string, unknown> {
  name: string
  message: string          // 自訂確認訊息
  notifyTelegram: boolean  // 是否透過 Telegram 通知
  screenshot: boolean      // 是否自動截圖並傳送到 Telegram
  previewPrevOutput: boolean  // 是否 render 上一步驟輸出檔案成 PNG 傳 TG
  sendPrevOutput: boolean  // 是否自動把上一步輸出檔當 document 傳到 TG（手機可下載）
  timeout: number          // 等待超時（秒）
  index: number
  status: 'idle' | 'running' | 'success' | 'failed'
  errorMsg: string
}

// 桌面自動化動作（對應 backend ComputerUseAction）
export interface ComputerUseAction {
  type: 'click_image' | 'click_at' | 'type_text' | 'hotkey' | 'wait' | 'wait_image' | 'screenshot' | 'scroll' | 'drag'
      | 'assert_image' | 'assert_text' | 'activate_window' | 'if_image_found' | 'retry_until' | 'vlm_check'
  image?: string
  image2?: string        // 次錨點（多錨點驗證）
  dx2?: number           // 次錨點相對點擊點的 X 位移
  dy2?: number           // 次錨點相對點擊點的 Y 位移
  x?: number
  y?: number
  x2?: number
  y2?: number
  dy?: number
  text?: string
  keys?: string[]
  seconds?: number
  timeout_sec?: number
  confidence?: number
  button?: 'left' | 'right' | 'middle'
  clicks?: number
  description?: string
  use_coord?: boolean   // 勾起 = 強制用絕對座標，跳過圖像比對
  hold_sec?: number     // click 長按時間（>0 時回放走 mouseDown→sleep→mouseUp）
  modifiers?: string[]  // click 時按著的修飾鍵（如 ["ctrl"]、["ctrl","shift"]）
  use_ocr?: boolean     // click_image 顯式 OCR 啟用（勾選才跑 OCR，避免 silent 填字但沒觸發）
  ocr_text?: string     // OCR 目標文字（跟 use_ocr=true 搭配才生效）
  // OCR 搜尋範圍（藍框，絕對桌面座標；width=0 = 未設定，回退 near_xy+cv_search_radius）
  ocr_box_left?: number
  ocr_box_top?: number
  ocr_box_width?: number
  ocr_box_height?: number
  // 嚴格鎖定範圍：true = 框內找不到立即 fail（不退附近、不退全螢幕）
  ocr_strict_region?: boolean
  anchor_off_x?: number // 點擊相對錨點影像中心的偏移 x
  anchor_off_y?: number // 點擊相對錨點影像中心的偏移 y
  full_image?: string   // 全螢幕截圖檔名（手動圈選編輯錨點時用）
  full_left?: number    // 全螢幕截圖對應的虛擬桌面原點 X（可能是負值）
  full_top?: number     // 全螢幕截圖對應的虛擬桌面原點 Y
  // search_region：CV / OCR / VLM 搜尋矩形（紅框，絕對桌面座標 [l,t,w,h]）
  search_region?: number[]
  // CV 嚴格鎖定範圍：true = 紅框內找不到立即 fail（不退附近、不退全螢幕、不退錄製座標）
  cv_strict_region?: boolean
  // VLM 相關欄位（vlm_check / click_image vlm_mode / 視覺判斷模板用）
  vlm_prompt?: string   // vlm_check 判斷條件、或 vlm_mode=description 的目標描述
  vlm_mode?: 'off' | 'description' | 'anchor_pick'
  vlm_anchors?: string[] // vlm_mode=anchor_pick 用的多張變體錨點圖檔名
  // 控制流：if_image_found / retry_until 用（unknown[] 因為遞迴 dict 巢狀）
  then?: ComputerUseAction[]
  else?: ComputerUseAction[]
  do?: ComputerUseAction[]
  until?: ComputerUseAction
  max_attempts?: number
  wait_between_sec?: number
  // activate_window 用
  title?: string
  title_contains?: string
}

export interface ComputerUseData extends Record<string, unknown> {
  name: string
  actions: ComputerUseAction[]
  assetsDir: string         // 錨點圖片資料夾（相對工作流）
  failFast: boolean         // 遇錯立即中止
  cvThreshold: number       // CV 比對門檻：0.50 寬鬆 / 0.80 標準 / 0.90 嚴格
  cvSearchOnlyNear: boolean // true = 只搜錄製座標附近（找不到直接 FAIL）
  cvSearchRadius: number    // 附近搜尋半徑（px），預設 400
  cvTriggerHover: boolean   // true = 比對前先 moveTo 錄製座標觸發 hover
  cvHoverWaitMs: number     // hover 等待 ms：200（快）/ 400（保險）
  cvCoordFallback: boolean  // true = CV 失敗時退回錄製座標硬點。預設 false（失敗就停，不亂點）
  ocrThreshold: number      // OCR 最小 conf 門檻（1.0/0.9/0.8/0.6 分級；預設 0.6）
  ocrCvFallback: boolean    // true = OCR 失敗時繼續試 CV 比對鏈。預設 false（失敗就停）
  timeout: number           // 秒（執行上限）
  retry: number
  index: number
  status: 'idle' | 'running' | 'success' | 'failed'
  errorMsg: string
}

/** 視覺驗證節點：用 Settings 主模型（必須支援視覺）判斷某個圖像是否符合預期 */
export interface VisualValidationData extends Record<string, unknown> {
  name: string
  source: 'prev_output' | 'current_screen'   // 上一步輸出檔 / 目前螢幕畫面
  prompt: string                              // 描述「應該看到什麼」的判斷條件
  // current_screen 來源時可選的螢幕區域（虛擬桌面絕對座標）。空陣列 = 看整個螢幕
  searchRegion: number[]   // [left, top, width, height]
  index: number
  status: 'idle' | 'running' | 'success' | 'failed'
  errorMsg: string
}

/** 網頁爬蟲動作（panel 互動序列；對應 backend 的 wc_interactions list[dict]）*/
export interface WebCrawlerAction {
  type: 'click' | 'scroll' | 'wait' | 'wait_for' | 'type'
  selector?: string             // click / wait_for / type 用
  to?: 'top' | 'bottom' | 'pixels'  // scroll 用
  pixels?: number               // scroll to=pixels 時
  seconds?: number              // wait 用
  text?: string                 // type 用
}

/** 網頁爬蟲節點：丟一個 URL 進去，吐 markdown + frontmatter 出來給後續 skill 解析 */
export interface WebCrawlerData extends Record<string, unknown> {
  name: string
  mode: 'web' | 'video'            // 模式：web=Crawl4AI 抓網頁 / video=yt-dlp 抓影片
  // 網頁模式
  url: string                      // 向後相容單 URL；建議改用 urls
  urls: string[]                   // 多 URL；> 1 個時 outputPath 解讀為資料夾、自動命名
  jsRender: boolean
  waitForSelector: string
  cloudflareFallback: boolean
  cookies: string                  // 登入 cookies（兩模式共用）
  interactions: WebCrawlerAction[]
  downloadAssets: boolean
  // 智慧滾動（進階設定）
  scrollCount: number              // 0=自動偵測 / >0=固定滾 N 次
  targetPostCount: number          // 0=不設目標 / >0=滾到至少 N 個貼文連結
  // 影片模式
  videoUrl: string
  videoQuality: string             // best / 1080p / 720p / 480p / 360p
  videoMaxFilesizeMb: number       // 預設 500
  videoMaxDurationMin: number      // 預設 30；0 = 不限
  videoSubs: boolean               // 預設 true
  videoSubsLangs: string           // 預設 ''（後端 fallback 'zh-TW,zh-Hant,zh-CN,zh-Hans,en'）
  videoSaveInfoJson: boolean       // 預設 false；勾起來才存 video.info.json
  // 共用
  outputPath: string
  retry: number
  timeout: number
  index: number
  status: 'idle' | 'running' | 'success' | 'failed'
  errorMsg: string
}

/** Outlook 自動化節點：透過 pywin32 + Outlook COM 處理寄信 / 收信 / 行事曆 / 附件 */
export interface OutlookData extends Record<string, unknown> {
  name: string
  // 選單模板 ID（前端選了哪個模板；空字串 = 自由輸入需求模式）
  // 例：daily_todo / search_summary / send_mail / send_with_attachment
  //    download_attachments / 等
  template: string
  // 自由輸入：模板沒勾時，使用者直接打字描述需求（agent 限定 win32 工具集）
  freeText: string
  // 模板參數：依模板而定的鍵值對（subject / sender / since / until / to / folder ...）
  params: Record<string, unknown>
  // 輸出檔路徑（可選；整理結果如 xlsx / md 報告會寫到這）
  outputPath: string
  retry: number
  // 整個步驟的執行上限（秒）。Outlook COM 對巨型收信夾 search_mail 可能跑 4-5 分鐘，
  // 預設 600 秒；30k+ 信箱建議 1800-3600。
  timeout: number
  index: number
  status: 'idle' | 'running' | 'success' | 'failed'
  errorMsg: string
}

export type ScriptNode = Node<StepData>
export type SkillNode = Node<SkillData>
export type AiValidationNode = Node<AiValidationData>
export type HumanConfirmNode = Node<HumanConfirmData>
export type ComputerUseNode = Node<ComputerUseData>
export type VisualValidationNode = Node<VisualValidationData>
export type OutlookNode = Node<OutlookData>
export type WebCrawlerNode = Node<WebCrawlerData>
export type AppNode = Node<StepData | AiValidationData | SkillData | HumanConfirmData | ComputerUseData | VisualValidationData | OutlookData | WebCrawlerData>

export function newAiValidationData(index = 0): AiValidationData {
  return { expectText: '', targetPath: '', skillMode: false, index }
}

let _webCrawlerCounter = 0
export function newWebCrawlerData(index = 0): WebCrawlerData {
  _webCrawlerCounter++
  return {
    name: `網頁爬蟲 ${_webCrawlerCounter}`,
    mode: 'web',
    url: '',
    urls: [],
    jsRender: true,
    waitForSelector: '',
    cloudflareFallback: true,
    cookies: '',
    interactions: [],
    downloadAssets: false,
    scrollCount: 0,
    targetPostCount: 0,
    videoUrl: '',
    videoQuality: '720p',
    videoMaxFilesizeMb: 500,
    videoMaxDurationMin: 30,
    videoSubs: true,
    videoSubsLangs: '',
    videoSaveInfoJson: false,
    outputPath: '',
    retry: 1,
    timeout: 600,  // 影片下載常需要 5-10 分鐘；網頁模式不會用到那麼久也沒影響
    index,
    status: 'idle',
    errorMsg: '',
  }
}

let _outlookCounter = 0
export function newOutlookData(index = 0): OutlookData {
  _outlookCounter++
  return {
    name: `Outlook 自動化 ${_outlookCounter}`,
    template: '',
    freeText: '',
    params: {},
    outputPath: '',
    retry: 0,
    timeout: 600,
    index,
    status: 'idle',
    errorMsg: '',
  }
}

let _visualValidationCounter = 0
export function newVisualValidationData(index = 0): VisualValidationData {
  _visualValidationCounter++
  return {
    name: `視覺驗證 ${_visualValidationCounter}`,
    source: 'prev_output',
    prompt: '',
    searchRegion: [],
    index,
    status: 'idle',
    errorMsg: '',
  }
}

let _confirmCounter = 0
export function newHumanConfirmData(index = 0): HumanConfirmData {
  _confirmCounter++
  return {
    name: `人工確認 ${_confirmCounter}`,
    message: '',
    notifyTelegram: true,
    screenshot: false,
    previewPrevOutput: false,
    sendPrevOutput: false,
    timeout: 3600,
    index,
    status: 'idle',
    errorMsg: '',
  }
}

let _computerUseCounter = 0
export function newComputerUseData(index = 0): ComputerUseData {
  _computerUseCounter++
  return {
    name: `桌面自動化 ${_computerUseCounter}`,
    actions: [],
    assetsDir: '',
    failFast: true,
    cvThreshold: 0.5,
    cvSearchOnlyNear: false,
    cvSearchRadius: 400,
    cvTriggerHover: true,
    cvHoverWaitMs: 200,
    cvCoordFallback: false,
    ocrThreshold: 0.6,
    ocrCvFallback: false,
    timeout: 300,
    retry: 0,
    index,
    status: 'idle',
    errorMsg: '',
  }
}

let _counter = 0
export function newStepData(index = 0): StepData {
  _counter++
  return {
    name: `Python腳本 ${_counter}`,
    batch: '',
    workingDir: '',
    outputPath: '',
    expect: '',
    timeout: 300,
    retry: 0,
    index,
    status: 'idle',
    errorMsg: '',
  }
}

let _skillCounter = 0
export function newSkillData(index = 0): SkillData {
  _skillCounter++
  return {
    name: `AI技能 ${_skillCounter}`,
    taskDescription: '',
    workingDir: '',
    outputPath: '',
    expectedOutput: '',
    readonly: false,
    skill: '',
    askMode: false,
    timeout: 300,
    retry: 0,
    index,
    status: 'idle',
    errorMsg: '',
  }
}

// ── 節點顏色（依 index 循環）──────────────────────────────────────────────────
const COLORS = ['#6366f1','#0ea5e9','#10b981','#f59e0b','#ec4899','#8b5cf6','#14b8a6','#f97316']
export const stepColor = (index: number) => COLORS[index % COLORS.length]

// ── Steps → ReactFlow nodes + edges ──────────────────────────────────────────
export function stepsToFlow(steps: StepData[]): { nodes: AppNode[]; edges: Edge[] } {
  const nodes: AppNode[] = steps.map((s, i) => {
    if (s.computerUse) {
      return {
        id: `step-${i}`,
        type: 'computerUse' as const,
        position: { x: i * 320, y: 160 },
        data: {
          name: s.name,
          actions: s.computerUseActions || [],
          assetsDir: s.computerUseAssetsDir || '',
          failFast: s.computerUseFailFast ?? true,
          cvThreshold: s.cvThreshold ?? 0.5,
          cvSearchOnlyNear: s.cvSearchOnlyNear ?? false,
          cvSearchRadius: s.cvSearchRadius ?? 400,
          cvTriggerHover: s.cvTriggerHover ?? true,
          cvHoverWaitMs: s.cvHoverWaitMs ?? 200,
          cvCoordFallback: s.cvCoordFallback ?? false,
          ocrThreshold: s.ocrThreshold ?? 0.6,
          ocrCvFallback: s.ocrCvFallback ?? false,
          timeout: s.timeout,
          retry: s.retry,
          index: i,
          status: 'idle' as const,
          errorMsg: '',
        } as ComputerUseData,
      }
    }
    if (s.visualValidation) {
      return {
        id: `step-${i}`,
        type: 'visualValidation' as const,
        position: { x: i * 320, y: 160 },
        data: {
          name: s.name,
          source: (s.vvSource === 'current_screen' ? 'current_screen' : 'prev_output') as 'prev_output' | 'current_screen',
          prompt: s.vvPrompt || '',
          searchRegion: Array.isArray(s.vvSearchRegion) ? s.vvSearchRegion : [],
          index: i,
          status: 'idle' as const,
          errorMsg: '',
        } as VisualValidationData,
      }
    }
    if (s.outlookAutomation) {
      return {
        id: `step-${i}`,
        type: 'outlookAutomation' as const,
        position: { x: i * 320, y: 160 },
        data: {
          name: s.name,
          template: s.outlookTemplate || '',
          freeText: s.outlookFreeText || '',
          params: (s.outlookParams as Record<string, unknown>) || {},
          outputPath: s.outputPath,
          retry: s.retry,
          index: i,
          status: 'idle' as const,
          errorMsg: '',
        } as OutlookData,
      }
    }
    if (s.webCrawler) {
      return {
        id: `step-${i}`,
        type: 'webCrawler' as const,
        position: { x: i * 320, y: 160 },
        data: {
          name: s.name,
          mode: (s.wcMode === 'video' ? 'video' : 'web') as 'web' | 'video',
          url: s.wcUrl || '',
          urls: Array.isArray(s.wcUrls) ? s.wcUrls : [],
          jsRender: s.wcJsRender ?? true,
          waitForSelector: s.wcWaitForSelector || '',
          cloudflareFallback: s.wcCloudflareFallback ?? true,
          cookies: s.wcCookies || '',
          interactions: Array.isArray(s.wcInteractions) ? s.wcInteractions : [],
          downloadAssets: s.wcDownloadAssets ?? false,
          scrollCount: s.wcScrollCount ?? 0,
          targetPostCount: s.wcTargetPostCount ?? 0,
          videoUrl: s.wcVideoUrl || '',
          videoQuality: s.wcVideoQuality || '720p',
          videoMaxFilesizeMb: s.wcVideoMaxFilesizeMb ?? 500,
          videoMaxDurationMin: s.wcVideoMaxDurationMin ?? 30,
          videoSubs: s.wcVideoSubs ?? true,
          videoSubsLangs: s.wcVideoSubsLangs || '',
          videoSaveInfoJson: s.wcVideoSaveInfoJson ?? false,
          outputPath: s.outputPath,
          retry: s.retry,
          timeout: s.timeout || 600,
          index: i,
          status: 'idle' as const,
          errorMsg: '',
        } as WebCrawlerData,
      }
    }
    if (s.humanConfirm) {
      return {
        id: `step-${i}`,
        type: 'humanConfirmation' as const,
        position: { x: i * 320, y: 160 },
        data: {
          name: s.name,
          message: s.humanConfirmMessage || '',
          notifyTelegram: s.humanConfirmNotifyTelegram ?? true,
          screenshot: s.humanConfirmScreenshot ?? false,
          previewPrevOutput: s.humanConfirmPreview ?? false,
          sendPrevOutput: s.humanConfirmSendPrevOutput ?? false,
          timeout: s.timeout || 3600,
          index: i,
          status: 'idle' as const,
          errorMsg: '',
        } as HumanConfirmData,
      }
    }
    if (s.skillMode) {
      // 向後相容：舊格式 skillMode=true → skillStep 節點
      return {
        id: `step-${i}`,
        type: 'skillStep' as const,
        position: { x: i * 320, y: 160 },
        data: {
          name: s.name,
          taskDescription: s.batch,
          workingDir: s.workingDir,
          outputPath: s.outputPath,
          expectedOutput: s.expect,
          readonly: s.readonly || false,
          skill: s.skill || '',
          askMode: s.askMode || false,
          timeout: s.timeout,
          retry: s.retry,
          index: i,
          status: 'idle' as const,
          errorMsg: '',
        } as SkillData,
      }
    }
    return {
      id: `step-${i}`,
      type: 'scriptStep' as const,
      position: { x: i * 320, y: 160 },
      data: { ...s, index: i, skillMode: undefined },
    }
  })

  // 用 insertable type — hover 出 + / 🗑️；箭頭由 ReactFlow defaultEdgeOptions 統一處理
  const edges: Edge[] = steps.slice(0, -1).map((_, i) => ({
    id: `e-${i}`,
    source: `step-${i}`,
    target: `step-${i + 1}`,
    type: 'insertable',
    animated: steps[i].status === 'running',
    style: { stroke: stepColor(i), strokeWidth: 2 },
    markerEnd: { type: 'arrowclosed' as any, color: stepColor(i), width: 18, height: 18 },
  }))

  return { nodes, edges }
}

// ── ReactFlow nodes → ordered steps（只包含有邊連接的節點）──────────────────────
export function flowToSteps(nodes: AppNode[], edges: Edge[]): StepData[] {
  // 收集 AI 驗證節點，建立 predecessor → aiData 映射
  const aiNodeIds = new Set<string>()
  const aiDataByPredecessor = new Map<string, AiValidationData>()

  for (const n of nodes) {
    if (n.type === 'aiValidation') {
      aiNodeIds.add(n.id)
      const inEdge = edges.find(e => e.target === n.id)
      if (inEdge) aiDataByPredecessor.set(inEdge.source, n.data as AiValidationData)
    }
  }

  // 過濾出可執行節點（scriptStep + skillStep + humanConfirmation + computerUse + visualValidation）
  const execNodeIds = new Set<string>()
  const execNodes: AppNode[] = []
  for (const n of nodes) {
    if (n.type === 'scriptStep' || n.type === 'skillStep' || n.type === 'humanConfirmation'
        || n.type === 'computerUse' || n.type === 'visualValidation'
        || n.type === 'outlookAutomation' || n.type === 'webCrawler') {
      execNodeIds.add(n.id)
      execNodes.push(n)
    }
  }
  if (execNodes.length === 0) return []

  // 建立虛擬邊（跳過 AI 驗證節點）
  const virtualEdges: Edge[] = []
  for (const e of edges) {
    if (aiNodeIds.has(e.source)) continue
    if (aiNodeIds.has(e.target)) {
      const aiOutEdge = edges.find(e2 => e2.source === e.target)
      if (aiOutEdge && execNodeIds.has(aiOutEdge.target)) {
        virtualEdges.push({ ...e, target: aiOutEdge.target, id: `v-${e.id}` })
      }
      continue
    }
    if (execNodeIds.has(e.source) && execNodeIds.has(e.target)) {
      virtualEdges.push(e)
    }
  }

  // 找起點（無入邊的節點）
  const hasIncoming = new Set(virtualEdges.map(e => e.target))
  const starts = execNodes.filter(n => !hasIncoming.has(n.id))
  if (!starts.length) return []

  // 沿邊走、收集有連接的節點。
  // 之前用 Map<source,target>（單一 target）→ 同 source 多條出邊只保留最後一條、
  // 後寫覆蓋前寫；使用者「插入中間節點忘記刪舊邊」會看運氣決定走不走中間節點，
  // 而且中間節點會被當「孤立節點」靜默丟掉（user 收不到任何警告）。
  // 改成 multimap + DFS 找最長路徑：插入新節點即使保留舊邊、新路徑也會被選中。
  const adjMulti = new Map<string, string[]>()
  for (const e of virtualEdges) {
    if (!adjMulti.has(e.source)) adjMulti.set(e.source, [])
    adjMulti.get(e.source)!.push(e.target)
  }
  // DFS 找最長路徑；visited 防 cycle、子探索用 set copy 不互相污染
  const longestFrom = (node: string, visited: Set<string>): string[] => {
    if (visited.has(node)) return []
    const next = new Set(visited); next.add(node)
    const targets = adjMulti.get(node) || []
    if (targets.length === 0) return [node]
    let best: string[] = []
    for (const t of targets) {
      const sub = longestFrom(t, next)
      if (sub.length > best.length) best = sub
    }
    return [node, ...best]
  }
  const orderIds = longestFrom(starts[0].id, new Set<string>())
  const ordered: AppNode[] = []
  for (const id of orderIds) {
    const node = execNodes.find(n => n.id === id)
    if (node) ordered.push(node)
  }

  // 孤立節點不加入（邊驅動執行；DFS 已偏好最長路徑、避免中間節點被丟掉）

  return ordered.map((n, i) => {
    const aiData = aiDataByPredecessor.get(n.id)

    if (n.type === 'computerUse') {
      const d = n.data as ComputerUseData
      return {
        name: d.name,
        batch: '',
        workingDir: '',
        outputPath: '',
        expect: '',
        computerUse: true,
        computerUseActions: d.actions,
        computerUseAssetsDir: d.assetsDir,
        computerUseFailFast: d.failFast,
        cvThreshold: d.cvThreshold,
        cvSearchOnlyNear: d.cvSearchOnlyNear,
        cvSearchRadius: d.cvSearchRadius,
        cvTriggerHover: d.cvTriggerHover,
        cvHoverWaitMs: d.cvHoverWaitMs,
        ocrThreshold: d.ocrThreshold,
        ocrCvFallback: d.ocrCvFallback,
        cvCoordFallback: d.cvCoordFallback,
        timeout: d.timeout,
        retry: d.retry,
        index: i,
        status: d.status,
        errorMsg: d.errorMsg,
      } as StepData
    }

    if (n.type === 'visualValidation') {
      const d = n.data as VisualValidationData
      return {
        name: d.name,
        batch: '',
        workingDir: '',
        outputPath: '',
        expect: '',
        visualValidation: true,
        vvSource: d.source,
        vvPrompt: d.prompt,
        vvSearchRegion: d.searchRegion && d.searchRegion.length === 4 ? d.searchRegion : [],
        timeout: 120,
        retry: 0,
        index: i,
        status: d.status,
        errorMsg: d.errorMsg,
      } as StepData
    }
    if (n.type === 'outlookAutomation') {
      const d = n.data as OutlookData
      return {
        name: d.name,
        batch: d.freeText || '',          // batch 欄位塞自由輸入；agent 跑時會優先看 outlookTemplate
        workingDir: '',
        outputPath: d.outputPath,
        expect: '',
        outlookAutomation: true,
        outlookTemplate: d.template,
        outlookFreeText: d.freeText,
        outlookParams: d.params,
        timeout: typeof d.timeout === 'number' && d.timeout > 0 ? d.timeout : 600,
        retry: d.retry,
        index: i,
        status: d.status,
        errorMsg: d.errorMsg,
      } as StepData
    }
    if (n.type === 'webCrawler') {
      const d = n.data as WebCrawlerData
      return {
        name: d.name,
        batch: '',
        workingDir: '',
        outputPath: d.outputPath,
        expect: '',
        webCrawler: true,
        wcMode: d.mode,
        wcUrl: d.url,
        wcUrls: d.urls,
        wcJsRender: d.jsRender,
        wcWaitForSelector: d.waitForSelector,
        wcCloudflareFallback: d.cloudflareFallback,
        wcCookies: d.cookies,
        wcInteractions: d.interactions,
        wcDownloadAssets: d.downloadAssets,
        wcScrollCount: d.scrollCount ?? 0,
        wcTargetPostCount: d.targetPostCount ?? 0,
        wcVideoUrl: d.videoUrl,
        wcVideoQuality: d.videoQuality,
        wcVideoMaxFilesizeMb: d.videoMaxFilesizeMb,
        wcVideoMaxDurationMin: d.videoMaxDurationMin,
        wcVideoSubs: d.videoSubs,
        wcVideoSubsLangs: d.videoSubsLangs,
        wcVideoSaveInfoJson: d.videoSaveInfoJson,
        timeout: typeof d.timeout === 'number' && d.timeout > 0 ? d.timeout : 600,
        retry: d.retry,
        index: i,
        status: d.status,
        errorMsg: d.errorMsg,
      } as StepData
    }
    if (n.type === 'humanConfirmation') {
      const d = n.data as HumanConfirmData
      return {
        name: d.name,
        batch: '',
        workingDir: '',
        outputPath: '',
        expect: '',
        humanConfirm: true,
        humanConfirmMessage: d.message,
        humanConfirmNotifyTelegram: d.notifyTelegram,
        humanConfirmScreenshot: d.screenshot,
        humanConfirmPreview: d.previewPrevOutput,
        humanConfirmSendPrevOutput: d.sendPrevOutput,
        timeout: d.timeout,
        retry: 0,
        index: i,
        status: d.status,
        errorMsg: d.errorMsg,
      } as StepData
    }

    if (n.type === 'skillStep') {
      const d = n.data as SkillData
      return {
        name: d.name,
        batch: d.taskDescription,
        workingDir: d.workingDir || '',
        outputPath: d.outputPath,
        expect: aiData?.expectText || d.expectedOutput,
        skillMode: true,
        readonly: d.readonly || false,
        skill: d.skill || '',
        askMode: d.askMode || false,
        timeout: d.timeout,
        retry: d.retry,
        index: i,
        status: d.status,
        errorMsg: d.errorMsg,
      } as StepData
    }

    const d = n.data as StepData
    return {
      name: d.name,
      batch: d.batch,
      workingDir: d.workingDir || '',
      outputPath: (aiData?.targetPath && !d.outputPath) ? aiData.targetPath : d.outputPath,
      expect: aiData?.expectText || d.expect,
      skillMode: aiData?.skillMode || false,
      timeout: d.timeout,
      retry: d.retry,
      index: i,
      status: d.status,
      errorMsg: d.errorMsg,
    } as StepData
  })
}

// ── Steps → YAML string ───────────────────────────────────────────────────────
export function stepsToYaml(name: string, steps: StepData[]): string {
  // 自動判斷 validate：有 skill 步驟或任何步驟有 expect → 啟用
  const needsValidate = steps.some(s => s.skillMode || !!s.expect)
  const lines: string[] = [
    `name: ${name || 'my-pipeline'}`,
    `validate: ${needsValidate}`,
    ``,
    `steps:`,
  ]
  for (const s of steps) {
    lines.push(`  - name: ${s.name}`)
    if (s.humanConfirm) {
      lines.push(`    human_confirm: true`)
      if (s.humanConfirmMessage) lines.push(`    message: "${s.humanConfirmMessage.replace(/"/g, '\\"')}"`)
      if (s.humanConfirmNotifyTelegram === false) lines.push(`    notify_telegram: false`)
      if (s.humanConfirmScreenshot) lines.push(`    screenshot: true`)
      if (s.humanConfirmPreview) lines.push(`    preview_prev_output: true`)
      if (s.humanConfirmSendPrevOutput) lines.push(`    send_prev_output: true`)
      if (s.timeout && s.timeout !== 3600) lines.push(`    timeout: ${s.timeout}`)
      continue
    }
    if (s.visualValidation) {
      lines.push(`    visual_validation: true`)
      lines.push(`    vv_source: ${s.vvSource || 'prev_output'}`)
      const vvp = s.vvPrompt || ''
      if (vvp) {
        if (vvp.includes('\n') || vvp.length > 80) {
          lines.push(`    vv_prompt: |`)
          for (const dl of vvp.split('\n')) {
            lines.push(`      ${dl}`)
          }
        } else {
          lines.push(`    vv_prompt: "${vvp.replace(/"/g, '\\"')}"`)
        }
      }
      if (s.vvSearchRegion && s.vvSearchRegion.length === 4) {
        lines.push(`    vv_search_region: [${s.vvSearchRegion.join(', ')}]`)
      }
      if (s.timeout && s.timeout !== 120) lines.push(`    timeout: ${s.timeout}`)
      continue
    }
    if (s.webCrawler) {
      lines.push(`    web_crawler: true`)
      if (s.wcMode && s.wcMode !== 'web') lines.push(`    wc_mode: ${s.wcMode}`)
      // 共用 cookies
      const ck = s.wcCookies || ''
      if (ck) {
        if (ck.includes('\n') || ck.length > 80) {
          lines.push(`    wc_cookies: |`)
          for (const dl of ck.split('\n')) lines.push(`      ${dl}`)
        } else {
          lines.push(`    wc_cookies: "${ck.replace(/"/g, '\\"')}"`)
        }
      }
      if (s.wcMode === 'video') {
        if (s.wcVideoUrl) lines.push(`    wc_video_url: "${s.wcVideoUrl.replace(/"/g, '\\"')}"`)
        if (s.wcVideoQuality && s.wcVideoQuality !== '720p') lines.push(`    wc_video_quality: ${s.wcVideoQuality}`)
        if (s.wcVideoMaxFilesizeMb !== undefined && s.wcVideoMaxFilesizeMb !== 500) lines.push(`    wc_video_max_filesize_mb: ${s.wcVideoMaxFilesizeMb}`)
        if (s.wcVideoMaxDurationMin !== undefined && s.wcVideoMaxDurationMin !== 30) lines.push(`    wc_video_max_duration_min: ${s.wcVideoMaxDurationMin}`)
        if (s.wcVideoSubs === false) lines.push(`    wc_video_subs: false`)
        if (s.wcVideoSubsLangs) lines.push(`    wc_video_subs_langs: "${s.wcVideoSubsLangs.replace(/"/g, '\\"')}"`)
        if (s.wcVideoSaveInfoJson === true) lines.push(`    wc_video_save_info_json: true`)
      } else {
        // 過濾使用者貼上的空行 / # 註解、只序列化有效 URL
        const validUrls = (s.wcUrls || [])
          .map(u => u.trim())
          .filter(u => u && !u.startsWith('#'))
        if (validUrls.length > 0) {
          lines.push(`    wc_urls:`)
          for (const u of validUrls) {
            lines.push(`      - "${u.replace(/"/g, '\\"')}"`)
          }
        } else if (s.wcUrl) {
          lines.push(`    wc_url: "${s.wcUrl.replace(/"/g, '\\"')}"`)
        }
        if (s.wcJsRender === false) lines.push(`    wc_js_render: false`)
        if (s.wcWaitForSelector) lines.push(`    wc_wait_for_selector: "${s.wcWaitForSelector.replace(/"/g, '\\"')}"`)
        if (s.wcCloudflareFallback === false) lines.push(`    wc_cloudflare_fallback: false`)
        if (s.wcInteractions && s.wcInteractions.length > 0) {
          lines.push(`    wc_interactions:`)
          for (const a of s.wcInteractions) {
            lines.push(`      - ${JSON.stringify(a)}`)
          }
        }
        if (s.wcDownloadAssets === true) lines.push(`    wc_download_assets: true`)
        // 智慧滾動：只在使用者有指定（非預設 0）時才寫入 yaml — 預設行為已在後端處理
        if (typeof s.wcScrollCount === 'number' && s.wcScrollCount > 0) {
          lines.push(`    wc_scroll_count: ${s.wcScrollCount}`)
        }
        if (typeof s.wcTargetPostCount === 'number' && s.wcTargetPostCount > 0) {
          lines.push(`    wc_target_post_count: ${s.wcTargetPostCount}`)
        }
      }
      if (s.outputPath) {
        lines.push(`    output:`)
        lines.push(`      path: ${s.outputPath}`)
      }
      if (s.timeout && s.timeout !== 600) lines.push(`    timeout: ${s.timeout}`)
      if (s.retry !== undefined && s.retry !== 1) lines.push(`    retry: ${s.retry}`)
      continue
    }
    if (s.outlookAutomation) {
      lines.push(`    outlook_automation: true`)
      if (s.outlookTemplate) lines.push(`    outlook_template: ${s.outlookTemplate}`)
      const ft = s.outlookFreeText || ''
      if (ft) {
        if (ft.includes('\n') || ft.length > 80) {
          lines.push(`    batch: |`)
          for (const dl of ft.split('\n')) lines.push(`      ${dl}`)
        } else {
          lines.push(`    batch: "${ft.replace(/"/g, '\\"')}"`)
        }
      }
      // outlook_params：序列化成 JSON 一行 — 最簡單也保守
      if (s.outlookParams && Object.keys(s.outlookParams).length > 0) {
        lines.push(`    outlook_params: ${JSON.stringify(s.outlookParams)}`)
      }
      if (s.outputPath) {
        lines.push(`    output:`)
        lines.push(`      path: ${s.outputPath}`)
      }
      if (s.timeout && s.timeout !== 600) lines.push(`    timeout: ${s.timeout}`)
      if (s.retry && s.retry !== 0) lines.push(`    retry: ${s.retry}`)
      continue
    }
    if (s.computerUse) {
      lines.push(`    computer_use: true`)
      if (s.computerUseAssetsDir) lines.push(`    assets_dir: ${s.computerUseAssetsDir}`)
      if (s.computerUseFailFast === false) lines.push(`    fail_fast: false`)
      if (s.cvThreshold !== undefined && s.cvThreshold !== 0.5) lines.push(`    cv_threshold: ${s.cvThreshold}`)
      if (s.cvSearchOnlyNear) lines.push(`    cv_search_only_near: true`)
      if (s.cvSearchRadius !== undefined && s.cvSearchRadius !== 400) lines.push(`    cv_search_radius: ${s.cvSearchRadius}`)
      if (s.cvTriggerHover === false) lines.push(`    cv_trigger_hover: false`)
      if (s.cvHoverWaitMs !== undefined && s.cvHoverWaitMs !== 200) lines.push(`    cv_hover_wait_ms: ${s.cvHoverWaitMs}`)
      // cv_coord_fallback 預設 false → 只在 true 時寫入
      if (s.cvCoordFallback === true) lines.push(`    cv_coord_fallback: true`)
      if (s.ocrThreshold !== undefined && s.ocrThreshold !== 0.6) lines.push(`    ocr_threshold: ${s.ocrThreshold}`)
      if (s.ocrCvFallback === true) lines.push(`    ocr_cv_fallback: true`)
      if (s.computerUseActions && s.computerUseActions.length > 0) {
        // 以 JSON 陣列寫入 actions（一行一動作，夠精簡又能 yaml parse）
        lines.push(`    actions:`)
        for (const a of s.computerUseActions) {
          // 用 flow 寫法把每個 action 壓成一行 JSON
          const compact = JSON.stringify(a)
          lines.push(`      - ${compact}`)
        }
      }
      if (s.timeout !== 300) lines.push(`    timeout: ${s.timeout}`)
      // computer_use 一定寫 retry（即使是 0），因為 backend PipelineStep 預設 retry=1
      // 對 UI 自動化來說 retry 從動作 #1 重跑會重複點擊造成副作用，所以預期是 retry=0
      lines.push(`    retry: ${s.retry ?? 0}`)
      continue
    }
    if (s.workingDir) lines.push(`    working_dir: ${s.workingDir}`)
    if (s.batch) {
      if (s.batch.includes('\n') || s.batch.length > 80) {
        lines.push(`    batch: |`)
        for (const bl of s.batch.split('\n')) {
          lines.push(`      ${bl}`)
        }
      } else {
        lines.push(`    batch: ${s.batch}`)
      }
    }
    if (s.skillMode) lines.push(`    skill_mode: true`)
    if (s.skill) lines.push(`    skill: ${s.skill}`)
    if (s.readonly) lines.push(`    readonly: true`)
    if (s.askMode) lines.push(`    ask_mode: true`)
    if (s.outputPath || s.expect) {
      lines.push(`    output:`)
      if (s.outputPath) lines.push(`      path: ${s.outputPath}`)
      if (s.expect) {
        lines.push(`      ai_validation: true`)
        if (s.expect.includes('\n') || s.expect.length > 80) {
          lines.push(`      description: |`)
          for (const dl of s.expect.split('\n')) {
            lines.push(`        ${dl}`)
          }
        } else {
          lines.push(`      description: "${s.expect.replace(/"/g, '\\"')}"`)
        }
      }
      if (s.skillMode) lines.push(`      skill_mode: true`)
    }
    if (s.timeout !== 300) lines.push(`    timeout: ${s.timeout}`)
    if (s.retry > 0)       lines.push(`    retry: ${s.retry}`)
  }
  return lines.join('\n')
}

// ── YAML string → steps ───────────────────────────────────────────────────────
export function parseYaml(raw: string): { name: string; validate: boolean; steps: StepData[] } | null {
  try {
    const lines = raw.split('\n')
    let stepIndent = 2
    for (const line of lines) {
      const m = line.match(/^(\s*)- name:/)
      if (m) { stepIndent = m[1].length; break }
    }

    let name = 'my-pipeline'
    let validate = false
    const steps: StepData[] = []
    let cur: Partial<StepData> | null = null
    let inOutput = false
    let multilineTarget: 'batch' | 'expect' | 'vv_prompt' | 'wc_cookies' | null = null
    let multilineIndent = 0
    let multilineLines: string[] = []

    const flushMultiline = () => {
      if (multilineTarget && cur && multilineLines.length > 0) {
        const text = multilineLines.join('\n').replace(/\n+$/, '')
        if (multilineTarget === 'batch') cur.batch = text
        else if (multilineTarget === 'vv_prompt') cur.vvPrompt = text
        else if (multilineTarget === 'wc_cookies') cur.wcCookies = text
        else cur.expect = text
      }
      multilineTarget = null
      multilineLines = []
      multilineIndent = 0
    }

    for (let li = 0; li < lines.length; li++) {
      const line = lines[li]
      const t = line.trim()

      if (multilineTarget) {
        if (t === '') { multilineLines.push(''); continue }
        const leadingSpaces = line.match(/^(\s*)/)?.[1].length ?? 0
        if (leadingSpaces >= multilineIndent) {
          multilineLines.push(line.slice(multilineIndent))
          continue
        }
        flushMultiline()
      }

      if (!t || t.startsWith('#') || t === 'pipeline:' || t === 'steps:') continue

      if (/^name:/.test(t) && !cur) {
        name = t.replace(/^name:\s*/, '')
      } else if (/^validate:/.test(t) && !cur) {
        validate = /true/.test(t)
      } else if (/^- name:/.test(t)) {
        flushMultiline()
        if (cur) steps.push(buildStep(cur, steps.length))
        cur = { name: t.replace(/^-\s*name:\s*/, '') }
        inOutput = false
      } else if (/^working_dir:/.test(t) && cur) {
        cur.workingDir = t.replace(/^working_dir:\s*/, '')
        inOutput = false
      } else if (/^batch:/.test(t) && cur) {
        const val = t.replace(/^batch:\s*/, '')
        if (val === '|' || val === '>') {
          multilineTarget = 'batch'
          const nextLine = lines[li + 1]
          multilineIndent = nextLine ? (nextLine.match(/^(\s*)/)?.[1].length ?? 0) : 0
        } else {
          cur.batch = val
        }
        inOutput = false
      } else if (/^output:/.test(t) && cur) {
        inOutput = true
      } else if (/^path:/.test(t) && cur && inOutput) {
        cur.outputPath = t.replace(/^path:\s*/, '')
      } else if (/^(expect|description):/.test(t) && cur && inOutput) {
        const val = t.replace(/^(expect|description):\s*/, '').replace(/^"|"$/g, '')
        if (val === '|' || val === '>') {
          multilineTarget = 'expect'
          const nextLine = lines[li + 1]
          multilineIndent = nextLine ? (nextLine.match(/^(\s*)/)?.[1].length ?? 0) : 0
        } else {
          cur.expect = val
        }
      } else if (/^ai_validation:/.test(t) && cur && inOutput) {
        if (/true/.test(t)) validate = true
      } else if (/^skill_mode:/.test(t) && cur) {
        cur.skillMode = /true/.test(t)
      } else if (/^skill:/.test(t) && cur) {
        cur.skill = t.replace(/^skill:\s*/, '').replace(/^"|"$/g, '')
      } else if (/^readonly:/.test(t) && cur) {
        cur.readonly = /true/.test(t)
      } else if (/^ask_mode:/.test(t) && cur) {
        cur.askMode = /true/.test(t)
      } else if (/^human_confirm:/.test(t) && cur) {
        cur.humanConfirm = /true/.test(t)
      } else if (/^message:/.test(t) && cur) {
        cur.humanConfirmMessage = t.replace(/^message:\s*/, '').replace(/^"|"$/g, '')
      } else if (/^notify_telegram:/.test(t) && cur) {
        cur.humanConfirmNotifyTelegram = /true/.test(t)
      } else if (/^screenshot:/.test(t) && cur) {
        cur.humanConfirmScreenshot = /true/.test(t)
      } else if (/^preview_prev_output:/.test(t) && cur) {
        cur.humanConfirmPreview = /true/.test(t)
      } else if (/^send_prev_output:/.test(t) && cur) {
        cur.humanConfirmSendPrevOutput = /true/.test(t)
      } else if (/^web_crawler:/.test(t) && cur) {
        cur.webCrawler = /true/.test(t)
      } else if (/^wc_mode:/.test(t) && cur) {
        const v = t.replace(/^wc_mode:\s*/, '').replace(/^"|"$/g, '').trim()
        cur.wcMode = (v === 'video' ? 'video' : 'web')
      } else if (/^wc_url:/.test(t) && cur) {
        cur.wcUrl = t.replace(/^wc_url:\s*/, '').replace(/^"|"$/g, '')
      } else if (/^wc_urls:/.test(t) && cur) {
        cur.wcUrls = []
      } else if (/^- ".*"$/.test(t) && cur && Array.isArray(cur.wcUrls)) {
        // wc_urls 的列表項目（每行 "url"）
        cur.wcUrls.push(t.replace(/^-\s*"/, '').replace(/"$/, ''))
      } else if (/^wc_js_render:/.test(t) && cur) {
        cur.wcJsRender = /true/.test(t)
      } else if (/^wc_wait_for_selector:/.test(t) && cur) {
        cur.wcWaitForSelector = t.replace(/^wc_wait_for_selector:\s*/, '').replace(/^"|"$/g, '')
      } else if (/^wc_cloudflare_fallback:/.test(t) && cur) {
        cur.wcCloudflareFallback = /true/.test(t)
      } else if (/^wc_download_assets:/.test(t) && cur) {
        cur.wcDownloadAssets = /true/.test(t)
      } else if (/^wc_scroll_count:/.test(t) && cur) {
        cur.wcScrollCount = parseInt(t.replace(/^wc_scroll_count:\s*/, '')) || 0
      } else if (/^wc_target_post_count:/.test(t) && cur) {
        cur.wcTargetPostCount = parseInt(t.replace(/^wc_target_post_count:\s*/, '')) || 0
      } else if (/^wc_video_url:/.test(t) && cur) {
        cur.wcVideoUrl = t.replace(/^wc_video_url:\s*/, '').replace(/^"|"$/g, '')
      } else if (/^wc_video_quality:/.test(t) && cur) {
        cur.wcVideoQuality = t.replace(/^wc_video_quality:\s*/, '').replace(/^"|"$/g, '')
      } else if (/^wc_video_max_filesize_mb:/.test(t) && cur) {
        cur.wcVideoMaxFilesizeMb = parseInt(t.replace(/^wc_video_max_filesize_mb:\s*/, '')) || 500
      } else if (/^wc_video_max_duration_min:/.test(t) && cur) {
        cur.wcVideoMaxDurationMin = parseInt(t.replace(/^wc_video_max_duration_min:\s*/, '')) || 0
      } else if (/^wc_video_subs:/.test(t) && cur) {
        cur.wcVideoSubs = /true/.test(t)
      } else if (/^wc_video_subs_langs:/.test(t) && cur) {
        cur.wcVideoSubsLangs = t.replace(/^wc_video_subs_langs:\s*/, '').replace(/^"|"$/g, '')
      } else if (/^wc_video_save_info_json:/.test(t) && cur) {
        cur.wcVideoSaveInfoJson = /true/.test(t)
      } else if (/^wc_cookies:/.test(t) && cur) {
        const val = t.replace(/^wc_cookies:\s*/, '').replace(/^"|"$/g, '')
        if (val === '|' || val === '>') {
          multilineTarget = 'wc_cookies'
          const nextLine = lines[li + 1]
          multilineIndent = nextLine ? (nextLine.match(/^(\s*)/)?.[1].length ?? 0) : 0
        } else {
          cur.wcCookies = val
        }
      } else if (/^wc_interactions:/.test(t) && cur) {
        // 後面跟著一串 - { JSON } 行；抓下面到 indent 縮回去為止
        cur.wcInteractions = []
      } else if (/^- \{/.test(t) && cur && Array.isArray(cur.wcInteractions)) {
        // wc_interactions 的 JSON 陣列項目
        try {
          const obj = JSON.parse(t.replace(/^-\s*/, ''))
          cur.wcInteractions.push(obj)
        } catch { /* ignore */ }
      } else if (/^visual_validation:/.test(t) && cur) {
        cur.visualValidation = /true/.test(t)
      } else if (/^vv_source:/.test(t) && cur) {
        const v = t.replace(/^vv_source:\s*/, '').replace(/^"|"$/g, '').trim()
        // 相容舊值 prev_output_file / rendered_preview → 一律轉 prev_output
        cur.vvSource = (v === 'current_screen' ? 'current_screen' : 'prev_output')
      } else if (/^vv_prompt:/.test(t) && cur) {
        const val = t.replace(/^vv_prompt:\s*/, '').replace(/^"|"$/g, '')
        if (val === '|' || val === '>') {
          multilineTarget = 'vv_prompt'
          const nextLine = lines[li + 1]
          multilineIndent = nextLine ? (nextLine.match(/^(\s*)/)?.[1].length ?? 0) : 0
        } else {
          cur.vvPrompt = val
        }
      } else if (/^vv_search_region:/.test(t) && cur) {
        // 格式 [l, t, w, h]
        const m = t.match(/\[([^\]]+)\]/)
        if (m) {
          const arr = m[1].split(',').map(x => parseInt(x.trim()) || 0)
          if (arr.length === 4) cur.vvSearchRegion = arr
        }
      } else if (/^outlook_automation:/.test(t) && cur) {
        cur.outlookAutomation = /true/.test(t)
      } else if (/^outlook_template:/.test(t) && cur) {
        cur.outlookTemplate = t.replace(/^outlook_template:\s*/, '').replace(/^"|"$/g, '').trim()
      } else if (/^outlook_params:/.test(t) && cur) {
        // 兩種格式都接：
        //   ✓ inline JSON：outlook_params: {"to":"x@y.com",...}
        //   ✓ multi-line YAML：outlook_params:↵  to: x@y.com↵  subject: ...
        // AI 助手本來指示用 inline JSON 但偶爾仍會吐 multi-line、這裡兜底
        const raw = t.replace(/^outlook_params:\s*/, '').trim()
        if (raw) {
          try { cur.outlookParams = JSON.parse(raw) } catch { /* 壞 JSON 就略過 */ }
        } else {
          // multi-line YAML：往下讀比 outlook_params 縮排更深的 key: value 行
          const baseIndent = line.match(/^(\s*)/)?.[1].length ?? 0
          const params: Record<string, unknown> = {}
          let lj = li + 1
          while (lj < lines.length) {
            const sub = lines[lj]
            const subTrim = sub.trim()
            if (!subTrim) { lj++; continue }
            const subIndent = sub.match(/^(\s*)/)?.[1].length ?? 0
            if (subIndent <= baseIndent) break  // 縮排回去了 → 結束
            const m = subTrim.match(/^([\w\-]+)\s*:\s*(.*)$/)
            if (!m) { lj++; continue }
            const key = m[1]
            let val: string = m[2].trim()
            // 剝引號 — "x" / 'x'
            if ((val.startsWith('"') && val.endsWith('"')) ||
                (val.startsWith("'") && val.endsWith("'"))) {
              val = val.slice(1, -1)
            }
            // 嘗試 boolean / number 推論
            if (val === 'true') params[key] = true
            else if (val === 'false') params[key] = false
            else if (/^-?\d+$/.test(val)) params[key] = parseInt(val)
            else params[key] = val
            lj++
          }
          if (Object.keys(params).length > 0) cur.outlookParams = params
          li = lj - 1  // 跳過已消化的子行（外層 for 會 ++）
        }
      } else if (/^timeout:/.test(t) && cur) {
        cur.timeout = parseInt(t.replace(/^timeout:\s*/, '')) || 300
        inOutput = false
      } else if (/^retry:/.test(t) && cur) {
        cur.retry = parseInt(t.replace(/^retry:\s*/, '')) || 0
        inOutput = false
      } else if (cur && t && !t.startsWith('-')) {
        // 不匹配任何 key 的行 → 追加到 batch（處理長文字被換行的情況）
        if (cur.batch && !inOutput) {
          cur.batch += ' ' + t
        } else if (cur.expect && inOutput) {
          cur.expect += ' ' + t
        }
      }
    }
    flushMultiline()
    if (cur) steps.push(buildStep(cur, steps.length))
    return { name, validate, steps }
  } catch { return null }
}

function buildStep(partial: Partial<StepData>, index: number): StepData {
  // Outlook 節點的編碼器把 outlookFreeText 寫進 batch:，這裡把它還原回 freeText、
  // 避免畫布上 freeText 欄空白而 batch 帶著一段使用者不會用到的描述
  if (partial.outlookAutomation && partial.batch && !partial.outlookFreeText) {
    partial.outlookFreeText = partial.batch
    partial.batch = ''
  }
  return {
    name: partial.name ?? `步驟 ${index + 1}`,
    batch: partial.batch ?? '',
    workingDir: partial.workingDir ?? '',
    outputPath: partial.outputPath ?? '',
    expect: partial.expect ?? '',
    skillMode: partial.skillMode ?? false,
    readonly: partial.readonly ?? false,
    skill: partial.skill ?? '',
    humanConfirm: partial.humanConfirm ?? false,
    humanConfirmMessage: partial.humanConfirmMessage ?? '',
    humanConfirmNotifyTelegram: partial.humanConfirmNotifyTelegram ?? true,
    humanConfirmScreenshot: partial.humanConfirmScreenshot ?? false,
    humanConfirmPreview: partial.humanConfirmPreview ?? false,
    humanConfirmSendPrevOutput: partial.humanConfirmSendPrevOutput ?? false,
    visualValidation: partial.visualValidation ?? false,
    vvSource: partial.vvSource ?? 'prev_output',
    vvPrompt: partial.vvPrompt ?? '',
    vvSearchRegion: partial.vvSearchRegion ?? [],
    webCrawler: partial.webCrawler ?? false,
    wcMode: partial.wcMode ?? 'web',
    wcUrl: partial.wcUrl ?? '',
    wcUrls: partial.wcUrls ?? [],
    wcJsRender: partial.wcJsRender ?? true,
    wcWaitForSelector: partial.wcWaitForSelector ?? '',
    wcCloudflareFallback: partial.wcCloudflareFallback ?? true,
    wcCookies: partial.wcCookies ?? '',
    wcInteractions: partial.wcInteractions ?? [],
    wcDownloadAssets: partial.wcDownloadAssets ?? false,
    wcScrollCount: partial.wcScrollCount ?? 0,
    wcTargetPostCount: partial.wcTargetPostCount ?? 0,
    wcVideoUrl: partial.wcVideoUrl ?? '',
    wcVideoQuality: partial.wcVideoQuality ?? '720p',
    wcVideoMaxFilesizeMb: partial.wcVideoMaxFilesizeMb ?? 500,
    wcVideoMaxDurationMin: partial.wcVideoMaxDurationMin ?? 30,
    wcVideoSubs: partial.wcVideoSubs ?? true,
    wcVideoSubsLangs: partial.wcVideoSubsLangs ?? '',
    wcVideoSaveInfoJson: partial.wcVideoSaveInfoJson ?? false,
    outlookAutomation: partial.outlookAutomation ?? false,
    outlookTemplate: partial.outlookTemplate ?? '',
    outlookFreeText: partial.outlookFreeText ?? '',
    outlookParams: partial.outlookParams ?? {},
    timeout: partial.timeout ?? (partial.humanConfirm ? 3600 : (partial.visualValidation ? 120 : (partial.webCrawler ? 600 : (partial.outlookAutomation ? 600 : 300)))),
    retry: partial.retry ?? 0,
    index,
    status: 'idle',
    errorMsg: '',
  }
}
