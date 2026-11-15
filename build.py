"""경제 브리핑 조판 — editions/날짜.json(편집본)과 data/raw.json(수집된 가격)을 합쳐 docs/ 를 만든다. AI 를 쓰지 않는다.

    python build.py                 가장 최근 편집본을 조판 (docs/index.html, docs/archive/날짜.html, docs/archive/index.json)
    python build.py --check 파일    편집본의 모양만 검사 (발행 전에 돌린다)

편집본은 발행 루틴(Claude)이 `발행-지침.md` 를 따라 쓴다.
"""
import html, json, re, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
e = html.escape
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def check(E):
    """편집본 모양 검사. 문제를 글로 돌려준다(없으면 빈 목록)."""
    bad = []
    need = lambda cond, msg: bad.append(msg) if not cond else None
    for k in ("date", "date_label", "basis", "stamp", "verdict", "verdict_sub", "chain", "top", "geo", "korea", "decisions", "watch"):
        need(k in E, f"'{k}' 가 없다")
    if bad:
        return bad
    need(re.fullmatch(r"\d{4}-\d{2}-\d{2}", E["date"] or ""), "date 는 YYYY-MM-DD")
    need(10 <= len(E["verdict"]) <= 34, f"verdict 는 10~34자 ({len(E['verdict'])}자)")
    need("\n" not in E["verdict"], "verdict 에 줄바꿈을 넣지 않는다")
    need(3 <= len(E["chain"]) <= 6, "chain 은 3~6칸")
    need(len(E["top"]) == 3, "top 은 정확히 3건")
    urls = []
    for i, s in enumerate(E["top"]):
        for k in ("kicker", "path", "title", "body", "why", "src", "nums"):
            need(k in s, f"top[{i}] 에 '{k}' 가 없다")
        if all(k in s for k in ("title", "src", "body")):
            need(24 <= len(s["title"]) <= 64, f"top[{i}] 제목은 24~64자 ({len(s['title'])}자)")
            need("\n" not in s["title"], f"top[{i}] 제목에 줄바꿈을 넣지 않는다")
            need(len(s["src"]) >= 1, f"top[{i}] 출처가 없다")
            urls += [x.get("url", "") for x in s["src"]]
    for sec in ("geo", "korea"):
        need(3 <= len(E[sec].get("items", [])) <= 6, f"{sec} 는 3~6건")
        urls += [x.get("url", "") for x in E[sec].get("items", [])]
    need(3 <= len(E["decisions"]) <= 8, "decisions 는 3~8건")
    urls += [x.get("url", "") for x in E["decisions"]]
    need(3 <= len(E["watch"]) <= 7, "watch 는 3~7건")
    for u in urls:
        need(u.startswith("http"), f"주소가 아니다: {u[:60]}")
    text = json.dumps(E, ensure_ascii=False)
    for w in ("매수 추천", "매도 추천", "사야 한다", "팔아야 한다", "지금 사라", "지금 팔라", "목표주가"):
        need(w not in text, f"투자 권유로 읽히는 말이 있다: '{w}'")
    return bad


if len(sys.argv) >= 3 and sys.argv[1] == "--check":
    problems = check(json.loads(Path(sys.argv[2]).read_text("utf-8")))
    print("\n".join("X " + p for p in problems) if problems else "검사 통과")
    sys.exit(1 if problems else 0)

eds = sorted((HERE / "editions").glob("20??-??-??.json"))
if not eds:
    sys.exit("editions/ 에 편집본이 없다")
E = json.loads(eds[-1].read_text("utf-8"))
problems = check(E)
if problems:
    sys.exit("편집본 검사 실패:\n" + "\n".join(problems))
raw = json.loads((HERE / "data" / "raw.json").read_text("utf-8"))
P = {p["name"]: p for p in raw["prices"] if p["ok"]}

GROUPS = ["주식", "금리", "신용", "환율", "원자재"]


def chg(p):
    v, pr, w, m = p["value"], p["prev"], p["week_ago"], p["month_ago"]
    if p["unit"] == "pct":
        f = lambda a, b: (a / b - 1) * 100
        return f(v, pr), f(v, w), f(v, m), "%"
    k = 100 if p["unit"] == "bp" else 1
    return (v - pr) * k, (v - w) * k, (v - m) * k, ("bp" if p["unit"] == "bp" else "")


def fmt_val(p):
    v = p["value"]
    if p["unit"] == "bp":
        return f"{v:.2f}%"
    return f"{v:,.2f}" if v < 10000 else f"{v:,.0f}"


def cls(x):
    return "up" if x > 0.004 else ("dn" if x < -0.004 else "fl")


def delta(x, unit, short=False):
    a = abs(x)
    s = f"{a:.0f}" if unit == "bp" else (f"{a:.1f}" if short else f"{a:.2f}")
    return ("▲" if x > 0.004 else ("▼" if x < -0.004 else "–")) + " " + s + unit


def tile(p):
    d, w, m, u = chg(p)
    bar = max(-1, min(1, m / (60 if u == "bp" else (15 if u == "%" else 8))))
    return (f'<div class="tile {cls(d)}"><div class="tn">{e(p["name"])}</div><div class="tv">{fmt_val(p)}</div>'
            f'<div class="td">{delta(d, u)}</div>'
            f'<div class="tw"><span class="{cls(w)}">주 {delta(w, u, True)}</span><span class="{cls(m)}">월 {delta(m, u, True)}</span></div>'
            f'<div class="tb"><i class="{cls(m)}" style="width:{abs(bar) * 50:.0f}%;{"left:50%" if bar > 0 else f"right:50%"}"></i></div></div>')


def board():
    out = []
    for g in GROUPS:
        ts = "".join(tile(p) for p in raw["prices"] if p["ok"] and p["group"] == g)
        out.append(f'<div class="bgroup"><h3>{g}</h3><div class="tiles">{ts}</div></div>')
    return "".join(out)


def mini(names):
    out = []
    for n in names:
        p = P.get(n)
        if p:
            d, w, m, u = chg(p)
            out.append(f'<span class="mini {cls(w)}"><b>{e(n)}</b> {fmt_val(p)} <em>주 {delta(w, u, True)}</em></span>')
    return "".join(out)


def srcs(lst):
    return " · ".join(f'<a href="{e(u)}" target="_blank" rel="noopener">{e(n)}</a>' for n, u in lst)


top = "".join(
    f'<article class="story s{i}"><div class="num">{i + 1}</div><div class="kick">{e(s["kicker"])}</div>'
    f'<h2>{e(s["title"])}</h2><div class="path">{"".join(f"<span>{e(x)}</span>" for x in s["path"])}</div>'
    f'<p>{e(s["body"])}</p><p class="why"><b>돈의 눈으로 보면</b>{e(s["why"])}</p>'
    f'<div class="minis">{mini(s["nums"])}</div><div class="src">출처 {srcs([(x["name"], x["url"]) for x in s["src"]])}</div></article>'
    for i, s in enumerate(E["top"]))
link = lambda x: f'<a href="{e(x["url"])}" target="_blank" rel="noopener">{e(x["title"])}</a>'
geo = "".join(f'<li><span class="tag">{e(x["tag"])}</span><h4>{link(x)}</h4><p>{e(x["body"])} <span class="by">{e(x["by"])}</span></p></li>' for x in E["geo"]["items"])
dec = "".join(f'<li><span class="who">{e(x["who"])}</span><div>{link(x)}{("<p>" + e(x["body"]) + "</p>") if x.get("body") else ""}</div></li>' for x in E["decisions"])
kor = "".join(f'<li><h4>{link(x)}</h4><p>{e(x["body"])} <span class="by">{e(x["by"])}</span></p></li>' for x in E["korea"]["items"])
chain = "".join(f'<span class="c{i}">{e(c)}</span>' for i, c in enumerate(E["chain"]))
watch = "".join(f"<li>{e(w)}</li>" for w in E["watch"])

arch = HERE / "docs" / "archive"
arch.mkdir(parents=True, exist_ok=True)
idx = [{"date": json.loads(p.read_text("utf-8"))["date"], "verdict": json.loads(p.read_text("utf-8"))["verdict"]} for p in reversed(eds)]
(arch / "index.json").write_text(json.dumps(idx, ensure_ascii=False, indent=1), "utf-8")
past = "".join(f'<a href="archive/{x["date"]}.html">{x["date"][5:].replace("-", ".")} · {e(x["verdict"])}</a>' for x in idx[1:8])

page = (HERE / "template.html").read_text("utf-8")
for k, v in {"{{DATE}}": e(E["date_label"]), "{{BASIS}}": e(E["basis"]), "{{VERDICT}}": e(E["verdict"]), "{{VERDICT_SUB}}": e(E["verdict_sub"]),
             "{{STAMP_DAY}}": e(E["stamp"]["day"]), "{{STAMP_SUB}}": e(E["stamp"]["sub"]),
             "{{CHAIN}}": chain, "{{BOARD}}": board(), "{{TOP}}": top, "{{GEO_LEAD}}": e(E["geo"]["lead"]), "{{GEO}}": geo, "{{DEC}}": dec,
             "{{KOR_LEAD}}": e(E["korea"]["lead"]), "{{KOR}}": kor, "{{WATCH}}": watch,
             "{{NSRC}}": str(raw["counts"]["ok"]), "{{NITEMS}}": str(raw["counts"].get("stored_total", raw["counts"]["items"])),
             "{{PRICE_AT}}": e(raw["generated_at"][:16].replace("T", " ")),
             "{{KOSPI}}": mini(E["korea"].get("nums", [])), "{{OIL}}": mini(E["geo"].get("nums", []))}.items():
    page = page.replace(k, v)
(arch / f'{E["date"]}.html').write_text(page.replace("{{PAST}}", "").replace('href="archive/', 'href="').replace("{{BASE}}", "../"), "utf-8")
(HERE / "docs" / "index.html").write_text(page.replace("{{PAST}}", past).replace("{{BASE}}", ""), "utf-8")
print("조판:", E["date"], "→ docs/index.html,", f'docs/archive/{E["date"]}.html', "· 지난 판", len(idx) - 1)
