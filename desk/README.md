# desk — 아침 한 장 (모의투자 공통 자료)

모의투자의 모든 선수가 똑같이 읽는 사실 모음이다. AI 를 쓰지 않는다. 판단·추천은 없다.
- `desk.py brief --market KR|US|WEEK` 가 한 장을 만든다. 원본은 감독의 PC 에 있고 여기 것은 사본이다(감독이 올린다).
- `brief` 워크플로가 정시에 만들어 `data` 가지의 `library/아침-한-장/날짜-시장.md` 에 둔다. 밖(GAS 예약)에서 깨운다.
- `library-in/` — 감독의 PC 에서만 만들 수 있는 자료(사서의 주간 조사, 한국 종목 숫자). `desk.py publish` 가 올린다.

- 열쇠: `FRED_API_KEY`·`ECOS_API_KEY`·`FINNHUB_API_KEY` 는 저장소 금고(Secrets)에서 온다. 없으면 그 줄만 "받지 못함"이 된다.
