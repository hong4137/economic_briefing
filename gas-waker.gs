/**
 * GitHub 수집기 깨우기 — GitHub 의 예약 실행이 몇 시간씩 밀리기 때문에, 정시 실행을 이 스크립트가 맡는다.
 * 이 스크립트는 수집하지 않는다. 정해진 시각에 "지금 돌려라"고 부르기만 한다.
 *
 * 처음 한 번:
 *   1) 왼쪽 톱니바퀴(프로젝트 설정) → 스크립트 속성 → 속성 추가:  GH_TOKEN = (GitHub 토큰)
 *      토큰은 github.com → Settings → Developer settings → Fine-grained tokens 에서 만든다.
 *      Repository access: Only select repositories → 깨울 저장소만 / Permissions: Actions = Read and write (그것 하나만)
 *      ※ 토큰을 이 코드나 시트 칸에 적지 않는다. 스크립트 속성에만 둔다.
 *   2) 프로젝트 설정에서 시간대가 (GMT+09:00) 서울인지 확인한다.
 *   3) setup() 을 한 번 실행한다 (예약이 만들어진다).  wakeNow() 로 바로 한 번 깨워 볼 수 있다.
 *      TARGETS 를 고치면 setup() 을 다시 한 번 실행한다 (예약 시각이 바뀐다).
 *
 * 2026-11-17: 모의 거래소(hong4137/paper-exchange)를 더했다. 토큰에 그 저장소도 넣고 Actions 읽기·쓰기를 준다.
 *   거래소만 시험하려면 wakeExchangeNow() (정기 실행 한 번 — 장전 수집은 그 시간에만 의미가 있어 부르지 않는다).
 */
var TARGETS = [
  // 깨울 곳.  hours: 실행할 시각(서울, 0~23).  minute: 그 시각의 몇 분쯤(앞뒤 15분 안에서 돈다).
  { name: "경제 브리핑 수집", repo: "hong4137/economic_briefing", workflow: "collect.yml", ref: "main",
    hours: [0, 3, 5, 6, 9, 12, 15, 18, 21], minute: 20 },
  // 미국장 마감은 서울 시각 05:00(서머타임) 또는 06:00(겨울)이라 05:20 과 06:20 을 둘 다 넣었다.

  // 모의 거래소 정기 실행 — 시세 갱신·체결·화면 자료. 미국 마감 뒤(06·07시), 한국 마감 뒤(16시), 장후 마감 뒤(20시),
  // 선수의 미국장 전 점검 전(21·22시). 07시는 한국장 전 점검(08시쯤) 전에 자료를 새로 하려는 것. GitHub 예약(매시)은 백업으로 남아 있다.
  { name: "모의 거래소 정기 실행", repo: "hong4137/paper-exchange", workflow: "daily.yml", ref: "main",
    hours: [6, 7, 16, 20, 21, 22], minute: 20 },
  // 한국 장전(넥스트레이드 08:00~08:50) 시세는 그 시간에 돌아야만 모인다. 평일만. 일찍 깨워도 거래소 쪽이 08:52 까지 기다린다.
  { name: "모의 거래소 장전 수집", repo: "hong4137/paper-exchange", workflow: "premarket.yml", ref: "main",
    hours: [7], minute: 50, weekdays: [1, 2, 3, 4, 5] }
  ,
  // 아침 한 장 — 모의투자의 모든 선수가 똑같이 읽는 사실 모음(2026-11-17). 한국장 전 점검(08:04)과 미국장 전 점검(22:50, 서머타임에는 21:53) 전에 만든다.
  // 시장(KR·US·WEEK)은 워크플로가 깨어난 시각으로 정한다.
  { name: "아침 한 장 (평일)", repo: "hong4137/economic_briefing", workflow: "brief.yml", ref: "main",
    hours: [7, 21, 22], minute: 20, weekdays: [1, 2, 3, 4, 5] },
  { name: "아침 한 장 (주간 회의)", repo: "hong4137/economic_briefing", workflow: "brief.yml", ref: "main",
    hours: [19], minute: 15, weekdays: [7] }
  // 다른 저장소도 정시에 돌리고 싶으면 여기에 한 줄 더한다. 그 저장소의 워크플로에 workflow_dispatch 가 있어야 하고, 토큰에 그 저장소를 추가해야 한다.
];

function setup() {
  ScriptApp.getProjectTriggers().forEach(function (t) { if (t.getHandlerFunction() === "tick") ScriptApp.deleteTrigger(t); });
  // 시각마다 예약을 하나씩 만든다 (매시간 예약은 몇 분에 돌지 정할 수 없어서). 한 스크립트의 예약은 20개까지다.
  var seen = {};
  TARGETS.forEach(function (t) { t.hours.forEach(function (h) { var k = h + ":" + t.minute; if (!seen[k]) seen[k] = { h: h, m: t.minute }; }); });
  var n = 0;
  Object.keys(seen).forEach(function (k) {
    ScriptApp.newTrigger("tick").timeBased().atHour(seen[k].h).nearMinute(seen[k].m).everyDays(1).inTimezone("Asia/Seoul").create(); n++;
  });
  log_("예약 만듦", n + "개 — 정한 시각의 앞뒤 15분 안에서 돈다");
}

/** 예약된 시각마다 돈다. 지금이 TARGETS 의 어느 시각(시:분)의 앞뒤 20분 안이면 그 대상을 깨운다.
 *  같은 대상의 같은 시각은 하루에 한 번만 깨운다. (같은 시(hour)에 시각이 둘 있어도 — 예: 07:20 과 07:50 — 서로 섞이지 않는다.) */
function tick() {
  var now = new Date();
  var hm = Utilities.formatDate(now, "Asia/Seoul", "H:m").split(":");
  var nowMin = Number(hm[0]) * 60 + Number(hm[1]);
  var day = Utilities.formatDate(now, "Asia/Seoul", "yyyy-MM-dd");
  var weekday = Number(Utilities.formatDate(now, "Asia/Seoul", "u"));      // 1=월 … 7=일
  var props = PropertiesService.getScriptProperties();
  TARGETS.forEach(function (t) {
    if (t.weekdays && t.weekdays.indexOf(weekday) < 0) return;
    t.hours.forEach(function (h) {
      var diff = nowMin - (h * 60 + t.minute);
      if (Math.abs(diff) > 20) return;
      var key = "last_" + t.repo + "_" + t.workflow + "_" + h + ":" + t.minute;
      if (props.getProperty(key) === day) return;
      if (wake_(t)) props.setProperty(key, day);
    });
  });
}

function wakeNow() { TARGETS.forEach(wake_); }

function wakeExchangeNow() { TARGETS.filter(function (t) { return t.workflow === "daily.yml"; }).forEach(wake_); }

function wake_(t) {
  var token = PropertiesService.getScriptProperties().getProperty("GH_TOKEN");
  if (!token) { log_(t.name, "실패 — 스크립트 속성에 GH_TOKEN 이 없다"); return false; }
  var url = "https://api.github.com/repos/" + t.repo + "/actions/workflows/" + t.workflow + "/dispatches";
  var code = 0, body = "";
  for (var i = 0; i < 3; i++) {                       // 실패하면 두 번까지 다시
    try {
      var r = UrlFetchApp.fetch(url, { method: "post", contentType: "application/json", muteHttpExceptions: true,
        headers: { Authorization: "Bearer " + token, Accept: "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28" },
        payload: JSON.stringify({ ref: t.ref || "main" }) });
      code = r.getResponseCode(); body = r.getContentText().slice(0, 200);
      if (code === 204) { log_(t.name, "깨움"); return true; }
    } catch (e) { body = String(e).slice(0, 200); }
    Utilities.sleep(4000);
  }
  log_(t.name, "실패 — " + code + " " + body);          // 401 = 토큰이 틀리거나 만료, 403/404 = 토큰에 그 저장소·Actions 권한이 없음
  return false;
}

/** 시트가 있으면 "깨우기기록" 탭에, 없으면 실행 기록에 남긴다. 토큰은 어디에도 적지 않는다. */
function log_(what, msg) {
  var line = [Utilities.formatDate(new Date(), "Asia/Seoul", "yyyy-MM-dd HH:mm:ss"), what, msg];
  try {
    var ss = SpreadsheetApp.getActiveSpreadsheet();
    var sh = ss.getSheetByName("깨우기기록") || ss.insertSheet("깨우기기록");
    if (sh.getLastRow() === 0) sh.appendRow(["시각(서울)", "대상", "결과"]);
    sh.appendRow(line);
    if (sh.getLastRow() > 600) sh.deleteRows(2, 200);
  } catch (e) { console.log(line.join(" | ")); }
}
