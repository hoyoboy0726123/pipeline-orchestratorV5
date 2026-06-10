"""零網路稽核:用各站『真實格式』URL 樣本測 _AUTO_CHILD_LINK_PATTERNS 是否真命中。
正面 = 應被認成子頁(必須抽到);負面 = 列表/首頁/雜訊(不該被抽)。"""
from pipeline.web_crawler import _extract_child_links_from_markdown as EX

# (站名, parent_host, [應命中的真實子頁URL], [不該命中的URL])
CASES = [
    ("Reddit", "www.reddit.com",
     ["https://www.reddit.com/r/ASUS/comments/1abc23/my_laptop_issue/"],
     ["https://www.reddit.com/r/ASUS/"]),
    ("Dcard", "www.dcard.tw",
     ["https://www.dcard.tw/f/3c/p/123456789"],
     ["https://www.dcard.tw/f/3c"]),
    ("PTT pttweb", "www.pttweb.cc",
     ["https://www.pttweb.cc/bbs/Stock/M.1700000000.A.ABC"],
     ["https://www.pttweb.cc/bbs/Stock"]),
    ("PTT ptt.cc", "www.ptt.cc",
     ["https://www.ptt.cc/bbs/Stock/M.1700000000.A.D3F.html"],
     ["https://www.ptt.cc/bbs/Stock/index.html"]),
    ("Mobile01", "www.mobile01.com",
     ["https://www.mobile01.com/topicdetail.php?f=295&t=6789012"],
     ["https://www.mobile01.com/category.php?id=1"]),
    ("HackerNews", "news.ycombinator.com",
     ["https://news.ycombinator.com/item?id=39876543"],
     ["https://news.ycombinator.com/news"]),
    ("Twitter/X", "x.com",
     ["https://x.com/ASUS/status/1790000000000000000"],
     ["https://x.com/ASUS"]),
    ("TikTok", "www.tiktok.com",
     ["https://www.tiktok.com/@asus/video/7300000000000000000"],
     ["https://www.tiktok.com/@asus"]),
    ("ProductHunt", "www.producthunt.com",
     ["https://www.producthunt.com/posts/some-cool-app"],
     ["https://www.producthunt.com/topics/tech"]),
    ("momo", "www.momoshop.com.tw",
     ["https://www.momoshop.com.tw/goods/GoodsDetail.jsp?i_code=13709422"],
     ["https://www.momoshop.com.tw/category/MgrpCategory.jsp?m_code=4300100008"]),
    ("PChome24h", "24h.pchome.com.tw",
     ["https://24h.pchome.com.tw/prod/DSBE1W-A900GI9EL"],
     ["https://24h.pchome.com.tw/store/DSBE1W"]),
    ("Shopee", "shopee.tw",
     ["https://shopee.tw/羅技滑鼠-i.123456.7890123"],
     ["https://shopee.tw/mall/brand/asus"]),
    ("Ruten 露天", "www.ruten.com.tw",
     ["https://www.ruten.com.tw/item/show?21849012345678",
      "https://www.ruten.com.tw/item/21849012345678"],
     ["https://www.ruten.com.tw/find/?q=asus"]),
    ("Amazon /dp/", "www.amazon.com",
     ["https://www.amazon.com/dp/B08XYZ1234"],
     ["https://www.amazon.com/s?k=asus+laptop"]),
    ("Amazon /gp/", "www.amazon.com",
     ["https://www.amazon.com/gp/product/B08XYZ1234"], []),
    ("eBay", "www.ebay.com",
     ["https://www.ebay.com/itm/123456789012"],
     ["https://www.ebay.com/sch/i.html?_nkw=asus"]),
    ("BestBuy", "www.bestbuy.com",
     ["https://www.bestbuy.com/site/gateway-laptop-with-intel/8651167.p?skuId=8651167"],
     ["https://www.bestbuy.com/site/searchpage.jsp?st=laptop"]),
    ("Walmart", "www.walmart.com",
     ["https://www.walmart.com/ip/ASUS-Laptop/123456789"],
     ["https://www.walmart.com/browse/electronics"]),
    ("Taobao", "item.taobao.com",
     ["https://item.taobao.com/item.htm?id=678901234567"],
     ["https://www.taobao.com/"]),
    ("JD 京東", "item.jd.com",
     ["https://item.jd.com/100012043978.html"],
     ["https://list.jd.com/list.html?cat=670"]),
    ("新聞 /article/", "example.com",
     ["https://example.com/article/some-news-slug"],
     ["https://example.com/category/tech"]),
]


def run():
    bad = []
    for name, host, pos, neg in CASES:
        md_pos = "\n".join(f"- [item{i}]({u})" for i, u in enumerate(pos))
        got = EX(md_pos, pattern="", max_count=20, parent_host=host)
        miss = [u for u in pos if u not in got]
        # 負面:把 neg 也丟進去,確認它們沒被當子頁
        md_neg = "\n".join(f"- [x{i}]({u})" for i, u in enumerate(neg))
        got_neg = EX(md_neg, pattern="", max_count=20, parent_host=host)
        false_pos = [u for u in neg if u in got_neg]
        status = "OK"
        if miss:
            status = "❌ 漏抓真實子頁"
        elif false_pos:
            status = "⚠ 誤抓列表/雜訊"
        print(f"[{status}] {name}")
        if miss:
            print("     漏:", miss)
            bad.append((name, "miss", miss))
        if false_pos:
            print("     誤:", false_pos)
            bad.append((name, "false_pos", false_pos))
    print("\n==== 總結 ====")
    if not bad:
        print("全部 pattern 正反面都正確。")
        return True
    print(f"有 {len(bad)} 個問題:")
    for n, k, v in bad:
        print(f"  - {n}: {k} {v}")
    return False


if __name__ == "__main__":
    import sys
    sys.exit(0 if run() else 1)
