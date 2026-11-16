"""감독 겸 사서의 도구 — 아침 한 장을 만들고, 누구를 깨울지 정한다. AI 를 쓰지 않는다(전부 코드).

  python desk.py brief --market KR|US|WEEK     공통 자료 한 장을 만든다 → 공통-서고/아침-한-장/
  python desk.py wake  --market KR|US|WEEK     캐릭터마다 깨울지 정해 결과를 JSON 으로 찍는다 (깨울 사람이 있으면 한 장도 만든다)
                                               깨울 캐릭터의 메시지는 out/ 에 파일로 둔다. 늦게 돌아도 차례를 잃지 않는다(차례 장부)
  python desk.py turns                         차례 장부 보기

원칙
- 한 장에는 **사실만** 싣는다. 판단·추천을 쓰지 않는다. 받지 못한 자료는 "받지 못함"이라고 적는다(추정으로 메우지 않는다).
- 캐릭터의 계좌·일지·위키 내용을 공통 자료에 옮기지 않는다. 한 장은 모든 캐릭터가 똑같이 읽는 것이다.
- 거래소 자료와 캐릭터 폴더는 읽기만 한다.
"""
import time
import argparse
import json
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
HERE = Path(__file__).resolve().parent
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
LABEL = {"KR": "한국장 전 점검", "US": "미국장 전 점검", "WEEK": "주간 회의"}

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def cfg():
    import os
    return json.loads((HERE / os.environ.get("DESK_CONFIG", "config.json")).read_text(encoding="utf-8"))


def rel(p):
    p = Path(p)
    return (p if p.is_absolute() else HERE / p).resolve()


def get(url, headers=None, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def is_kr(sym):
    return bool(re.fullmatch(r"\d{6}", sym))


# ---------- 시세 ----------
def daily(sym, rng="3mo"):
    """[(날짜, 종가)] 최근 것까지. 미국·지수·환율은 야후, 한국 종목은 다음 금융(정규장 종가)."""
    if is_kr(sym):
        per = 260 if rng == "1y" else 30
        j = get(f"https://finance.daum.net/api/quote/A{sym}/days?symbolCode=A{sym}&page=1&perPage={per}&pagination=true",
                {"Referer": f"https://finance.daum.net/quotes/A{sym}"})
        rows = [(x["date"][:10], x["tradePrice"]) for x in j.get("data", []) if x.get("tradePrice")]
        return sorted(rows)
    from urllib.parse import quote
    r = get(f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(sym)}?range={rng}&interval=1d")["chart"]["result"][0]
    off = r["meta"].get("gmtoffset", 0)
    cl = r["indicators"]["quote"][0]["close"]
    rows = [(datetime.fromtimestamp(t + off, timezone.utc).strftime("%Y-%m-%d"), c)
            for t, c in zip(r.get("timestamp") or [], cl) if c is not None]
    return rows


def move(rows):
    """(기준일, 마지막 값, 하루 변동 %, 닷새 변동 %)"""
    if len(rows) < 2:
        return None
    d, last = rows[-1]
    d1 = (last / rows[-2][1] - 1) * 100
    d5 = (last / rows[-6][1] - 1) * 100 if len(rows) >= 6 else None
    return d, last, d1, d5


def pct(v):
    return "—" if v is None else f"{v:+.1f}%"


def num(v):
    return f"{v:,.0f}" if abs(v) >= 1000 else f"{v:,.2f}"


# ---------- 한 장 ----------
def universe(c):
    """기본 목록 + 모든 계좌의 보유·대기 주문·지켜보기 종목. (누구 것인지는 싣지 않는다.)"""
    syms = dict(c["universe"])
    for ch in c["characters"]:
        st = load_state(c, ch)
        if not st:
            continue
        for p in st.get("positions", []):
            syms.setdefault(p["symbol"], [p.get("name") or p["symbol"]])
        for o in st.get("orders", []):
            if o.get("status") == "pending":
                syms.setdefault(o["symbol"], [o.get("name") or o["symbol"]])
        for w in st.get("watches", []) or []:
            if w.get("symbol"):
                syms.setdefault(w["symbol"], [w["symbol"]])
    return syms


def load_state(c, ch):
    f = rel(c["data_dir"]) / ch["state"]
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return None


def pull(c):
    try:
        subprocess.run(["git", "-C", str(rel(c["data_dir"])), "pull", "--rebase", "-q"],
                       capture_output=True, text=True, timeout=120)
    except Exception:
        pass


DECISIVE = re.compile(r"사임|사퇴|해임|퇴진|인수|합병|파산|부도|상장폐지|조사 착수|기소|체포|구속|전쟁|침공|휴전|디폴트|리콜|해킹|"
                      r"resign|steps? down|depart|ouster|oust|\bexit\b|acquir|bankrupt|merger|indict|arrest|ceasefire|default", re.I)
ECON_KW = re.compile(r"금리|물가|인플레|환율|원/달러|달러|유가|원유|연준|FOMC|한은|한국은행|고용|실업|관세|전쟁|휴전|수출|국채|채권|"
                     r"증시|코스피|코스닥|원자재|금값|셧다운|GDP|CPI|PCE|경기|침체|외국인|공매도|부채한도|무역수지")
ECON_EN = re.compile(r"\bFed\b|Federal Reserve|rate hike|rate cut|interest rate|inflation|\bCPI\b|\bPCE\b|jobs|payroll|unemployment|"
                     r"oil|crude|tariff|Treasury|yield|\bwar\b|Iran|ceasefire|shutdown|\bGDP\b|recession|dollar|Nasdaq|S&P|Dow|"
                     r"stocks (fall|rise|slide|rally|tumble|drop)|sell-?off|earnings|Nvidia|midterm", re.I)
PROMO_EN = re.compile(r"bullish|upside|\bbuy\b|picks?\b|top .*analysts|strategy to|should you|here's why|looks attractive|how to|winners", re.I)


def csv_series(c, name, col):
    """NDX Watch 가 매일 받아 두는 표(재무부 금리·MOVE 등)를 다시 쓴다. [(날짜, 값)]"""
    base = c["sources"]["ndx_watch"].rsplit("/", 1)[0]
    req = urllib.request.Request(f"{base}/{name}", headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=25) as r:
        lines = r.read().decode("utf-8-sig").splitlines()
    head = [h.strip() for h in lines[0].split(",")]
    i = head.index(col)
    out = []
    for ln in lines[1:]:
        p = ln.split(",")
        try:
            out.append((p[0][:10], float(p[i])))
        except (ValueError, IndexError):
            pass
    return out


def back(rows, n):
    return rows[-1 - n][1] if len(rows) > n else None


def change(rows, n, unit):
    old = back(rows, n)
    if old is None:
        return "—"
    last = rows[-1][1]
    return f"{last - old:+.2f}%p" if unit == "%" else pct((last / old - 1) * 100)


def key(name):
    """공용 열쇠 보관함(통합 개발/_열쇠)에서 읽는다. 없으면 None — 그 자료는 '받지 못함'으로 남는다."""
    import importlib.util
    import os
    if os.environ.get(name):
        return os.environ[name]
    f = rel(cfg().get("keys_dir", "../_열쇠")) / "keys.py"
    try:
        spec = importlib.util.spec_from_file_location("invest_keys", f)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m.get(name)
    except Exception:
        return None


def ecos_series(table, item, days=70):
    """한국은행 경제통계(ECOS) 일별 계열 [(날짜, 값)]."""
    k = key("ECOS_API_KEY")
    if not k:
        raise RuntimeError("ECOS 열쇠 없음")
    end = datetime.now(KST).date()
    start = end - timedelta(days=days)
    j = get(f"https://ecos.bok.or.kr/api/StatisticSearch/{k}/json/kr/1/100/{table}/D/{start:%Y%m%d}/{end:%Y%m%d}/{item}")
    out = []
    for r in j.get("StatisticSearch", {}).get("row", []):
        try:
            t = r["TIME"]
            out.append((f"{t[:4]}-{t[4:6]}-{t[6:]}", float(r["DATA_VALUE"])))
        except (KeyError, ValueError):
            pass
    return out


def fred_obs(series, n=16):
    """FRED 관측값 {날짜: 값} (최근 n개)."""
    from urllib.parse import urlencode
    k = key("FRED_API_KEY")
    if not k:
        raise RuntimeError("FRED 열쇠 없음")
    j = get("https://api.stlouisfed.org/fred/series/observations?" + urlencode(
        {"series_id": series, "api_key": k, "file_type": "json", "sort_order": "desc", "limit": n}))
    return {o["date"]: float(o["value"]) for o in j.get("observations", []) if o.get("value") not in (".", "", None)}


def us_releases():
    """최근 발표된 미국 지표 줄들 [(이름, 기준, 값, 직전)]. 계산은 전년 같은 달 대비·전월 대비 차이뿐이다."""
    out = []

    def yoy(series, name):
        o = fred_obs(series)
        ds = sorted(o, reverse=True)

        def rate(d):
            prev = f"{int(d[:4]) - 1}{d[4:]}"
            return (o[d] / o[prev] - 1) * 100 if prev in o else None
        cur = rate(ds[0])
        old = rate(ds[1]) if len(ds) > 1 else None
        out.append((name, ds[0][:7], "전년 같은 달 자료 없음" if cur is None else f"{cur:+.1f}%",
                    "—" if old is None else f"{old:+.1f}% ({ds[1][:7]})"))

    for series, name in (("CPIAUCSL", "소비자물가(CPI), 전년 대비"), ("CPILFESL", "근원 소비자물가, 전년 대비"), ("PCEPI", "개인소비지출 물가(PCE), 전년 대비")):
        try:
            yoy(series, name)
        except Exception:
            out.append((name, "—", "받지 못함", "—"))
    try:
        o = fred_obs("UNRATE")
        ds = sorted(o, reverse=True)
        out.append(("실업률", ds[0][:7], f"{o[ds[0]]:.1f}%", f"{o[ds[1]]:.1f}% ({ds[1][:7]})"))
    except Exception:
        out.append(("실업률", "—", "받지 못함", "—"))
    try:
        o = fred_obs("PAYEMS")
        ds = sorted(o, reverse=True)
        out.append(("비농업 일자리 증감", ds[0][:7], f"{o[ds[0]] - o[ds[1]]:+,.0f}천 명",
                    f"{o[ds[1]] - o[ds[2]]:+,.0f}천 명 ({ds[1][:7]})" if len(ds) > 2 else "—"))
    except Exception:
        out.append(("비농업 일자리 증감", "—", "받지 못함", "—"))
    try:
        up, lo = fred_obs("DFEDTARU", 40), fred_obs("DFEDTARL", 40)
        ds = sorted(up, reverse=True)
        cur = (lo.get(ds[0]), up[ds[0]])
        prev = next(((lo.get(d), up[d], d) for d in ds if up[d] != cur[1]), None)
        out.append(("연준 기준금리 범위", ds[0], f"{cur[0]:.2f}~{cur[1]:.2f}%",
                    f"{prev[0]:.2f}~{prev[1]:.2f}% ({prev[2]} 까지)" if prev else "최근 40일 동안 변동 없음"))
    except Exception:
        out.append(("연준 기준금리 범위", "—", "받지 못함", "—"))
    return out


def us_earnings(symbols, start, end):
    """Finnhub 실적 일정 {종목: (미국 날짜, 시간대, 주당이익 예상)}."""
    from urllib.parse import urlencode
    k = key("FINNHUB_API_KEY")
    if not k:
        raise RuntimeError("Finnhub 열쇠 없음")
    j = get("https://finnhub.io/api/v1/calendar/earnings?" + urlencode({"from": start, "to": end, "token": k}), timeout=40)
    out = {}
    for e in j.get("earningsCalendar", []):
        if e.get("symbol") in symbols and e["symbol"] not in out:
            out[e["symbol"]] = (e["date"], e.get("hour") or "", e.get("epsEstimate"))
    return out


def rss(url, timeout=20):
    """[(시각, 제목, 주소)] — 시각을 못 읽으면 None."""
    from email.utils import parsedate_to_datetime
    import html
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        text = r.read().decode("utf-8", "replace")
    out = []
    for it in re.findall(r"<item>.*?</item>", text, flags=re.S):
        def tag(n):
            m = re.search(rf"<{n}>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{n}>", it, flags=re.S)
            return html.unescape(m.group(1).strip()) if m else ""
        try:
            when = parsedate_to_datetime(tag("pubDate")).astimezone(KST)
        except Exception:
            when = None
        out.append((when, re.sub(r"\s+", " ", tag("title")), tag("link")))
    return out


def won(v):
    """원 단위 금액을 조·억으로."""
    if v is None:
        return "—"
    a = abs(v)
    if a >= 1e12:
        return f"{v / 1e12:,.2f}조"
    return f"{v / 1e8:,.0f}억"


def kr_listed_shares(sym):
    """정기보고서에서 주식 수를 못 얻었을 때 — 다음 금융 일별 자료의 상장주식수."""
    try:
        j = get(f"https://finance.daum.net/api/quote/A{sym}/days?symbolCode=A{sym}&page=1&perPage=1&pagination=true",
                {"Referer": f"https://finance.daum.net/quotes/A{sym}"})
        v = (j.get("data") or [{}])[0].get("listedSharesCount")
        return int(v) if v else None
    except Exception:
        return None


def kr_stock_numbers(c, uni, today):
    """지켜보는 한국 종목의 기본 숫자 줄들. 7-GEMS 의 한국 공시 도구(오픈API 1순위)를 다시 쓴다.
    하루에 한 번만 받는다(공통-서고/종목-숫자/날짜.json)."""
    import importlib.util
    cache = rel(c["library_dir"]) / "종목-숫자" / f"{today.isoformat()}.json"
    data = {}
    if not cache.exists() and cache.parent.exists():
        old = sorted(f for f in cache.parent.glob("20??-??-??.json") if f.stem <= today.isoformat())
        if old and (today - datetime.strptime(old[-1].stem, "%Y-%m-%d").date()).days <= 8:
            cache = old[-1]                 # 며칠 전에 받아 둔 것 (주 1회 갱신)
    if cache.exists():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    tool = Path(c.get("kr_register_tool", r"D:\개발\기업분석\기업 분석 자동화\new_skills_v2\investment-orchestrator\_shared\tools\build_filing_register_kr.py"))
    mod = None
    lines, miss = [], []
    for sym in [s for s in uni if is_kr(s) and "ETF" not in uni[s][0]]:
        if sym not in data:
            try:
                if mod is None:
                    spec = importlib.util.spec_from_file_location("kr_register", tool)
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                reg = mod.build(sym, sector=(c.get("kr_sector") or {}).get(sym, "general"))
                rows = daily(sym, "1y")
                f = reg.get("facts", {})
                pick = lambda k, x="value": (f.get(k) or {}).get(x)
                data[sym] = {
                    "name": uni[sym][0], "report": (reg.get("latest_filings") or [{}])[0].get("form"), "via": (reg.get("method") or {}).get("statements_latest"),
                    "close": rows[-1][1], "close_date": rows[-1][0], "hi": max(r[1] for r in rows), "lo": min(r[1] for r in rows), "days": len(rows),
                    "rev": pick("rev_total"), "rev_py": pick("rev_total", "prior_year_q"),
                    "nii": pick("net_interest_income"), "nii_py": pick("net_interest_income", "prior_year_q"),
                    "op": pick("operating_income"), "op_py": pick("operating_income", "prior_year_q"),
                    "ni": pick("net_income_parent") or pick("net_income"), "ni_py": pick("net_income_parent", "prior_year_q") or pick("net_income", "prior_year_q"),
                    "ni_ttm": pick("net_income_parent_ttm") or pick("net_income_ttm"),
                    "shares": pick("shares_outstanding") or kr_listed_shares(sym),
                    "shares_src": "보고서" if pick("shares_outstanding") else "다음 금융 상장주식수",
                    "equity": pick("equity_parent") or pick("total_equity"),
                    "basis": pick("rev_total", "basis") or pick("operating_income", "basis"),
                }
            except Exception as e:
                data[sym] = {"name": uni[sym][0], "error": type(e).__name__}
        d = data[sym]
        if d.get("error"):
            lines.append(f"- **{d['name']} ({sym})** — 받지 못함")
            miss.append(f"{d['name']} 기본 숫자")
            continue

        def yoy(cur, prev):
            if cur is None or prev in (None, 0):
                return ""
            if prev < 0 or cur < 0:
                return f" (전년 같은 분기 {won(prev)})"
            return f" (전년비 {(cur / prev - 1) * 100:+.0f}%)"

        cap = d["close"] * d["shares"] if d.get("shares") else None
        pe = cap / d["ni_ttm"] if cap and d.get("ni_ttm") and d["ni_ttm"] > 0 else None
        pb = cap / d["equity"] if cap and d.get("equity") and d["equity"] > 0 else None
        top = d["nii"] if d.get("nii") is not None else d.get("rev")
        top_py = d["nii_py"] if d.get("nii") is not None else d.get("rev_py")
        top_name = "순이자이익" if d.get("nii") is not None else "매출"
        whole = "" if d.get("basis") == "quarter" else " (석 달치가 아니라 누적)"
        lines.append(f"- **{d['name']} ({sym})** — 종가 {num(d['close'])}원 ({d['close_date'][5:]}) · 최근 {d['days']}거래일 범위 {num(d['lo'])}~{num(d['hi'])}원")
        lines.append(f"    · {d.get('report') or '보고서 미상'}{whole}: {top_name} {won(top)}{yoy(top, top_py)} · 영업이익 {won(d.get('op'))}{yoy(d.get('op'), d.get('op_py'))} · 지배주주 순이익 {won(d.get('ni'))}{yoy(d.get('ni'), d.get('ni_py'))}")
        lines.append(f"    · 최근 12개월 지배주주 순이익 {won(d.get('ni_ttm'))} · 시가총액 {won(cap)} · P/E {'—' if pe is None else f'{pe:.1f}배'} · P/B {'—' if pb is None else f'{pb:.2f}배'}"
                     + ("" if d.get("via") == "api" else " · (공식 API 가 아니라 웹 원문에서 뽑음)"))
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    if not lines:
        lines.append("- 지켜보는 한국 종목이 없다")
    return lines, miss


def research_file(c):
    return rel(c["library_dir"]) / "조사" / "latest.json"


def check_research(d):
    """사서 조사 결과의 모양을 검사한다. 문제 목록을 돌려준다(없으면 빈 목록)."""
    bad = []
    if not isinstance(d, dict):
        return ["맨 바깥이 객체가 아니다"]
    try:
        datetime.fromisoformat(d.get("made_at", ""))
    except Exception:
        bad.append("made_at 이 시각이 아니다 (예: 2026-11-22T17:40+09:00)")
    day = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    for i, e in enumerate(d.get("events") or [], 1):
        for k in ("name", "last_fact"):
            if not str(e.get(k, "")).strip():
                bad.append(f"사건 {i}: {k} 가 비었다")
        if not str(e.get("source_url", "")).startswith("http"):
            bad.append(f"사건 {i} ({e.get('name', '?')}): 원문 주소(source_url)가 없다")
        for k in ("last_fact_date", "next_date"):
            if e.get(k) and not day.match(str(e[k])):
                bad.append(f"사건 {i}: {k} 는 2026-11-22 꼴이어야 한다")
    for i, e in enumerate(d.get("calendar") or [], 1):
        if not day.match(str(e.get("date", ""))):
            bad.append(f"일정 {i}: date 가 2026-11-22 꼴이 아니다")
        if not str(e.get("title", "")).strip():
            bad.append(f"일정 {i}: title 이 비었다")
        if e.get("confirmed") and not str(e.get("source_url", "")).startswith("http"):
            bad.append(f"일정 {i} ({e.get('title', '?')}): 확정이라면서 원문 주소가 없다")
    if len(d.get("events") or []) > 10:
        bad.append("사건이 10개를 넘는다 — 줄인다")
    if len(d.get("calendar") or []) > 60:
        bad.append("일정이 60개를 넘는다 — 줄인다")
    return bad


def load_research(c):
    f = research_file(c)
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return None
    return None if check_research(d) else d


def cmd_research_check(a):
    """사서가 조사 뒤에 돌린다. 통과하면 날짜가 붙은 사본을 남긴다."""
    c = cfg()
    f = research_file(c)
    if not f.exists():
        sys.exit(f"조사 결과가 없습니다: {f}")
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except Exception as e:
        sys.exit(f"JSON 으로 읽히지 않습니다: {e}")
    bad = check_research(d)
    if bad:
        print("고칠 것:")
        for b in bad:
            print(" -", b)
        sys.exit(2)
    keep = f.parent / f"{d['made_at'][:10]}.json"
    keep.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    print(f"통과 — 사건 {len(d.get('events') or [])}건, 일정 {len(d.get('calendar') or [])}건. 사본: {keep.name}")
    try:
        cmd_publish(a)                      # 저장소의 한 장 만들기도 이 조사를 읽게 보낸다
    except SystemExit as e:
        print("저장소로 보내지 못함:", e)
    except Exception as e:
        print("저장소로 보내지 못함:", type(e).__name__)


MADE_AT = "저장소에서 만듦" if __import__("os").environ.get("GITHUB_ACTIONS") else "이 PC 에서 만듦"
CLOUD_BRIEF = "https://raw.githubusercontent.com/hong4137/economic_briefing/data/library/{name}"      # {name} = 주소용으로 바꾼 "아침-한-장/날짜-시장.md"


def cloud_brief(market, now, c):
    """저장소가 정시에 만들어 둔 한 장을 받아 온다 — 모든 선수가 같은 글을 읽게 하려는 것이다.
    오늘 이 시장의 것이 있고 만든 지 4시간이 안 됐으면 공통 서고에 그대로 저장하고 그 경로를 돌려준다. 없거나 낡았으면 None."""
    name = f"{now.date().isoformat()}-{market}.md"
    try:
        url = c.get("cloud_brief_url", CLOUD_BRIEF).format(name=urllib.parse.quote("아침-한-장/" + name))
        with urllib.request.urlopen(urllib.request.Request(url + f"?t={int(now.timestamp())}", headers={"User-Agent": "Mozilla/5.0"}), timeout=20) as r:
            text = r.read().decode("utf-8")
        m = re.search(r"만든 시각 (\d\d)-(\d\d) (\d\d):(\d\d)", text)
        made = datetime(now.year, int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)), tzinfo=KST)
        if not (timedelta(0) <= now - made <= timedelta(hours=c.get("cloud_brief_max_age_h", 4))):
            return None
        out = rel(c["library_dir"]) / "아침-한-장" / name
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8", newline="\n")
        missing = re.search(r"## 받지 못한 자료\n\n- (.+)", text)
        return out, ([x.strip() for x in missing.group(1).split(",")] if missing else [])
    except Exception:
        return None


def make_brief(market, now):
    c = cfg()
    today = now.date()
    week = market == "WEEK"
    L = [f"# 아침 한 장 — {today.isoformat()} {LABEL[market]}", "",
         f"> 만든 시각 {now:%m-%d %H:%M} (한국 시간, {MADE_AT}). 사실만 모은 것이다. 판단은 없다. 모든 캐릭터가 같은 글을 읽는다.",
         "> 기사 제목은 남이 쓴 요약이다. **⚠ 가 붙은 제목(사임·인수·전쟁 같은 결정적 사건)은 원문을 확인하기 전에는 사실로 쓰지 않는다.**", ""]
    missing = []

    # 1. 시장과 거시
    L += ["## 시장과 거시 지표", "", "| 무엇 | 기준일 | 값 | 하루 | 닷새 | 한 달 |", "|---|---|---|---|---|---|"]
    for ix in c["indices"]:
        unit = ix.get("unit")
        try:
            if ix.get("csv"):
                rows = csv_series(c, ix["csv"], ix["col"])
            elif ix.get("ecos"):
                rows = ecos_series(*ix["ecos"])
            else:
                rows = daily(ix["symbol"])
            if not rows:
                raise ValueError
            d, last = rows[-1]
            val = f"{last:.2f}%" if unit == "%" else num(last)
            if len(rows) == 1:
                L.append(f"| {ix['name']} | {d[5:]} | {val} | 받지 못함 | 받지 못함 | 받지 못함 |")
            else:
                L.append(f"| {ix['name']} | {d[5:]} | {val} | {change(rows, 1, unit)} | {change(rows, 5, unit)} | {change(rows, 22, unit)} |")
        except Exception:
            L.append(f"| {ix['name']} | — | 받지 못함 | — | — | — |")
            missing.append(ix["name"])
    L += ["", "금리는 %p 차이, 나머지는 % 변화. 닷새·한 달은 거래일 5일·22일 전과 견준 것.",
          "아직 싣지 못하는 것: 외국인·기관 순매수, 다음 연준 회의의 시장 확률, 한국 물가.", ""]

    # 1-a. 최근 발표된 미국 지표 (주간 회의와 미국장 전에만)
    if market in ("WEEK", "US"):
        L += ["## 최근 발표된 미국 지표", "", "| 무엇 | 기준 | 값 | 직전 |", "|---|---|---|---|"]
        rel_rows = us_releases()
        for name, when, val, prev in rel_rows:
            L.append(f"| {name} | {when} | {val} | {prev} |")
        if any(r[2] == "받지 못함" for r in rel_rows):
            missing.append("미국 지표 일부")
        L += ["", "미국 세인트루이스 연준(FRED)에 올라온 가장 최근 값이다. 물가는 지수를 전년 같은 달과 견준 변화율이다(발표 기관의 공식 수치와 소수점이 조금 다를 수 있다).", ""]

    # 1-b. 미국 업종 (주간 회의와 미국장 전에만)
    if market in ("WEEK", "US") and c.get("sectors"):
        L += ["## 미국 업종 ETF", "", "| 업종 | 한 주 | 한 달 | 올해 |", "|---|---|---|---|"]
        for sym, name in c["sectors"].items():
            try:
                rows = daily(sym, "1y")
                yr = [r for r in rows if r[0][:4] == str(today.year)]
                ytd = pct((rows[-1][1] / yr[0][1] - 1) * 100) if yr and rows[0][0][:4] != str(today.year) else "—"
                L.append(f"| {name} ({sym}) | {change(rows, 5, None)} | {change(rows, 22, None)} | {ytd} |")
            except Exception:
                L.append(f"| {name} ({sym}) | 받지 못함 | — | — |")
                missing.append(sym)
        L.append("")

    # 2. 시장 계기판
    L += ["## 시장 계기판 (NDX Watch)", ""]
    try:
        s = get(c["sources"]["ndx_watch"])
        age = (today - datetime.strptime(s["date"], "%Y-%m-%d").date()).days
        L.append(f"- 기준일 {s['date']}" + (f" (**{age}일 전 자료**)" if age > 3 else "")
                 + f" · 나스닥100 하루 {s['ret1_pct']:+.2f}%, 사흘 {s['cum3_pct']:+.2f}% · 공포 지수 {s['vix']}")
        L.append(f"- 큰 변동(사건) 판정: {'**사건**' if s.get('event') else '아님'}")
        mo = s.get("monthly") or {}
        if mo.get("label"):
            L.append(f"- 계기판의 한 달 판단: {mo['label']}")
        warn = []
        for key, p in (s.get("panels") or {}).items():
            for g in p.get("g", []):
                if isinstance(g, dict) and g.get("state") in ("hot", "warn"):
                    warn.append(f"{g.get('name')}({'경고' if g['state'] == 'hot' else '주의'})")
        L.append("- 주의·경고가 켜진 계기: " + (", ".join(dict.fromkeys(warn)) if warn else "없음"))
        L.append("- 계기의 뜻과 문턱, 어느 것이 검증된 신호인지는 계기판 화면(ndx.sharktalk.co.kr)에 있다. 여기에는 켜졌다는 사실만 적는다.")
    except Exception:
        L.append("- 받지 못함")
        missing.append("시장 계기판")
    L.append("")

    # 2-b. 진행 중인 큰 사건 — 사서가 주 1회 조사해 둔 것
    rs = load_research(c)
    L += ["## 진행 중인 큰 사건", ""]
    if rs and rs.get("events"):
        age = (today - datetime.fromisoformat(rs["made_at"]).date()).days
        L.append(f"사서가 {rs['made_at'][:10]} 에 조사한 것이다" + (f" (**{age}일 전**)" if age > 8 else "") + ". 그 뒤의 일은 여기에 없다.")
        L.append("")
        for e in rs["events"]:
            if week:
                L.append(f"- **{e['name']}** (시작 {e.get('started') or '?'})")
                L.append(f"    · 마지막으로 확인된 사실 ({e.get('last_fact_date') or '날짜 모름'}): {e.get('last_fact', '')} — {e.get('source_url') or '원문 주소 없음'}")
                if e.get("next_what"):
                    L.append(f"    · 다음 고비: {e.get('next_date') or '날짜 미정'} {e['next_what']}")
                if e.get("unconfirmed"):
                    L.append(f"    · 확인하지 못한 것: {e['unconfirmed']}")
            else:
                nxt = f" · 다음 고비 {e.get('next_date') or '미정'} {e['next_what']}" if e.get("next_what") else ""
                L.append(f"- {e['name']} — 마지막 확인 {e.get('last_fact_date') or '?'}{nxt}")
        if not week:
            L.append("- 자세한 경과와 원문 주소는 주간 회의 한 장에 실린다.")
    else:
        L.append("- 아직 조사된 것이 없다. 전쟁·금리 국면 같은 큰 배경은 직접 확인한다.")
        missing.append("큰 사건 장부")
    L.append("")

    # 3. 일정 — 사서 조사 + 조사된 일정표 + 원자료(발표일·실적·연준) + 규칙으로 아는 정기 발표
    span = 42 if week else 3
    L += [f"## 일정 (오늘부터 {span}일)", ""]
    ev, seen = [], set()
    end = (today + timedelta(days=span)).isoformat()

    def add(e, src):
        # 같은 일정이 조사된 일정표와 원자료에 다른 이름("미국 10월 소매판매" / "미국 소매판매")으로 실려 있다 — 먼저 온 것만 싣는다
        key = (e.get("date"), e.get("region", ""), re.sub(r"[\W0-9A-Za-z월]", "", e.get("title", ""))[:8])
        if e.get("date") and today.isoformat() <= e["date"] <= end and key not in seen:
            seen.add(key)
            ev.append((e["date"], e.get("time") or "", e.get("region", ""), e["title"], e.get("importance"), src))

    for e in (rs or {}).get("calendar", []):               # 사서 조사를 먼저 — 예상치·직전 값과 원문 주소가 붙어 있다
        extra = []
        if e.get("consensus"):
            extra.append(f"예상 {e['consensus']}")
        if e.get("previous"):
            extra.append(f"직전 {e['previous']}")
        title = e.get("title", "") + (f" [{' · '.join(extra)}]" if extra else "")
        add({**e, "title": title}, ("사서 조사, 확정" if e.get("confirmed") else "사서 조사, **추정**") + (f" {e['source_url']}" if e.get("source_url") else ""))
    cal_at = ""
    try:
        cal = get(c["sources"]["calendar"])
        cal_at = str(cal.get("generated_at", ""))[:16]
        for e in cal.get("events", []):
            add(e, "조사됨" if e.get("verified") else "확인 안 됨")
    except Exception:
        missing.append("일정표")
    try:
        raw = get(c["sources"]["calendar"].replace("calendar.json", "calendar_raw.json"))
        for e in raw.get("events", []):
            add(e, f"원자료 {e.get('src', '')}")
        for e in raw.get("rule_candidates", []):
            add(e, "정기 발표(규칙)")
    except Exception:
        missing.append("일정 원자료")
    # 지켜보는 미국 종목의 실적 발표일 (Finnhub). 이미 같은 회사의 실적 일정이 실려 있으면 건너뛴다.
    try:
        uni_all = universe(c)
        us_syms = {s for s in uni_all if not is_kr(s) and "ETF" not in uni_all[s][0]}
        for sym, (us_date, hour, eps) in sorted(us_earnings(us_syms, today.isoformat(), end).items(), key=lambda kv: kv[1][0]):
            nm = uni_all[sym][0]
            d0 = datetime.strptime(us_date, "%Y-%m-%d").date()
            if hour == "amc":                                  # 미국 장 마감 뒤 = 한국 다음 날 아침
                kd, kt, when = d0 + timedelta(days=1), "06:00 이후", "미국 장 마감 뒤"
            elif hour == "bmo":
                kd, kt, when = d0, "저녁", "미국 개장 전"
            else:
                kd, kt, when = d0, "", "시각 미정"
            if any(nm in t and "실적" in t and abs((datetime.strptime(d, "%Y-%m-%d").date() - kd).days) <= 1 for d, _, _, t, _, _ in ev):
                continue
            if not today.isoformat() <= kd.isoformat() <= end:
                continue
            est = f" [주당이익 예상 {eps:.2f}달러]" if isinstance(eps, (int, float)) else ""
            ev.append((kd.isoformat(), kt, "US", f"{nm} 실적 발표 ({when}, 미국 날짜 {us_date[5:]}){est}", 4, "Finnhub 실적 일정 — 날짜는 추정일 수 있다"))
    except Exception:
        missing.append("실적 일정(Finnhub)")
    for d, t, region, title, imp, src in sorted(ev):
        L.append(f"- {d[5:]} {t} [{region}] {title}" + (f" (중요도 {imp})" if imp else "") + f" — {src}")
    if not ev:
        L.append("- 실린 일정 없음")
    if rs and rs.get("calendar"):
        L += [f"- \"사서 조사\"는 {rs['made_at'][:10]} 에 향후 6주를 조사한 것이다. \"추정\"은 날짜가 공식 발표되지 않은 것이다. 그 밖의 줄은 매일 갱신되는 일정표(만들어진 때 {cal_at or '받지 못함'})에서 왔고 향후 한 주쯤만 담는다.",
              "- 예상치와 직전 값은 사서가 찾은 것만 붙어 있다. 여기에 없다고 일정이 없는 것은 아니다.", ""]
    else:
        L += [f"- 지표 발표·행사 일정은 매일 갱신되는 일정표(만들어진 때 {cal_at or '받지 못함'})에서 왔고 **향후 한 주쯤만 담는다.** 그 뒤의 중앙은행 회의·지표 발표는 사서의 주간 조사가 올라오기 전까지 싣지 못한다 — 여기에 없다고 일정이 없는 것은 아니다.",
              "- 실적 발표일은 지켜보는 미국 종목만, Finnhub 에서 받는다. 회사가 확정 발표한 날짜가 아닐 수 있다. 지표의 예상치와 직전 값은 아직 싣지 못한다.", ""]

    # 4. 경제·시장 뉴스
    hours = 24 * 7 if week else 36
    since = now - timedelta(hours=hours)
    L += [f"## 경제·시장 뉴스 (최근 {'7일' if week else '36시간'})", ""]
    got = {"KR": [], "US": []}
    for f in c.get("econ_feeds", []):
        try:
            for when, title, link in rss(f["url"]):
                if when is None or when < since or not title:
                    continue
                if f.get("filter") == "kr" and not ECON_KW.search(title):
                    continue
                if f.get("filter") == "en" and (not ECON_EN.search(title) or PROMO_EN.search(title)):
                    continue                              # 남의 추천·권유 글은 싣지 않는다 (사실만)
                got[f["region"]].append((when, f["name"], title, link))
        except Exception:
            missing.append(f"뉴스:{f['name']}")
    order = ["KR", "US"] if market == "KR" else ["US", "KR"]
    caps = {order[0]: 14 if week else 8, order[1]: 10 if week else 5}
    n_news = 0
    for region in order:
        items, seen_t = [], set()
        for when, name, title, link in sorted(got[region], reverse=True):
            k = re.sub(r"\W", "", title)[:18]
            if k in seen_t:
                continue
            seen_t.add(k)
            items.append((when, name, title, link))
        for when, name, title, link in items[:caps[region]]:
            flag = " ⚠" if DECISIVE.search(title) else ""
            L.append(f"- {when:%m-%d %H:%M} [{name}]{flag} {title} — {link}")
            n_news += 1
        if len(items) > caps[region]:
            L.append(f"- ({'한국' if region == 'KR' else '미국'} 쪽 이 밖에 {len(items) - caps[region]}건)")
    if not n_news:
        L.append("- 걸린 뉴스 없음")
    L += ["- 통신사·경제 매체·연준 보도자료의 제목과 주소다. 한국 매체는 경제 낱말이 든 제목만 골랐다. 내용은 주소에서 직접 확인한다.", ""]

    # 5. 종목
    uni = universe(c)
    want = [s for s in uni if week or is_kr(s) == (market == "KR")]
    L += ["## 종목 (지켜보는 목록)", "", "| 종목 | 기준일 | 종가 | 하루 | 닷새 | 한 달 |", "|---|---|---|---|---|---|"]
    for s in want:
        try:
            rows = daily(s)
            L.append(f"| {uni[s][0]} ({s}) | {rows[-1][0][5:]} | {num(rows[-1][1])} | {change(rows, 1, None)} | {change(rows, 5, None)} | {change(rows, 22, None)} |")
        except Exception:
            L.append(f"| {uni[s][0]} ({s}) | — | 받지 못함 | — | — | — |")
            missing.append(s)
    L += ["", "이 목록은 기본 목록에 캐릭터들이 들고 있거나 주문·지켜보기를 걸어 둔 종목을 더한 것이다. 누구의 것인지는 싣지 않는다.",
          "미국 종목의 실적 숫자·가치 지표는 아직 싣지 못한다(다음 실적일은 일정 칸에 있다).", ""]

    # 5-b. 한국 종목 기본 숫자 (주간 회의에만) — 전자공시(DART) 정기보고서에서
    if week:
        L += ["## 한국 종목 기본 숫자", ""]
        try:
            lines, miss = kr_stock_numbers(c, uni, today)
            L += lines
            missing += miss
        except Exception as e:
            L.append(f"- 받지 못함 ({type(e).__name__})")
            missing.append("한국 종목 기본 숫자")
        L += ["- 전자공시(DART)의 가장 최근 정기보고서(연결 기준) 숫자다. \"분기\"는 그 보고서의 석 달치, \"전년비\"는 한 해 전 같은 석 달과 견준 것, \"12개월\"은 최근 누적 + 직전 연간 − 전년 동기 누적.",
              "- 시가총액 = 종가 × 발행 주식 수(보통주, 자기주식 포함). 주가수익배수(P/E) = 시가총액 ÷ 최근 12개월 지배주주 순이익. 주가순자산배수(P/B) = 시가총액 ÷ 지배주주 자본. 계산일 뿐 평가가 아니다.", ""]

    # 6. 종목 이름이 나온 기사
    days = 7 if week else 2
    since_d = (today - timedelta(days=days)).isoformat()
    L += [f"## 종목 이름이 나온 기사 (최근 {days}일)", ""]
    hits = []
    try:
        idx = get(c["sources"]["foreign_index"], timeout=60)
        for a in idx.get("articles", []):
            d = str(a.get("date", ""))[:10]
            if not re.match(r"\d{4}-\d{2}-\d{2}$", d) or d < since_d:
                continue
            text = f"{a.get('title', '')} {a.get('summary', '')} {' '.join(a.get('keywords') or [])}"
            who = [uni[s][0] for s in uni if any(n.lower() in text.lower() for n in uni[s] if "ETF" not in n)]
            if who:
                url = f"https://hong4137.github.io/briefing/archive/{a.get('file')}.html" + (f"#{a['anchor']}" if a.get("anchor") else "")
                hits.append((d, "외신 요약", a.get("title", ""), who, bool(a.get("is_pick")), url))
    except Exception:
        missing.append("외신 기사 색인")
        L.append("- 외신 기사 색인: 받지 못함")
    try:
        mn = get(c["sources"]["must_news"])
        for a in mn.get("articles", [])[:30]:
            who = [uni[s][0] for s in uni if any(n in a.get("title", "") for n in uni[s] if "ETF" not in n)]
            if who:
                hits.append((str(mn.get("updated", ""))[:10], f"국내 {a.get('rank')}위", a.get("title", ""), who, False, a.get("url", "")))
    except Exception:
        missing.append("국내 IT 순위")
        L.append("- 국내 IT 순위: 받지 못함")
    hits.sort(key=lambda h: (h[0], h[4]), reverse=True)
    cap = 25 if week else 12
    for d, src, title, who, pick, url in hits[:cap]:
        flag = " ⚠" if DECISIVE.search(title) else ""
        L.append(f"- {d[5:]} [{src}{' · 주요' if pick else ''}]{flag} {title} — {', '.join(dict.fromkeys(who))}" + (f" — {url}" if url else ""))
    if not hits:
        L.append("- 걸린 기사 없음")
    if len(hits) > cap:
        L.append(f"- (이 밖에 {len(hits) - cap}건)")
    L += ["- \"외신 요약\"은 외신 브리핑이 여러 기사를 묶어 쓴 2차 요약이고, 주소는 그 요약 글이다(원문 주소는 그 글 안에 있다). 기사 자료는 AI·기술 분야에 치우쳐 있다. \"걸린 기사 없음\"은 \"아무 일 없음\"이 아니다.", ""]

    if missing:
        L += ["## 받지 못한 자료", "", "- " + ", ".join(dict.fromkeys(missing)), ""]
    L += ["---", "출처: 야후 일봉(지수·환율·원자재·미국 종목), 다음 금융 일별(한국 종목), NDX Watch(계기판, 미국 2년·10년 금리, MOVE), "
          "Daily UptoDate 경제 일정과 그 원자료, 연준 보도자료·CNBC·연합뉴스(경제 뉴스), 외신 브리핑 기사 색인, Must News."]
    out = rel(c["library_dir"]) / "아침-한-장" / f"{today.isoformat()}-{market}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L) + "\n", encoding="utf-8", newline="\n")
    return out, missing


# ---------- 깨우기 ----------
# 차례(slot) = 시장마다 거래일 하루에 한 번의 개장 전 점검, 그리고 일요일마다의 주간 회의.
# 컴퓨터가 꺼져 있어 제때 못 돈 차례를 잃지 않도록, 캐릭터마다 차례 장부(out/turns-{이름표}.json)를 둔다.
#   woken   깨웠다          skipped 볼 일이 없어 깨우지 않았다
#   missed  놓쳤다          merged  놓친 주간 회의를 다른 점검에 붙였다
_MK = None


def _markets():
    """거래소 엔진의 장 달력(휴장일·서머타임·조기 폐장·시간이 바뀌는 날). 읽기만 한다.
    엔진 저장소의 최신 달력을 받아 out/ 에 두고 쓴다(하루에 한 번 새로 받음) — 이 PC 의 엔진 사본은 감독이 고치지 않아
    뒤처질 수 있다(2026-11-16: 사본에 수능일 시간표 KR_SPECIAL 이 없었다). 받지 못하면 엔진 사본으로 돌아간다."""
    global _MK
    if _MK is not None:
        return _MK
    import importlib.util
    cache = HERE / "out" / "markets_cache.py"
    url = cfg().get("markets_url", "https://raw.githubusercontent.com/hong4137/paper-exchange/main/broker/markets.py")
    try:
        if not cache.exists() or time.time() - cache.stat().st_mtime > 12 * 3600:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=20) as r:
                src = r.read().decode("utf-8")
            if "def sessions" in src:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(src, "utf-8")
        spec = importlib.util.spec_from_file_location("markets_remote", cache)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        m.sessions("KR", datetime.now(KST).date())
        _MK = m
    except Exception:
        p = str(rel(cfg().get("engine_dir", "../모의투자")) / "broker")
        if p not in sys.path:
            sys.path.insert(0, p)
        import markets
        _MK = markets
    return _MK


def slot_of(market, d, c):
    """현지 날짜 d 의 차례: (예정 시각, 개장, 폐장). 쉬는 날이면 None."""
    reg = [x for x in _markets().sessions(market, d) if x[0] == "r"]
    if not reg:
        return None
    _, op, cl = reg[0]
    return op - timedelta(minutes=c.get("due_before_open_min", {}).get(market, 45)), op, cl


def week_due(d, c):
    hh, mm = c.get("week_due", "19:41").split(":")
    return datetime(d.year, d.month, d.day, int(hh), int(mm), tzinfo=KST)


def turns_file(ch):
    return HERE / "out" / f"turns-{ch['id']}.json"


def load_turns(ch, now):
    f = turns_file(ch)
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    return {"start": now.isoformat(timespec="minutes"), "slots": {}}       # 장부를 처음 만든 때부터만 센다


def save_turns(ch, t):
    f = turns_file(ch)
    f.parent.mkdir(exist_ok=True)
    f.write_text(json.dumps(t, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")


def due_slots(market, t, now, c):
    """장부에 없고 예정 시각이 된 차례들 [(열쇠, 예정, 개장, 폐장)] — 오래된 것부터."""
    start = datetime.fromisoformat(t["start"])
    grace = timedelta(minutes=10)                        # 예약이 몇 분 일찍 도는 것은 제때로 본다
    out, d = [], start.date() - timedelta(days=1)
    while d <= now.date():
        key = f"{market}:{d.isoformat()}"
        if market == "WEEK":
            if d.weekday() == 6:
                due = week_due(d, c)
                end = datetime(d.year, d.month, d.day, 23, 59, tzinfo=KST)     # 그날 자정까지를 '제때'로
                if start <= due <= now + grace and key not in t["slots"]:
                    out.append((key, due, due, end))
        else:
            s = slot_of(market, d, c)
            if s and start <= s[0] <= now + grace and key not in t["slots"]:
                out.append((key, *s))
        d += timedelta(days=1)
    return out


def next_slot(market, now, c):
    d = now.date()
    for _ in range(12):
        s = slot_of(market, d, c)
        if s and s[0] > now:
            return (f"{market}:{d.isoformat()}", *s)
        d += timedelta(days=1)
    return None


def reasons_to_wake(c, ch, market, st):
    """이 시장에 볼 일이 있는가 (시각은 따지지 않는다)."""
    if st is None:
        return ["거래소 자료를 읽지 못했다 — 직접 확인이 필요하다"]
    if market == "WEEK":
        return ["주간 회의"]
    in_mk = lambda sym: is_kr(sym) == (market == "KR")
    why = []
    pos = [p for p in st.get("positions", []) if in_mk(p["symbol"])]
    pend = [o for o in st.get("orders", []) if o.get("status") == "pending" and in_mk(o["symbol"])]
    if pos:
        why.append(f"이 시장에 보유 종목 {len(pos)}개")
    if pend:
        why.append(f"이 시장에 기다리는 주문 {len(pend)}건")
    if st.get("watch_hits"):
        why.append(f"지켜보던 조건에 닿음 {len(st['watch_hits'])}건")
    if [w for w in (st.get("watches") or []) if w.get("symbol") and in_mk(w["symbol"])]:
        why.append("이 시장에 지켜보는 조건이 있음")
    cash = (st.get("summary") or {}).get("cash_pct")
    if cash is not None and cash > c.get("entering_cash_pct", 30):
        why.append(f"아직 들어가는 중 (현금 {cash}%)")
    note = rel(ch["journal"]) / "운용자" / "다음-점검.md"
    if note.exists() and re.sub(r"^#.*$|^>.*$", "", note.read_text(encoding="utf-8"), flags=re.M).strip():
        why.append("스스로 적어 둔 '다음 점검 때 할 일'이 있음")
    return why


def iso(t):
    return t.isoformat(timespec="minutes")[:16]


def plan_turn(c, ch, market, now, st):
    """이번 실행에서 이 캐릭터에게 할 일을 정한다.
    반환: (깨울지, 까닭 목록, 상황, 이번 차례의 예정 시각, 늦은 분, 고친 장부, 설명)"""
    t = load_turns(ch, now)

    def mark(key, due, status, note=""):
        v = {"due": iso(due), "status": status, "at": iso(now), "note": note}
        if status == "missed":
            v["reported"] = False
        t["slots"][key] = v

    pend = due_slots(market, t, now, c)
    if not pend:
        return False, [], "none", None, None, t, "예정 시각이 된 차례가 없다"
    for key, due, op, cl in pend[:-1]:                    # 가장 최근 것만 다루고, 그 앞의 밀린 것은 놓친 것
        mark(key, due, "missed", "컴퓨터가 꺼져 있어 돌지 못했다")
    key, due, op, cl = pend[-1]
    why = reasons_to_wake(c, ch, market, st)
    late = round((now - due).total_seconds() / 60)

    if market == "WEEK":
        if now <= cl:                                     # 일요일 안이면 늦어도 그대로 한다
            mark(key, due, "woken", f"{late}분 늦게" if late > 15 else "")
            return True, why, "week", due, late, t, ""
        mark(key, due, "missed", "일요일 안에 돌지 못했다 — 다음 점검에 붙인다")
        return False, [], "week-missed", None, None, t, "주간 회의를 놓쳤다 — 다음 개장 전 점검에 붙인다"

    if now < cl:                                          # 개장 전이거나 장중: 이 차례를 지금 한다
        if not why:
            mark(key, due, "skipped", "볼 일이 없다")
            return False, [], "skipped", due, late, t, "볼 일이 없어 깨우지 않는다"
        mark(key, due, "woken", ("장중 " if now >= op else "") + (f"{late}분 늦게" if late > 15 else ""))
        return True, why, "open" if now >= op else "pre", due, late, t, ""

    # 폐장 뒤: 이 차례는 놓쳤다. 다음 차례가 곧 오면 거기에 합치고, 멀면 다음 차례를 앞당겨 지금 한다.
    mark(key, due, "missed", "장이 끝난 뒤에야 돌았다")
    nxt = next_slot(market, now, c)
    soon = c.get("merge_if_next_within_min", 180)
    if not nxt or (nxt[1] - now).total_seconds() / 60 <= soon or not why:
        return False, [], "missed", None, None, t, "놓친 차례 — 다음 차례에 알린다" + ("" if why else " (지금 볼 일은 없다)")
    nkey, ndue, nop, ncl = nxt
    mark(nkey, ndue, "woken", "앞당겨 했다 (놓친 차례 뒤 늦게 켜짐)")
    return True, why, "early", ndue, round((now - ndue).total_seconds() / 60), t, ""


def collect_missed(t):
    """아직 선수에게 알리지 않은 놓친 차례들. 알린 것으로 표시한다. 놓친 주간 회의는 이번 점검에 붙인다."""
    out = []
    for key, v in sorted(t["slots"].items(), key=lambda kv: kv[1]["due"]):
        if v["status"] == "missed" and not v.get("reported"):
            out.append((key.split(":")[0], v["due"]))
            v["reported"] = True
            if key.startswith("WEEK:"):
                v["status"] = "merged"
    return out


def cmd_brief(a):
    out, missing = make_brief(a.market, datetime.now(KST))
    print(out)
    if missing:
        print("받지 못한 자료:", ", ".join(dict.fromkeys(missing)))


def cmd_wake(a):
    c = cfg()
    now = datetime.fromisoformat(a.now).astimezone(KST) if a.now else datetime.now(KST)
    pull(c)
    res = {"market": a.market, "label": LABEL[a.market], "ready": c.get("ready", False), "brief": None, "missing": [], "characters": []}
    (HERE / "out").mkdir(exist_ok=True)
    plans = []
    for ch in c["characters"]:
        st = load_state(c, ch)
        if not c.get("ready", False):
            res["characters"].append({"id": ch["id"], "name": ch["name"], "session": ch["session"], "wake": False,
                                      "reasons": [], "held": "감독 설정이 '준비 중'이라 깨우지 않는다"})
            continue
        plans.append((ch, st, *plan_turn(c, ch, a.market, now, st)))
    brief = ""
    if any(p[2] for p in plans):
        got = None if a.dry and a.now else cloud_brief(a.market, now, c)
        out, missing = got if got else make_brief(a.market, now)
        res["brief_from"] = "저장소" if got else "이 PC"
        brief = out.read_text(encoding="utf-8")
        res["brief"], res["missing"] = str(out), list(dict.fromkeys(missing))
    for ch, st, wake, why, situation, due, late, t, note in plans:
        item = {"id": ch["id"], "name": ch["name"], "session": ch["session"], "wake": wake, "reasons": why,
                "situation": situation, "note": note}
        if wake:
            missed = collect_missed(t)
            mkt = (st or {}).get("markets") or {}
            parts = []
            for k, n in (("KR", "한국장"), ("US", "미국장")):
                if k in mkt:
                    nx = mkt[k].get("next_open")
                    parts.append(f"{n} {mkt[k]['label']}" + (f" (다음 개장 {nx[5:16].replace('T', ' ')})" if nx else ""))
            head = [f"[감독] {LABEL[a.market]} 차례입니다 ({now:%m-%d %H:%M}).", "",
                    f"- 이 차례의 예정 시각: {iso(due)}" + (f" — **{late}분 늦었습니다** (컴퓨터가 그때 꺼져 있었습니다)" if late > 15 else ""),
                    f"- 지금 시장: {' · '.join(parts) or '받지 못함'}",
                    "- 깨운 까닭: " + " / ".join(why)]
            day_note = (c.get("day_notes") or {}).get(now.date().isoformat())
            if day_note:
                head.append(f"- **오늘의 알림**: {day_note}")
            if situation == "open":
                head.append("- **장이 이미 열려 있습니다.** 개장 전 점검을 놓치고 늦게 깨운 것입니다. 지금 낸 시장가 주문은 다음 1분에 체결됩니다. 지금 움직일지, 이번 차례를 넘길지는 스스로 정합니다.")
            if situation == "early":
                head.append("- **직전 차례를 놓쳤고, 다음 차례를 앞당겨 지금 합니다.** 장은 닫혀 있습니다. 지금 낸 주문은 다음 개장 첫 1분(시장가) 또는 그날 종가(종가 주문)에 체결됩니다. 다음 정규 시각에는 다시 깨우지 않습니다.")
            if missed:
                head.append("- 놓친 차례 (컴퓨터가 꺼져 있었습니다):")
                head += [f"    · {LABEL[m]} {d}" for m, d in missed]
                if any(m == "WEEK" for m, _ in missed):
                    head.append("- **주간 회의를 놓쳤습니다. 이번 점검에서 주간 회의도 함께 합니다** (준비서 5절의 주간 회의 순서, 끝에 주간 보고).")
            head.append("")
            if a.market == "WEEK":
                head += ["오늘은 주간 회의입니다. `운용자/운용-준비서.md` 5절의 주간 회의 순서대로 읽고, 결정이 있으면 내고, 끝에 주간 보고(`report --kind weekly`)를 올립니다."
                         + (" 이번 달 첫 일요일이므로 월간 되돌아보기도 함께 합니다(`report --kind monthly`, 교훈 고치기)." if now.day <= 7 else ""), ""]
            else:
                head += ["`python journal.py sync` 로 지난 점검 이후 일어난 일을 받고, 아래 한 장을 읽습니다. 할 일이 없으면 일기 세 줄(`memo`)로 끝냅니다. 새 생각은 주간 회의에서 다룹니다.", ""]
            head += ["**끝낼 때 차례를 일지에 남깁니다.**",
                     f"- 이번 차례: `python journal.py turn --market {a.market} --due {iso(due)} --outcome acted` (주문을 냈으면 acted, 보기만 했으면 passed)"]
            for m, d in missed:
                if m == "WEEK":                              # 놓친 주간 회의를 이번 점검에 붙였다 — missed 가 아니라 merged
                    head.append(f"- 붙여서 한 주간 회의: `python journal.py turn --market WEEK --due {d} --outcome merged --why \"{now:%m-%d} {LABEL[a.market]}에 붙임\"` (끝까지 못 했으면 merged 대신 missed)")
                else:
                    head.append(f"- 놓친 차례: `python journal.py turn --market {m} --due {d} --outcome missed`")
            head += ["- 그다음 `python journal.py save`.", "",
                     "아래는 모든 캐릭터가 똑같이 받는 공통 자료입니다. 사실만 있고 판단은 없습니다.", "", "---", ""]
            f = HERE / "out" / f"{now:%Y%m%d-%H%M}-{a.market}-{ch['id']}.md"
            if not a.dry:                                    # 시험(--dry)에서는 보낼 글도 남기지 않는다
                f.write_text("\n".join(head) + brief, encoding="utf-8", newline="\n")
            item["message_file"] = str(f)
            item["due"], item["late_min"], item["missed"] = iso(due), late, [f"{m} {d}" for m, d in missed]
        if not a.dry:
            save_turns(ch, t)
        res["characters"].append(item)
    print(json.dumps(res, ensure_ascii=False, indent=1))


def cmd_turns(a):
    c = cfg()
    for ch in c["characters"]:
        f = turns_file(ch)
        print(f"[{ch['name']}]", "(장부 없음)" if not f.exists() else "")
        if f.exists():
            t = json.loads(f.read_text(encoding="utf-8"))
            for key, v in sorted(t["slots"].items(), key=lambda kv: kv[1]["due"])[-20:]:
                print(f"  {v['due']}  {key:16s} {v['status']:8s} {v.get('note', '')}")


def cmd_publish(a):
    """이 PC 에서만 만들 수 있는 공통 자료(사서의 주간 조사, 한국 종목 숫자)를 저장소로 보낸다 → economic_briefing 의 desk/library-in/.
    저장소의 한 장 만들기가 그것을 읽는다. 사서가 조사를 마칠 때와 주간 회의 한 장을 만든 뒤에 돌린다."""
    c = cfg()
    repo = HERE / "out" / "economic_briefing"
    url = c.get("publish_repo", "https://hong4137@github.com/hong4137/economic_briefing.git")
    run = lambda *x: subprocess.run(["git", "-C", str(repo), *x], capture_output=True, text=True, encoding="utf-8", timeout=180)
    if not (repo / ".git").exists():
        repo.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(["git", "clone", "-q", "--depth", "1", url, str(repo)], capture_output=True, text=True, timeout=300)
        if r.returncode:
            sys.exit("저장소를 받지 못했습니다: " + r.stderr[-200:])
    run("fetch", "-q", "--depth", "1", "origin", "main"); run("reset", "-q", "--hard", "origin/main")
    dst = repo / "desk" / "library-in"
    lib = rel(c["library_dir"])
    sent = []
    for sub, pick in (("조사", ["latest.json"]), ("종목-숫자", [f.name for f in sorted((lib / "종목-숫자").glob("20??-??-??.json"))[-1:]])):
        for name in pick:
            src = lib / sub / name
            if src.exists():
                (dst / sub).mkdir(parents=True, exist_ok=True)
                (dst / sub / name).write_bytes(src.read_bytes()); sent.append(f"{sub}/{name}")
    run("add", "-A", "desk/library-in")
    if not run("status", "--porcelain").stdout.strip():
        print("바뀐 것이 없습니다:", ", ".join(sent)); return
    r = run("-c", "user.name=hong4137", "-c", "user.email=hong4137@users.noreply.github.com", "commit", "-q", "-m", "공통 서고: 사서 조사·종목 숫자 갱신")
    r = run("push", "-q", "origin", "HEAD:main")
    print("보냄:" if r.returncode == 0 else "보내지 못함: " + r.stderr[-200:], ", ".join(sent))


def main():
    p = argparse.ArgumentParser(description="감독 겸 사서")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn in [("brief", cmd_brief), ("wake", cmd_wake)]:
        s = sub.add_parser(name)
        s.add_argument("--market", required=True, choices=["KR", "US", "WEEK"])
        if name == "wake":
            s.add_argument("--now", help="시험용: 이 시각인 것처럼 (2026-11-16T08:04+09:00)")
            s.add_argument("--dry", action="store_true", help="시험용: 차례 장부에 적지 않는다")
        s.set_defaults(f=fn)
    sub.add_parser("turns").set_defaults(f=cmd_turns)
    sub.add_parser("research-check").set_defaults(f=cmd_research_check)
    sub.add_parser("publish").set_defaults(f=cmd_publish)
    a = p.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
