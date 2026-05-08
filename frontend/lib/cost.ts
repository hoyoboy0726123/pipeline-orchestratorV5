// LLM token 成本對照（USD per 1M tokens）— 2025-Q4 公開定價、各 provider 可能調整
// 用 substring match 配對 model 名稱，因 provider 常加 prefix 像
// "groq/llama-3.3-70b" / "openrouter/anthropic/claude-sonnet-4"

type Pricing = [match: string, inputPer1M: number, outputPer1M: number]

// 順序重要：較具體的放前面（避免 "llama-3.1-8b" 被 "llama-3.1" 攔截）
const PRICING_TABLE: Pricing[] = [
  // ── Anthropic Claude ──
  ['claude-opus-4',     15.00, 75.00],
  ['claude-sonnet-4',    3.00, 15.00],
  ['claude-haiku-4',     1.00,  5.00],
  ['claude-3-5-sonnet',  3.00, 15.00],
  ['claude-3-5-haiku',   0.80,  4.00],
  ['claude-3-opus',     15.00, 75.00],

  // ── OpenAI ──
  ['gpt-4o-mini',        0.15,  0.60],
  ['gpt-4o',             2.50, 10.00],
  ['gpt-4-turbo',       10.00, 30.00],
  ['gpt-3.5',            0.50,  1.50],
  ['o1-mini',            1.10,  4.40],
  ['o1-preview',        15.00, 60.00],

  // ── Google Gemini ──
  ['gemini-2.5-pro',     1.25,  5.00],
  ['gemini-2.5-flash',   0.075, 0.30],
  ['gemini-2.0-flash',   0.10,  0.40],
  ['gemini-1.5-pro',     1.25,  5.00],
  ['gemini-1.5-flash',   0.075, 0.30],
  ['gemma',              0,     0],     // Gemma 多半免費

  // ── Groq (LPU 定價，普遍低於 reference 模型) ──
  ['llama-4-scout',      0.11,  0.34],
  ['llama-4-maverick',   0.20,  0.60],
  ['llama-3.3-70b',      0.59,  0.79],
  ['llama-3.1-405b',     0.79,  0.79],
  ['llama-3.1-70b',      0.59,  0.79],
  ['llama-3.1-8b',       0.05,  0.08],
  ['deepseek-r1',        0.75,  0.99],
  ['deepseek',           0.27,  1.10],   // deepseek-v3 / chat 通用
  ['qwen-3',             0.29,  0.59],
  ['qwen',               0.20,  0.50],
  ['mixtral',            0.27,  0.27],

  // ── Local Ollama 模型(host 端跑、無 API 成本) ──
  ['ollama',             0,     0],
]

/** 算給定 model + token 用量的 USD 成本。配對不到 pricing 表回 null。 */
export function computeCostUsd(model: string, inputTokens: number, outputTokens: number): number | null {
  if (!model) return null
  const lower = model.toLowerCase()
  for (const [key, inP, outP] of PRICING_TABLE) {
    if (lower.includes(key)) {
      return (inputTokens * inP + outputTokens * outP) / 1_000_000
    }
  }
  return null
}

/** 格式化 USD 金額：小金額用更精細小數、大金額用 2 位。null → 空字串。 */
export function formatCostUsd(cost: number | null | undefined): string {
  if (cost === null || cost === undefined) return ''
  if (cost === 0) return '$0'
  if (cost < 0.0001) return `$${cost.toExponential(1)}`
  if (cost < 0.01)   return `$${cost.toFixed(4)}`
  if (cost < 1)      return `$${cost.toFixed(3)}`
  return `$${cost.toFixed(2)}`
}
