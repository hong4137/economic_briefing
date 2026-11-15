"""경제 브리핑 원천 수집기 — AI 를 쓰지 않는다.

    python collect.py [--hours 36] [--out data/raw.json]

sources.json 의 원천을 받아 한 벌의 JSON 으로 남긴다. 층(결정·숫자·가격·매체)과 원문 제목·주소·시각만 담는다.
세 곳이 이 파일을 읽는다: 모의투자 아침 한 장(사실만), 경제 브리핑(사람이 읽는 뉴스), Daily UptoDate.
열쇠(FRED)는 환경변수 → `../_열쇠/keys.env` 순으로 찾고, 없으면 그 줄만 "받지 못함"이 된다.
"""
import argparse, concurrent.futures as cf, html, http.cookiejar, json, os, re, ssl, sys, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
KST = timezone(timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Accept": "application/rss+xml, application/xml, text/xml, application/json, */*", "Accept-Language": "en-US,en;q=0.9,ko;q=0.8"}
_NOVERIFY = ssl.create_default_context(); _NOVERIFY.check_hostname = False; _NOVERIFY.verify_mode = ssl.CERT_NONE


def fetch(url, noverify=False, cap=4_000_000):
    handlers = [urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())]
    if noverify:
        handlers.append(urllib.request.HTTPSHandler(context=_NOVERIFY))
    with urllib.request.build_opener(*handlers).open(urllib.request.Request(url, headers=UA), timeout=25) as r:
        return r.read(cap).decode("utf-8", "replace")


def key(name):
    v = os.environ.get(name)
    if v:
        return v
    f = HERE.parent / "_열쇠" / "keys.env"
    if f.exists():
        for line in f.read_text("utf-8").splitlines():
            if line.strip().startswith(name + "="):
                return line.split("=", 1)[1].strip().strip('"\'') or None
    return None


def clean(s):
    s = re.sub(r"<!\[CDATA\[|\]\]>", "", s or "")
    s = html.unescape(html.unescape(s))               # 피드가 태그를 &lt;p&gt; 로 싸서 주는 경우가 있어 먼저 푼다
    s = re.sub(r"<(script|style|iframe)[^>]*>.*?</\1>", " ", s, flags=re.S | re.I)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s)).strip()


def when(s):
    """여러 모양의 날짜 글자를 한국 시각 ISO 로. 못 읽으면 None."""
    s = (s or "").strip()
    if not s:
        return None
    try:
        d = parsedate_to_datetime(re.sub(r",(\d)", r", \1", s))
    except Exception:
        d = None
    if d is None:
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?(?:\.\d+)?\s*(Z|[+-]\d{2}:?\d{2})?", s)
        m2 = re.match(r"(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})$", s)
        m3 = re.match(r"(\d{4})-(\d{2})-(\d{2})$", s)
        try:
            if m:
                tz = m.group(7)
                d = datetime(*[int(x) for x in m.groups()[:5]], int(m.group(6) or 0))
                if tz == "Z":
                    d = d.replace(tzinfo=timezone.utc)
                elif tz:
                    sign = 1 if tz[0] == "+" else -1
                    d = d.replace(tzinfo=timezone(sign * timedelta(hours=int(tz[1:3]), minutes=int(tz[-2:]))))
                else:
                    d = d.replace(tzinfo=KST)          # 시간대 없는 것은 한국 관청 피드다
            elif m2:
                d = datetime(*[int(x) for x in m2.groups()], tzinfo=KST)
            elif m3:
                d = datetime(*[int(x) for x in m3.groups()], 12, tzinfo=timezone.utc)
        except Exception:
            d = None
    if d is None:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(KST).isoformat(timespec="minutes")


def parse_feed(t):
    out = []
    for m in re.finditer(r"<(item|entry)[\s>].*?</\1>", t, re.S):
        b = m.group(0)
        g = lambda tag: (re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", b, re.S) or [None, ""])[1]
        link = clean(g("link")) or (re.search(r'<link[^>]*href=["\']([^"\']+)', b) or [None, ""])[1]
        if not link.startswith("http"):
            link = clean(g("guid")) if clean(g("guid")).startswith("http") else link
        dt = next((when(clean(g(tag))) for tag in ("pubDate", "updated", "published", "dc:date") if clean(g(tag))), None)
        out.append({"title": clean(g("title")), "url": html.unescape(link), "at": dt,
                    "summary": clean(g("description") or g("summary") or g("content:encoded"))[:400]})
    return out


def parse_fedreg(t):
    return [{"title": clean(r.get("title")), "url": r.get("html_url"), "at": when(r.get("publication_date")),
             "summary": clean(r.get("abstract") or "")[:400], "kind": r.get("type")} for r in json.loads(t).get("results", [])]


def parse_auctions(t):
    out = []
    for r in json.loads(t):
        y = r.get("highYield") or r.get("highDiscountRate") or r.get("highInvestmentRate")
        if not y:
            continue
        out.append({"title": f"{r.get('securityTerm')} {r.get('securityType')} 입찰 — 낙찰 금리 {float(y):.3f}%, 응찰률 {float(r.get('bidToCoverRatio') or 0):.2f}배",
                    "url": "https://www.treasurydirect.gov/auctions/announcements-data-results/", "at": when((r.get("auctionDate") or "")[:10]),
                    "summary": f"발행 {r.get('offeringAmount')} 달러, CUSIP {r.get('cusip')}", "kind": "auction"})
    return out


def collect_source(src, since):
    try:
        t = fetch(src["url"], src.get("noverify", False))
        items = {"fedreg": parse_fedreg, "auctions": parse_auctions}.get(src.get("type"), parse_feed)(t)
    except Exception as e:
        return {**{k: src[k] for k in ("id", "name", "layer", "group")}, "ok": False, "error": f"{type(e).__name__}: {str(e)[:80]}", "items": []}
    drop = re.compile(src["drop"], re.I) if src.get("drop") else None
    keep = [i for i in items if i["title"] and i["url"] and (i["at"] is None or i["at"] >= since) and not (drop and drop.search(i["title"]))]
    undated = sum(i["at"] is None for i in keep)
    return {**{k: src[k] for k in ("id", "name", "layer", "group")}, "ok": True, "total": len(items), "undated": undated,
            "items": keep[: src.get("max", 40)]}


# ---------- 가격 ----------
def yahoo(sym):
    j = json.loads(fetch(f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}?range=1mo&interval=1d"))
    r = j["chart"]["result"][0]
    rows = [(t, c) for t, c in zip(r["timestamp"], r["indicators"]["quote"][0]["close"]) if c is not None]
    last, prev = rows[-1], rows[-2]
    wk = rows[-6][1] if len(rows) >= 6 else rows[0][1]
    return {"value": last[1], "prev": prev[1], "week_ago": wk, "month_ago": rows[0][1],
            "date": datetime.fromtimestamp(last[0], timezone.utc).date().isoformat()}


def fred(sid, k):
    q = urllib.parse.urlencode({"series_id": sid, "api_key": k, "file_type": "json", "sort_order": "desc", "limit": 30})
    obs = [o for o in json.loads(fetch("https://api.stlouisfed.org/fred/series/observations?" + q))["observations"] if o["value"] != "."]
    v = lambda i: float(obs[min(i, len(obs) - 1)]["value"])
    return {"value": v(0), "prev": v(1), "week_ago": v(5), "month_ago": v(21), "date": obs[0]["date"]}


def collect_prices(specs):
    k = key("FRED_API_KEY")
    def one(p):
        try:
            d = yahoo(p["symbol"]) if p["via"] == "yahoo" else (fred(p["symbol"], k) if k else None)
            if d is None:
                return {**p, "ok": False, "error": "열쇠 없음"}
            return {**p, "ok": True, **d}
        except Exception as e:
            return {**p, "ok": False, "error": f"{type(e).__name__}: {str(e)[:60]}"}
    with cf.ThreadPoolExecutor(8) as ex:
        return list(ex.map(one, specs))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=36)
    ap.add_argument("--out", default=str(HERE / "data" / "raw.json"))
    ap.add_argument("--store", default=str(HERE / "data" / "store.json"))
    ap.add_argument("--keep-days", type=int, default=14)
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    cfg = json.loads((HERE / "sources.json").read_text("utf-8"))
    now = datetime.now(KST)
    now_iso = now.isoformat(timespec="minutes")
    since = (now - timedelta(hours=a.hours)).isoformat(timespec="minutes")
    floor = (now - timedelta(days=a.keep_days)).isoformat(timespec="minutes")
    with cf.ThreadPoolExecutor(16) as ex:
        srcs = list(ex.map(lambda s: collect_source(s, floor), cfg["sources"]))
    prices = collect_prices(cfg["prices"])

    # 보관함: 피드는 최신 10~50건만 주므로, 돌 때마다 새 것을 쌓아 둔다. 주소가 열쇠, 먼저 본 원천이 임자(원문이 매체보다 먼저 온다).
    sp = Path(a.store)
    store = json.loads(sp.read_text("utf-8")) if sp.exists() else {}
    new = dup = 0
    for s in srcs:
        for i in s["items"]:
            u = i["url"].split("?")[0].rstrip("/")
            if u in store:
                dup += 1
                continue
            store[u] = {**i, "src": s["id"], "first_seen": now_iso}
            new += 1
    eff = lambda i: i["at"] or i["first_seen"]            # 날짜 없는 피드(닛케이 등)는 처음 본 시각으로 친다
    store = {u: i for u, i in store.items() if eff(i) >= floor}
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(json.dumps(store, ensure_ascii=False), "utf-8")
    by = {}
    for i in store.values():
        if eff(i) >= since:
            by.setdefault(i["src"], []).append({k: v for k, v in i.items() if k != "src"})
    for s in srcs:
        s["items"] = sorted(by.get(s["id"], []), key=eff, reverse=True)
    out = {"generated_at": now_iso, "window_hours": a.hours, "since": since,
           "counts": {"sources": len(srcs), "ok": sum(s["ok"] for s in srcs), "items": sum(len(s["items"]) for s in srcs),
                      "new_this_run": new, "already_stored": dup, "stored_total": len(store), "keep_days": a.keep_days},
           "prices": prices, "sources": srcs}
    p = Path(a.out); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1), "utf-8")
    for layer in ("결정", "숫자", "매체"):
        ss = [s for s in srcs if s["layer"] == layer]
        print(f"[{layer}] 원천 {len(ss)} · 열림 {sum(s['ok'] for s in ss)} · 기사 {sum(len(s['items']) for s in ss)}")
        for s in ss:
            print(f"   {'O' if s['ok'] else 'X'} {s['name']}: " + (f"{len(s['items'])}건 (전체 {s['total']}, 날짜 없음 {s['undated']})" if s["ok"] else s["error"]))
    print(f"[가격] {sum(p['ok'] for p in prices)}/{len(prices)} · 못 받은 것: {[p['name'] for p in prices if not p['ok']]}")
    c = out["counts"]
    print(f"[보관함] 이번에 새로 {c['new_this_run']}건 · 이미 있던 것 {c['already_stored']}건 · 보관 {c['stored_total']}건 ({a.keep_days}일)")
    print("→", p, f"({p.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
