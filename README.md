# economic_briefing

경제 브리핑의 원천 수집기. 금리·물가·고용·환율·원자재·중앙은행·채권·신용·은행·부동산·에너지와 국제 정세를
네 층(결정 원문 · 숫자 · 가격 · 매체)으로 받아 한 벌의 JSON 으로 남긴다. AI 를 쓰지 않는다.

- `collect.py` — 수집기. `sources.json` 의 원천을 받아 `raw.json`(최근 36시간)과 `store.json`(14일 보관함)을 만든다.
- `원천-목록.md` — 원천별 시험 기록.
- 결과: `data` 가지 — https://raw.githubusercontent.com/hong4137/economic_briefing/data/raw.json
- 실행: 밖에서 정시에 `workflow_dispatch` 로 깨운다. GitHub 의 예약 실행은 안전망이다.
- `probe/` — 수집 위치를 정할 때 쓴 접속 시험.
