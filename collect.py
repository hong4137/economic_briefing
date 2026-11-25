"""경제 브리핑 원천 수집기 — AI 를 쓰지 않는다.

    python collect.py [--hours 36] [--out data/raw.json]

sources.json 의 원천을 받아 한 벌의 JSON 으로 남긴다. 층(결정·숫자·가격·매체)과 원문 제목·주소·시각만 담는다.
세 곳이 이 파일을 읽는다: 모의투자 아침 한 장(사실만), 경제 브리핑(사람이 읽는 뉴스), Daily UptoDate.
열쇠(FRED·ECOS)는 환경변수 → `../_열쇠/keys.env` 순으로 찾고, 없으면 그 줄만 "받지 못함"이 된다.
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
def _yahoo_rows(host, sym, rng):
    """(날짜, 종가) 줄들과, 받은 자료에 대한 메모. 날짜는 그 시장의 현지 날짜다."""
    j = json.loads(fetch(f"https://{host}.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}?range={rng}&interval=1d"))
    r = j["chart"]["result"][0]
    m = r.get("meta") or {}
    off = m.get("gmtoffset") or 0
    day = lambda t: datetime.fromtimestamp(t + off, timezone.utc).date().isoformat()
    pairs = list(zip(r["timestamp"], r["indicators"]["quote"][0]["close"]))
    rows = [(day(t), c) for t, c in pairs if c is not None]
    note = {"host": host, "bars": len(pairs), "null_tail": next((i for i, (_, c) in enumerate(reversed(pairs)) if c is not None), len(pairs))}
    # 야후는 가끔 가장 최근 거래일의 일봉을 빼거나 비워서 준다(아침 6시 수집에서 아시아 지수가 하루 전 값으로 들어왔다, 2026-11-19).
    # 같은 응답의 meta 에 있는 "정규장 마지막 값과 그 시각"이 일봉의 마지막 날보다 뒤면 그것을 마지막 줄로 붙인다.
    mt, mp = m.get("regularMarketTime"), m.get("regularMarketPrice")
    if mt and mp is not None and rows and day(mt) > rows[-1][0]:
        rows.append((day(mt), mp))
        note["from_meta"] = True
    return rows, note


def yahoo(sym):
    """일봉을 두 번(다른 서버·다른 기간) 받아 마지막 날짜가 더 늦은 쪽을 쓴다. 한쪽이 묵은 자료를 줄 때를 걸러낸다."""
    got, err = [], None
    for host, rng in (("query1", "3mo"), ("query2", "1mo")):
        try:
            got.append(_yahoo_rows(host, sym, rng))
        except Exception as e:
            err = e
    if not got:
        raise err
    rows, note = max(got, key=lambda g: (g[0][-1][0], len(g[0])))
    if len(got) == 2 and got[0][0][-1][0] != got[1][0][-1][0]:
        note["disagree"] = [g[0][-1][0] for g in got]          # 두 응답의 마지막 날짜가 달랐다 — 나중에 원인을 볼 수 있게 남긴다
    last, prev = rows[-1], rows[-2]
    wk = rows[-6][1] if len(rows) >= 6 else rows[0][1]
    mo = rows[-22][1] if len(rows) >= 22 else rows[0][1]
    return {"value": last[1], "prev": prev[1], "week_ago": wk, "month_ago": mo, "date": last[0], "fetch_note": note}


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


# ---------- 한국 수급·거시 (사실만, 해석 없음) ----------
# 투자자 구분 코드(네이버 증권 화면의 묶음 방식을 그대로 따른다): 9001 기타외국인 → 외국인, 3100 사모 → 투신, 7000 국가·지자체 → 연기금 등.
_FLOW = {"8000": "individual", "9000": "foreign", "9001": "foreign", "1000": "financial", "2000": "insurance", "3000": "trust",
         "3100": "trust", "4000": "bank", "5000": "other_financial", "6000": "pension", "7000": "pension", "7100": "other_corp"}
_INST = ("financial", "insurance", "trust", "bank", "other_financial", "pension")


def kr_flow(market, days=130):
    """코스피·코스닥 시장 전체의 투자자별 순매수(억 원), 최근 거래일부터. 원천: 네이버 증권(KRX 정규시장 자료)."""
    q = urllib.parse.urlencode({"tradeType": "KRX", "marketType": market, "bizdate": datetime.now(KST).strftime("%Y%m%d"),
                                "startIdx": 0, "pageSize": days})
    rows = []
    for d in json.loads(fetch("https://stock.naver.com/api/domestic/market/trend/daily?" + q))["content"]:
        r = dict.fromkeys(list(_INST) + ["individual", "foreign", "other_corp"], 0.0)
        for n in d["netAmounts"]:
            if n["investorGubun"] in _FLOW:
                r[_FLOW[n["investorGubun"]]] += float(n.get("diffValue") or 0) / 1e8
        r["institution"] = sum(r[k] for k in _INST)
        b = d["bizdate"]
        rows.append({"date": f"{b[:4]}-{b[4:6]}-{b[6:]}", **{k: round(v) for k, v in r.items()}})
    if not rows:
        raise RuntimeError("빈 응답")
    sums = {f"{n}d": {k: sum(r[k] for r in rows[:n]) for k in ("foreign", "institution", "individual")} for n in (5, 20, 60) if len(rows) >= n}
    return {"rows": rows, "sums": sums}


# ECOS 월별 계열: (이름, 표, 항목, 단위). 전년 같은 달 대비 변화율은 지수·금액에서 직접 셈한다.
_ECOS_M = [("cpi", "소비자물가 총지수", "901Y009", "0", "2020=100"),
           ("cpi_core", "근원물가(농산물·석유류 제외)", "901Y010", "QB", "2020=100"),
           ("cpi_core_oecd", "근원물가(식료품·에너지 제외)", "901Y010", "DB", "2020=100"),
           ("exports", "수출(통관 기준)", "901Y118", "T002", "천 달러"),
           ("imports", "수입(통관 기준)", "901Y118", "T004", "천 달러")]


def kr_macro(k, months=30):
    now = datetime.now(KST)
    start = f"{now.year - (months // 12 + 1)}{now.month:02d}"
    out = {}
    for sid, name, table, item, unit in _ECOS_M:
        try:
            j = json.loads(fetch(f"https://ecos.bok.or.kr/api/StatisticSearch/{k}/json/kr/1/100/{table}/M/{start}/{now:%Y%m}/{item}"))
            obs = {r["TIME"]: float(r["DATA_VALUE"]) for r in j["StatisticSearch"]["row"] if r.get("DATA_VALUE") not in (None, "")}
            rows = []
            for t in sorted(obs, reverse=True)[:13]:
                ly = f"{int(t[:4]) - 1}{t[4:]}"
                rows.append({"month": f"{t[:4]}-{t[4:]}", "value": obs[t],
                             "yoy_pct": round((obs[t] / obs[ly] - 1) * 100, 1) if obs.get(ly) else None})
            out[sid] = {"name": name, "unit": unit, "ok": True, "latest": rows[0]["month"], "rows": rows}
        except Exception as e:
            out[sid] = {"name": name, "unit": unit, "ok": False, "error": f"{type(e).__name__}: {str(e)[:60]}"}
    if out["exports"]["ok"] and out["imports"]["ok"]:
        im = {r["month"]: r["value"] for r in out["imports"]["rows"]}
        out["trade_balance"] = {"name": "무역수지(수출−수입)", "unit": "천 달러", "ok": True,
                                "rows": [{"month": r["month"], "value": round(r["value"] - im[r["month"]])} for r in out["exports"]["rows"] if r["month"] in im]}
    return out


def collect_kr(prev):
    """받지 못하면 지난번 raw.json 의 값을 그대로 두고 표시만 한다(carried=True). 그래야 한 번의 실패로 칸이 비지 않는다."""
    now_iso = datetime.now(KST).isoformat(timespec="minutes")
    old = (prev or {}).get("kr") or {}
    flow = {"source": "네이버 증권 시장 투자자별 매매동향(KRX 정규시장, NXT 제외)", "unit": "억 원, 순매수(+)·순매도(−)",
            "note": "기관(institution) = 금융투자+보험+투신(사모 포함)+은행+기타금융+연기금 등(국가 포함). 외국인은 기타외국인 포함.", "markets": {}}
    for m in ("KOSPI", "KOSDAQ"):
        try:
            flow["markets"][m] = {"ok": True, "fetched_at": now_iso, **kr_flow(m)}
        except Exception as e:
            o = ((old.get("flow") or {}).get("markets") or {}).get(m)
            err = f"{type(e).__name__}: {str(e)[:80]}"
            flow["markets"][m] = {**o, "ok": False, "carried": True, "error": err} if o and o.get("rows") else {"ok": False, "error": err}
    k = key("ECOS_API_KEY")
    macro = {"source": "한국은행 ECOS (소비자물가 901Y009·901Y010, 수출입 총괄 901Y118 — 관세청 통관 확정치라 한두 달 늦다)",
             "fetched_at": now_iso, "series": kr_macro(k) if k else {}}
    if not k:
        macro["error"] = "열쇠 없음(ECOS_API_KEY)"
    olds = (old.get("macro") or {}).get("series") or {}
    for sid in list(macro["series"]) + [s for s in olds if s not in macro["series"]]:
        s, o = macro["series"].get(sid) or {}, olds.get(sid)
        if not s.get("ok") and o and o.get("rows"):
            macro["series"][sid] = {**o, "ok": False, "carried": True, "error": s.get("error") or macro.get("error")}
    return {"flow": flow, "macro": macro}


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
    try:
        prev = json.loads(Path(a.out).read_text("utf-8")) if Path(a.out).exists() else None
    except Exception:
        prev = None
    kr = collect_kr(prev)

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
           "prices": prices, "kr": kr, "sources": srcs}
    p = Path(a.out); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1), "utf-8")
    for layer in ("결정", "숫자", "매체"):
        ss = [s for s in srcs if s["layer"] == layer]
        print(f"[{layer}] 원천 {len(ss)} · 열림 {sum(s['ok'] for s in ss)} · 기사 {sum(len(s['items']) for s in ss)}")
        for s in ss:
            print(f"   {'O' if s['ok'] else 'X'} {s['name']}: " + (f"{len(s['items'])}건 (전체 {s['total']}, 날짜 없음 {s['undated']})" if s["ok"] else s["error"]))
    print(f"[가격] {sum(p['ok'] for p in prices)}/{len(prices)} · 못 받은 것: {[p['name'] for p in prices if not p['ok']]}")
    for m, f in kr["flow"]["markets"].items():
        r = (f.get("rows") or [{}])[0]
        print(f"[한국 수급] {'O' if f['ok'] else 'X'} {m} {r.get('date', '')}: 외국인 {r.get('foreign')} · 기관 {r.get('institution')} · 개인 {r.get('individual')} (억 원)"
              + ("" if f["ok"] else f" — {f.get('error')}" + (" (지난 값 유지)" if f.get("carried") else "")))
    print("[한국 거시] " + " · ".join(f"{'O' if s['ok'] else 'X'} {s['name']} {s.get('latest') or (s.get('rows') or [{}])[0].get('month', '')}"
                                     for s in kr["macro"]["series"].values()) + (f" — {kr['macro']['error']}" if kr["macro"].get("error") else ""))
    c = out["counts"]
    print(f"[보관함] 이번에 새로 {c['new_this_run']}건 · 이미 있던 것 {c['already_stored']}건 · 보관 {c['stored_total']}건 ({a.keep_days}일)")
    print("→", p, f"({p.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
