"""手動診斷 Outlook COM 是否能用 + 列預設資料夾 + 撈最近幾封信。

執行：
    cd backend
    .venv\\Scripts\\python.exe test_outlook_api.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def test_basic_com():
    """測試最基本的 COM dispatch — 新版 Outlook 在這裡就會炸。"""
    print("=" * 60)
    print("Step 1: 測試 win32com.client.Dispatch('Outlook.Application')")
    print("=" * 60)
    try:
        import win32com.client
        import pythoncom
        pythoncom.CoInitialize()
        app = win32com.client.Dispatch("Outlook.Application")
        print(f"[OK] COM Dispatch 成功")
        print(f"     Outlook.Name        = {app.Name}")
        print(f"     Outlook.Version     = {app.Version}")
        print(f"     Outlook.Class       = {app.Class}")
        return app
    except Exception as e:
        print(f"[FAIL] COM Dispatch 失敗：{e.__class__.__name__}: {e}")
        print()
        print("可能原因：")
        print("  1. 你用的是「新版 Outlook」(New Outlook for Windows) — 不支援 COM")
        print("     → 切換到「傳統 Outlook」: 設定 → 切換到傳統 Outlook 開關")
        print("  2. 桌面版 Outlook 沒裝（純用 Web Outlook / Outlook 365 webmail）")
        print("  3. Outlook 沒設預設 profile")
        return None


def test_namespace(app):
    """測試 MAPI namespace + 列預設資料夾。"""
    print()
    print("=" * 60)
    print("Step 2: 取得 MAPI namespace + 列預設資料夾")
    print("=" * 60)
    try:
        ns = app.GetNamespace("MAPI")
        print(f"[OK] namespace 取得成功，type={type(ns).__name__}")
        # OlDefaultFolders.olFolderInbox = 6
        for fid, name in [(6, "Inbox"), (5, "Sent"), (16, "Drafts"),
                          (3, "Deleted"), (9, "Calendar")]:
            try:
                f = ns.GetDefaultFolder(fid)
                print(f"     [{fid:2d}] {name:10s} → {f.Name} ({f.Items.Count} 項)")
            except Exception as e:
                print(f"     [{fid:2d}] {name:10s} → FAILED: {e}")
        return ns
    except Exception as e:
        print(f"[FAIL] namespace 取得失敗：{e.__class__.__name__}: {e}")
        return None


def test_inbox_recent(ns):
    """讀收件匣最近 5 封 — 看看能不能讀到實際信件。"""
    print()
    print("=" * 60)
    print("Step 3: 讀收件匣最近 5 封信")
    print("=" * 60)
    try:
        inbox = ns.GetDefaultFolder(6)
        items = inbox.Items
        items.Sort("[ReceivedTime]", True)  # descending
        count = 0
        for item in items:
            try:
                if item.Class != 43:  # 不是 MailItem
                    continue
                count += 1
                received = item.ReceivedTime
                sender = item.SenderName or "(unknown)"
                subj = item.Subject or "(no subject)"
                print(f"  {count:2d}. {received}  {sender[:25]:25s}  {subj[:50]}")
                if count >= 5:
                    break
            except Exception as e:
                print(f"     跳過一筆無法讀取的：{e}")
        if count == 0:
            print("[WARN] 收件匣空的？或所有 item 都不是 MailItem")
        else:
            print(f"[OK] 讀到 {count} 封")
    except Exception as e:
        print(f"[FAIL] 讀收件匣失敗：{e.__class__.__name__}: {e}")


def test_search_subject(ns, keyword: str):
    """用 search_mail wrapper 搜尋特定主旨關鍵字。"""
    print()
    print("=" * 60)
    print(f"Step 4: 用 win32_helpers.outlook.search_mail 搜「{keyword}」")
    print("=" * 60)
    try:
        from pipeline.win32_helpers.outlook import search_mail
        df = search_mail(subject=keyword, folder="inbox", exact_match=False, limit=10)
        if df.empty:
            print(f"[OK] 搜尋無錯，但 0 筆命中（主旨含 '{keyword}' 的信不存在）")
        else:
            print(f"[OK] 命中 {len(df)} 筆：")
            for _, row in df.iterrows():
                print(f"  - {row['received']}  {row['sender_name'][:20]:20s}  {row['subject'][:60]}")
    except Exception as e:
        print(f"[FAIL] search_mail 失敗：{e.__class__.__name__}: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    print()
    print("Outlook COM 診斷工具")
    print()
    app = test_basic_com()
    if app is None:
        sys.exit(1)
    ns = test_namespace(app)
    if ns is None:
        sys.exit(1)
    test_inbox_recent(ns)
    # 你截圖的主旨大多含 "ASUS NOTICE" / "ECN" / "PLM"，挑一個常見字試
    test_search_subject(ns, "ECN")
    print()
    print("✓ 診斷完成。如果 Step 1 通過但 Step 4 找不到信，代表 search_mail")
    print("  邏輯本身有問題；如果 Step 1 就 fail，就是 Outlook 版本問題。")
