# economic_briefing

경제 브리핑의 원천 수집기. 금리·물가·고용·환율·원자재·중앙은행·채권·신용·은행·부동산·에너지와 국제 정세를
네 층(결정 원문 · 숫자 · 가격 · 매체)으로 받아 한 벌의 JSON 으로 남긴다. AI 를 쓰지 않는다.

- `collect.py` — 수집기. `sources.json` 의 원천을 받아 `raw.json`(최근 36시간)과 `store.json`(14일 보관함)을 만든다.
- `raw.json` 의 `kr` 칸 (2026-11-26 추가, 수집 때마다 새로 받는다):
  - `kr.flow.markets.KOSPI / KOSDAQ` — 시장 전체 투자자별 순매수(억 원). `rows` 는 최근 130 거래일(최신이 먼저), `sums` 는 5·20·60 거래일 합. 원천: 네이버 증권(KRX 정규시장).
  - `kr.macro.series` — 한국은행 ECOS 월별: 소비자물가 `cpi`, 근원 `cpi_core`(농산물·석유류 제외)·`cpi_core_oecd`(식료품·에너지 제외), 수출 `exports`·수입 `imports`(통관, 천 달러), `trade_balance`. 각 13개월, `yoy_pct` 는 전년 같은 달 대비 %.
  - 받지 못하면 지난번 값을 그대로 두고 `ok: false, carried: true` 로 표시한다.
- `원천-목록.md` — 원천별 시험 기록.
- 결과: `data` 가지 — https://raw.githubusercontent.com/hong4137/economic_briefing/data/raw.json
- 실행: 밖에서 정시에 `workflow_dispatch` 로 깨운다. GitHub 의 예약 실행은 안전망이다.
- `probe/` — 수집 위치를 정할 때 쓴 접속 시험.
