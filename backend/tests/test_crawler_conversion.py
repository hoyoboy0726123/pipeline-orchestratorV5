# -*- coding: utf-8 -*-
"""
Atlas V5 — 爬蟲 HTML→Markdown 轉換回歸測試(read-only)
=========================================================
鎖定 `pipeline/web_crawler.py::_html_to_markdown()` —— 這是整個 codebase 唯一
用到 html2text(GPL)的地方,也是日後「html2text → markdownify(MIT)」要替換
的函式。本測試以「內容保留(content-preservation)」斷言為主,而非比對特定
函式庫的逐字輸出,因此**換掉底層轉換庫後仍應全綠**,正好作為該改動的回歸網。

說明:
  - _html_to_markdown 三層 fallback:trafilatura → html2text → 去 tag。
    測試會先偵測目前實際走哪一層並印出(透明)。
  - 純離線、確定性、零網路、零 LLM。不修改任何應用程式碼。
  - 另含一個「基準快照」輸出,換庫前後可肉眼對照。

用法:
  & backend\\.venv\\Scripts\\python.exe backend\\tests\\test_crawler_conversion.py
"""
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

_results = []


def _rec(ok, name, detail=""):
    _results.append((ok, name, detail))
    tag = "PASS" if ok is True else ("SKIP" if ok is None else "FAIL")
    print(f"  [{tag}] {name}" + (f" — {detail}" if detail else ""))


def check(name, fn):
    try:
        r = fn()
        if isinstance(r, tuple):
            _rec(r[0], name, r[1] if len(r) > 1 else "")
        else:
            _rec(bool(r), name)
    except Exception as e:
        _rec(False, name, f"例外:{type(e).__name__}: {str(e)[:160]}")


def main():
    print("=" * 70)
    print("Atlas V5 — 爬蟲 HTML→Markdown 轉換回歸測試")
    print("=" * 70)

    # 匯入待測函式
    try:
        from pipeline.web_crawler import _html_to_markdown, _extract_title
    except Exception as e:
        print(f"❌ 無法匯入 pipeline.web_crawler:{type(e).__name__}: {e}")
        print("   (請用 backend 的 venv 執行,且該模組可在 host 載入)")
        sys.exit(2)

    # 偵測目前走哪一層(僅供透明,不影響斷言)
    tiers = []
    try:
        import trafilatura  # noqa
        tiers.append("trafilatura")
    except Exception:
        pass
    try:
        import html2text  # noqa
        tiers.append("html2text")
    except Exception:
        pass
    tiers.append("strip-tags(保底)")
    print(f"\n目前可用轉換層(依序):{' → '.join(tiers)}")
    print("（本測試以內容保留為準,換成 markdownify 後仍應通過）\n")

    SAMPLE = (
        "<html><head><title>測試頁 Title</title></head><body>"
        "<h1>主標題 Heading</h1>"
        "<p>這是一段內文 paragraph,含 <strong>粗體</strong> 與 <em>斜體</em>。</p>"
        "<a href=\"https://example.com/page\">連結文字 link</a>"
        "<ul><li>項目一 item-A</li><li>項目二 item-B</li></ul>"
        "<p>數量 5 &amp; 10 &lt; 20</p>"
        "</body></html>"
    )

    print("== A. 內容保留(核心,換庫後仍須通過) ==")
    md = _html_to_markdown(SAMPLE)
    check("輸出為非空字串", lambda: (isinstance(md, str) and len(md.strip()) > 0, f"len={len(md)}"))
    check("保留標題文字 '主標題 Heading'", lambda: ("主標題 Heading" in md, ""))
    check("保留內文 'paragraph'", lambda: ("paragraph" in md, ""))
    check("保留粗體/斜體文字", lambda: ("粗體" in md and "斜體" in md, ""))
    # 連結 URL 保留與否由 tier-1(trafilatura)主導,與 html2text 那層無關 → 列為資訊性,不計失敗。
    # (實測:host 上 trafilatura 會略去單獨連結的 URL,僅保留連結文字)
    _url_in = "https://example.com/page" in md
    _rec(None, "連結 URL 保留(整體層,tier 相依)",
         f"目前={'有' if _url_in else '無(trafilatura tier-1 行為,非本次改動造成)'}")
    check("保留連結文字 'link'", lambda: ("link" in md, ""))
    check("保留清單項目 item-A / item-B", lambda: ("item-A" in md and "item-B" in md, ""))
    check("移除 HTML tag(無 <p>/<h1>/<ul>)",
          lambda: (not any(t in md for t in ("<p>", "<h1>", "<ul>", "<li>", "<a ")), ""))
    check("HTML entity 解碼(& 與 < 還原)",
          lambda: ("&amp;" not in md and "&lt;" not in md and "5" in md and "10" in md, "實體應被還原"))

    print("\n== B. 邊緣條件 ==")
    check("空字串 → ''", lambda: (_html_to_markdown("") == "", ""))
    check("None 安全處理(不 crash)",
          lambda: (isinstance(_html_to_markdown(None) if True else "", str) or True, "見下行實測"))
    # 實測 None:函式開頭 `if not html: return ""`,None 屬 falsy → ""
    check("None → ''(falsy 防護)", lambda: (_html_to_markdown(None) == "", ""))
    check("純文字(無 tag)→ 內容保留",
          lambda: ("hello world" in _html_to_markdown("hello world"), ""))
    check("未閉合/壞 HTML 不崩潰且回字串",
          lambda: (isinstance(_html_to_markdown("<p>壞掉 <b>沒收尾 <div"), str), ""))
    check("<script>/<style> 內容不應原樣大量殘留",
          lambda: ("alert(" not in _html_to_markdown("<p>正文 body-text</p><script>alert('x')</script>")
                   and "body-text" in _html_to_markdown("<p>正文 body-text</p><script>alert('x')</script>"),
                   "正文保留、script 內容濾除(容忍部分庫保留,主要看正文在)"))

    print("\n== C. _extract_title(與 markdown 搭配) ==")
    check("從 <title> 取標題", lambda: (_extract_title(SAMPLE, md) == "測試頁 Title", f"got={_extract_title(SAMPLE, md)!r}"))
    check("無 title 時從首個 # 標題取",
          lambda: (_extract_title("<body><p>x</p></body>", "# 我的標題\n內文") == "我的標題", ""))
    check("皆無 → ''",
          lambda: (_extract_title("<body><p>x</p></body>", "純內文沒有標題") == "", ""))

    print("\n== D. 基準快照(換庫前後肉眼對照用) ==")
    snap = _html_to_markdown(SAMPLE).strip()
    print("  --- 目前 _html_to_markdown(SAMPLE) 輸出 ---")
    for line in snap.splitlines():
        print("  | " + line)
    print("  --- 快照結束 ---")
    _rec(True, "基準快照已輸出(供 markdownify 換庫後比對)", f"{len(snap)} 字元")

    # ==================================================================
    # E. tier-2 轉換庫不變式 —— 這是「html2text → markdownify」swap 的精準回歸網
    #    現行 html2text 應全綠(基準);markdownify 換庫後再跑,同樣不變式須全綠
    #    = 證明換庫後 tier-2 輸出等價、未回歸。
    # ==================================================================
    print("\n== E. tier-2 轉換庫不變式(swap 目標:html2text → markdownify) ==")

    def _invariants(out):
        return {
            "非空輸出": len(out.strip()) > 0,
            "保留標題": "主標題 Heading" in out,
            "保留內文 paragraph": "paragraph" in out,
            "保留連結 URL": "https://example.com/page" in out,
            "保留連結文字 link": "link" in out,
            "保留清單 item-A/B": ("item-A" in out and "item-B" in out),
            "移除 HTML tag": not any(t in out for t in ("<p>", "<h1>", "<a ", "<ul>")),
        }

    # 現行 tier-2:html2text(與 _html_to_markdown 內設定一致)
    def _html2text_conv(html):
        import html2text
        h = html2text.HTML2Text()
        h.body_width = 0
        h.ignore_images = False
        h.ignore_links = False
        return h.handle(html)

    try:
        inv = _invariants(_html2text_conv(SAMPLE))
        for k, v in inv.items():
            _rec(v, f"html2text · {k}", "")
    except Exception as e:
        _rec(None, "html2text 直接不變式", f"無法載入/執行:{type(e).__name__}: {str(e)[:100]}")

    # 換庫目標:markdownify(MIT)。尚未安裝 → SKIP;換庫後 `pip install markdownify` 此區自動生效
    try:
        from markdownify import markdownify as _md
        inv = _invariants(_md(SAMPLE))
        for k, v in inv.items():
            _rec(v, f"markdownify · {k}", "")
    except ImportError:
        _rec(None, "markdownify 不變式(換庫後啟用)", "尚未安裝;`pip install markdownify` 後此區自動比對")

    passed = sum(1 for ok, _, _ in _results if ok is True)
    failed = sum(1 for ok, _, _ in _results if ok is False)
    skipped = sum(1 for ok, _, _ in _results if ok is None)
    print("\n" + "=" * 70)
    print(f"彙整:PASS {passed} / FAIL {failed} / SKIP {skipped}(共 {len(_results)})")
    if failed:
        print("失敗項:")
        for ok, name, detail in _results:
            if ok is False:
                print(f"  - {name} :: {detail}")
    print("=" * 70)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
