"""접속 시험 (GitHub Actions 또는 아무 곳에서나): probe/urls.json 의 주소가 열리는지 표로 찍는다."""
import json, re, time, urllib.request, concurrent.futures as cf
from pathlib import Path
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36", "Accept": "application/rss+xml, application/xml, text/xml, application/json, */*", "Accept-Language": "en-US,en;q=0.9,ko;q=0.8"}
def probe(u):
    t = time.time()
    try:
        with urllib.request.urlopen(urllib.request.Request(u["url"], headers=UA), timeout=25) as r:
            st = r.status; body = r.read(600000).decode("utf-8", "replace")
        return {**u, "status": st, "items": len(re.findall(r"<item[\s>]|<entry[\s>]", body)), "json": body.lstrip()[:1] in "[{", "ms": int((time.time() - t) * 1000)}
    except Exception as e:
        return {**u, "status": getattr(e, "code", 0), "error": f"{type(e).__name__}: {str(e)[:60]}", "ms": int((time.time() - t) * 1000)}
U = json.loads(Path(__file__).with_name("urls.json").read_text("utf-8"))
with cf.ThreadPoolExecutor(12) as ex: out = list(ex.map(probe, U))
ok = lambda o: o["status"] == 200 and (o.get("items") or o.get("json"))
for o in out: print(("O" if ok(o) else "X"), o["layer"], "|", o["name"], "|", o["status"], "|", o.get("items", 0), "|", o.get("error", ""))
print("열림", sum(1 for o in out if ok(o)), "/", len(out))
Path(__file__).with_name("result.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), "utf-8")
