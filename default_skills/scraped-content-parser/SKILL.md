---
name: scraped-content-parser
description: 處理爬蟲節點抓回來的原始內容(HTML / markdown / 純文字)、把留言板留言、評論、商品列表、搜尋結果等「重複性資料」快速又正確地抽成結構化 JSON。核心做法:讓 LLM 只看樣本、辨識重複結構、寫一支確定性解析器,再用程式碼跑完整檔 — 不靠 LLM 逐筆讀取(慢、貴、易漏、會編)。V5 Recipe 會把解析器快取、下次同站秒過。當使用者在爬蟲節點後面掛 Skill 節點想解析抓回來的留言 / 評論 / 列表、或說「把爬回來的內容整理成表格 / JSON」「解析這個論壇頁面」「抽出所有留言」「這頁的評論幫我結構化」這類訴求時都要觸發、不限於明說 skill 名稱。

---

# Scraped Content Parser — 爬蟲內容結構化

## 目的

爬蟲節點(crawl4ai 等)抓回來的是**原始 HTML / markdown** — 又長又雜。下游想用的是**結構化資料**(每則留言的作者 / 時間 / 內文)。這個 skill 負責中間這段轉換。

掛載場景:`爬蟲節點 → [Skill 節點掛這個 skill] → 下游節點`

## 核心理念(務必理解、否則會做錯)

**LLM 找規律、程式碼抽資料。**

留言板 / 評論區 / 列表頁的結構幾乎都是**規律重複**的 — 每一筆資料(留言)套同一個 HTML 模板。所以:

- ❌ **錯**:把整包 HTML 丟給 LLM、要它「讀出每一則留言」。這是逐筆抽取、**非確定性** — 慢、燒 token、會漏、會編造。
- ✅ **對**:LLM 只看**一小段樣本**、認出「一則留言」的重複結構、**寫一支解析器**(CSS selector / regex);再用**確定性程式碼**跑完整檔抽出全部。

LLM 只做一次「結構辨識 + 寫 parser」、剩下交給程式碼。速度、正確率、成本一次解決。V5 Recipe 還會把這支 parser 快取、下次同站完全不呼叫 LLM。

## 🚫 絕不捏造資料(最重要的鐵律)

parser 抽出來的每一筆、**必須真的來自輸入檔**。

- ❌ **嚴格禁止**:解析卡住、找不到結構時、自己「生」一批看起來合理的商品 / 留言(編標題、編價格、編遞增的 ID)。這是欺騙、比 0 筆還糟 — 下游會拿假資料做決策。
- 你寫的 `parser.py` 必須是「從輸入檔**讀出來**」的邏輯;**絕不可以**在 code 裡 hardcode 一份資料清單、或用迴圈生假 ID。
- 驗證時若發現抽出的資料「太乾淨」「ID 是規律遞增」「跟原始檔對不上」→ 那就是你(或上一輪)捏造了、立刻重做。
- **真的解不出來**(結構太亂 / 信號太稀疏 / 試了 3 輪都失敗)→ 誠實 `done(success=false)`、summary 說明卡在哪。**放棄是可以接受的、捏造不行。**

判斷自己有沒有捏造的方法:隨機挑 parser 輸出的一筆、用 `grep` 在原始輸入檔搜它的標題或 URL — 搜不到 = 你捏造了。

## V5 工作流慣例(必守)

- **CWD = workflow 資料夾**(`ai_output/<workflow_name>/`),你的 tool 跑在這
- 輸入:爬蟲節點的輸出。優先從 batch 內的 `{{ steps.X.output.path }}` 或明講的路徑拿;不確定就 `ask_user` 問
- 輸出:**相對路徑**寫(`Path("parsed.json").write_text(...)`)→ 落 workflow dir → 下游 `{{ steps.本步.output.path }}` 自動接到
- 結束 `print` 輸出路徑 + 筆數、`done(success=true)`
- **解析器要確定性** — 同輸入永遠同輸出、不要隨機 / 時間戳,否則 Recipe 命中率崩

## 流程總覽

1. 拿到輸入檔路徑(爬蟲輸出)
2. **讀樣本**(不要讀整檔)— 判斷是 HTML 還是 markdown / 純文字
3. **辨識重複結構** — 找出「一筆資料」的邊界與欄位
4. **寫解析器** → `parser.py`
5. **跑解析器吃完整檔** → 輸出 `parsed.json`
6. **驗證** — 筆數合理、欄位沒空 → `done(success=true)`

---

## Step 1:拿到輸入檔

從 batch 推輸入路徑。batch 通常會有 `{{ steps.爬蟲.output.path }}` 已被展開成實際路徑、或直接寫路徑。

如果 batch 沒明說、用 `ask_user`:
```
ask_user({
  "question": "要解析的爬蟲輸出檔在哪?",
  "context": "通常是上一個爬蟲節點的輸出、例如 workflow 資料夾內的 crawled.html / page.md"
})
```

也用 `ask_user` 確認**想抽什麼**(如果 batch 沒寫清楚):
```
ask_user({
  "question": "要從這頁抽出什麼?",
  "options": ["留言 / 評論(作者+時間+內文)", "商品列表(名稱+價格+連結)", "搜尋結果", "其他(我描述)"],
  "context": "我會依此辨識重複結構、寫對應的解析器"
})
```

## Step 2:讀樣本(關鍵 — 不要讀整檔)

用 `read_file` 讀輸入檔。**檔案常常很大(幾百 KB)**、`read_file` 有 24KB / 100 行上限、會自動截斷 — 這正好、樣本足夠辨識結構了。

```
read_file  →  workflow 資料夾內的爬蟲輸出檔
```

需要看更多段、用 `read_file` 帶 `offset`:
```
{"path": "crawled.html", "offset": 100, "limit": 100}
```

判斷格式:
- 內容是 `<html>` / `<div>` 標籤海 → **HTML**(走 HTML 策略)
- 內容是 `## 標題` / `- 項目` / 純段落 → **markdown / 純文字**(走文字策略)

## Step 3:辨識重複結構

### 🎯 動手前先做這個檢查 —— 輸入有沒有 `# 子頁 N/M:` 標記(最容易撞、先看)

爬蟲節點開了 `wc_with_children` 時,輸出檔長這樣:
- 開頭一段「**列表頁**」:一堆 `[標題](連結)` —— 這些**沒有內文**
- 接著 `# 子頁 1/N:` … `# 子頁 N/N:` —— **每個子頁區塊才是一篇貼文的完整內文 + 留言**

動手前先 `run_shell("grep -c '# 子頁' <輸入檔>")`。**只要 grep 到 `# 子頁` 標記,結構就定了、不要再猜樣本:**

- 記錄選擇器 = 用 regex 切 `^# 子頁 \d+/\d+:` —— **一個子頁區塊 = 一筆資料**
- `title` = 子頁標題那行(`# 子頁 N/M:` 後面的字);
  `content` = 該標記到下一個 `# 子頁`(或檔尾)之間的內文
- **第一個 `# 子頁` 之前的列表頁整段,直接忽略** —— 不要從那裡抽任何東西
- ❌ 抽列表頁的 `[標題](連結)` 會得到幾百筆 `content` 全空的垃圾 —— 這是最常犯的錯

沒有 `# 子頁` 標記 → 才往下走 HTML / markdown 策略。

### HTML 策略

在樣本裡找「一則留言」對應的重複 DOM 區塊。線索:
- 重複出現的 class:`<div class="comment">` / `<li class="post">` / `<article>`
- 留言通常包:作者(`.author` / `.username`)、時間(`.date` / `<time>`)、內文(`.content` / `.message` / `.text`)
- 用 `run_python` + BeautifulSoup 實際 parse 樣本、`soup.select(...)` 試 selector、確認抓得到再寫進 parser

### markdown / 純文字策略

找重複的分隔規律:
- 每則留言以 `---` / 空行 / `### 樓層N` / `[作者]` / `# 子頁 N/M:` 開頭
- 用 regex 切塊、再從每塊抽欄位

**辨識完心裡要有**:`記錄選擇器`(怎麼切出一筆)+ `欄位選擇器`(每筆內怎麼抽 author / time / content)。

### ⚠️ 只有列表頁、沒有子頁的情況

如果輸入**完全沒有** `# 子頁` 標記、只有列表頁 → 那就只能抽到標題+連結、
content 本來就沒有,done summary 要說明「來源只有列表頁、無內文」。
(有 `# 子頁` 標記的情況,看上面「動手前先做這個檢查」那段 —— 一律鎖子頁。)

### 🧹 詳情頁內文要濾掉網站 chrome

子頁 / 詳情頁的 markdown 常把**整頁**抓下來 — 含頁首導覽列(`[ 新聞 ]` `[ 熱門文章 ]` `[ 登入/註冊 ]`)、分享按鈕、頁尾、廣告。這些是**每頁都一樣的 chrome**、不是貼文本體。

parser 抽 content 時要清掉:
- 頭尾的導覽 / 選單行(整行都是 `[文字](連結)` 連結列、或 `分享至:` 之類)
- 廣告區塊、`* * *` 純分隔線堆
- 留下的應該是貼文本體(規格、心得、提問內容)

做法:抽出子頁區塊後、用簡單規則修掉明顯 chrome 行(例:一行內連結數 ≥ 3 視為導覽列、跳過;`分享至` / `facebook` / `plurk` 開頭跳過)。不用做到完美、把最明顯的雜訊去掉即可 — 下游 LLM 能容忍少量雜訊、但整段導覽列會稀釋分析品質。

### 📦 巨大 + 信號稀疏的檔案(購物站常見)

有些站(尤其購物網:momo、PChome 等)的列表頁**又大又雜** — 檔案上看 1MB、7000 行、90% 是促銷橫幅 / 0 利率分期文字 / 「相關搜尋」連結 / 廣告。真正的商品只是大海裡的針。

這種檔**不要靠「讀樣本猜結構」** — 樣本很可能整段落在 chrome 區、你會找不到商品、然後迷路。

**正確做法:用 grep 反向定位**
1. 先想清楚「一筆商品 / 記錄」的**唯一 URL 特徵**(商品詳情頁的 URL pattern):
   - momo:`GoodsDetail.jsp?i_code=數字`
   - PChome:`/prod/英數`
   - Amazon:`/dp/十碼ASIN`
   - 一般詳情頁:`/p/數字`、`?id=數字` 之類
2. 用 `grep` 直接搜這個 pattern、看它命中哪些行 — 那些行就是**真實商品列**所在
3. parser 錨定在這些行上(用同樣的 URL pattern 當「記錄選擇器」),從每個命中點往外抓標題 / 價格
4. 完全不要被 chrome 行干擾 — chrome 行不含商品詳情 URL、自然被你的 pattern 過濾掉

這比「切塊 + 猜」對巨大稀疏檔可靠得多。

## Step 4:寫解析器 `parser.py`

用 `write_file` 寫到 workflow 資料夾根。**這支檔要能獨立跑、確定性、吃輸入檔吐 JSON**。

HTML 版範本(直接抄改):
```python
"""留言解析器 — 由 scraped-content-parser skill 自動產生。
吃爬蟲輸出 HTML、用 CSS selector 抽出結構化留言、輸出 JSON。"""
import sys, json
from pathlib import Path
from bs4 import BeautifulSoup

def parse(html_path: str, out_path: str = "parsed.json") -> int:
    html = Path(html_path).read_text(encoding="utf-8", errors="ignore")
    soup = BeautifulSoup(html, "html.parser")

    records = []
    # ↓ 依 Step 3 辨識結果換成實際 selector
    for block in soup.select("div.comment"):
        def _txt(sel):
            el = block.select_one(sel)
            return el.get_text(strip=True) if el else ""
        rec = {
            "author":  _txt(".author"),
            "time":    _txt(".date"),
            "content": _txt(".content"),
        }
        # 跳過整筆全空的(通常是抓錯區塊)
        if any(rec.values()):
            records.append(rec)

    Path(out_path).write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return len(records)

if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "crawled.html"
    out = sys.argv[2] if len(sys.argv) > 2 else "parsed.json"
    n = parse(src, out)
    print(f"✓ 解析完成、{n} 筆 → {out}")
```

markdown / 文字版:把 `BeautifulSoup` 換成 `re` 切塊。

**容器內套件**:`beautifulsoup4` / `lxml` 通常已預裝;沒有就 `run_shell("pip install beautifulsoup4 lxml")`(安裝命令會被攔截等使用者授權、正常)。

## Step 5:跑解析器吃完整檔

```
run_shell  →  python parser.py <輸入檔> parsed.json
```

注意 CWD = workflow 資料夾,所以輸入檔給相對檔名或絕對路徑、輸出 `parsed.json` 落 workflow dir。

## Step 6:驗證 + done

跑完用 `run_python` 檢查 `parsed.json`、**三層驗證、不要只看「非空」就 done**:

**驗證 1 — 筆數**
- 筆數 > 0(0 筆通常 = selector 錯、回 Step 3 重挑)
- 筆數跟樣本目測的「一頁有幾則 / 幾個子頁」大致吻合

**驗證 2 — 欄位非空(content 大量為空 = 抓錯層、硬性 retry)**
- 抽幾筆看欄位:author / content 不該全空
- ⚠️ **若幾十、幾百筆的 `content` 幾乎都是空字串 → 你 100% 抓錯層了** ——
  抽到了開頭列表頁的連結清單、沒抽到 `# 子頁` 區塊。
  **必須**回 Step 3、改用 `^# 子頁 \d+/\d+:` 當記錄選擇器重抽。
  「幾百筆空 content」不是成功、**絕對不可以** `done(success=true)`。

**驗證 3 —(關鍵)content 是「真內文」還是「chrome 雜訊」**
- chrome 過濾規則逐站不同、寫死的規則救不了所有站。**必須實際抽查、不能假設濾乾淨了**。
- `run_python` 印出 **2-3 筆 content 的開頭 200 字**、**自己讀**、判斷:
  - 開頭就是貼文本體(規格、提問、心得、留言正文)→ ✅ 通過
  - 開頭是 `分享至`、`[發表新連結]`、`shortlink:`、分數列、`[ 新聞 ][ 熱門 ]` 這種**網站介面字串** → ❌ 還是 chrome、回 Step 4 加強過濾規則重抽
- ⚠️ **絕對不要**在 content 還是 chrome 的情況下 done(success=true) 還寫「已濾除雜訊」— 那是謊報。沒濾乾淨就老實回去修。

**selector 抓錯 / content 是 chrome → 不要 done**、回 Step 3/4 重試。**頂多試 3 輪**、仍不行才 `done(success=false)` 說明卡在哪(例:「content 含 chrome、已試 3 種過濾規則仍無法乾淨切出貼文本體」)。

成功:
```
done({
  "success": true,
  "summary": "✓ 解析 crawled.html、抽出 N 則留言 → parsed.json\n   欄位:author / time / content\n   解析器 parser.py 已存、下次同站 Recipe 會 replay、不再呼叫 LLM"
})
```

## 設計原則

1. **LLM 找規律、程式碼抽資料** — 絕不讓 LLM 逐筆「讀出」留言。LLM 的工作到「寫好 parser.py」為止。
2. **樣本辨識、不吞整檔** — read_file 截斷是好事;要更多段用 offset、不要硬塞整包 HTML 進 context。
3. **解析器確定性** — 同輸入同輸出、不隨機、不時間戳,Recipe 才命中。
4. **驗證再 done** — 0 筆 / 欄位全空 = selector 錯,修到對。寧可回 Step 3、不要交爛資料。
5. **互動但節制** — `ask_user` 只在「真不知道輸入在哪 / 要抽什麼」時用。

## 常見坑

- **selector 太貪心**:`soup.select("div")` 抓到整頁 → 一定要鎖到「一則留言」那層的 class
- **巢狀留言(回覆)**:留言內又包子留言、`select` 會重複抓 → 用 `recursive=False` 或只取頂層
- **動態載入沒抓到**:如果樣本裡留言數明顯比實際少 → 是爬蟲節點沒滾動 / 沒等 JS,那是**爬蟲節點**的問題、不是這個 skill 能解的,done summary 註明請調整爬蟲節點
- **編碼亂碼**:一律 `encoding="utf-8", errors="ignore"`
- **整檔太大跑很慢**:BeautifulSoup 對超大 HTML 慢 → 可改 `selectolax`(快很多、API 類似),`pip install selectolax`
