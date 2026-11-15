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
 */
var TARGETS = [
  // 깨울 곳.  hours: 실행할 시각(서울, 0~23).  minute: 그 시각의 몇 분쯤(앞뒤 15분 안에서 돈다).
  { name: "경제 브리핑 수집", repo: "hong4137/economic_briefing", workflow: "collect.yml", ref: "main",
    hours: [0, 3, 5, 6, 9, 12, 15, 18, 21], minute: 20 }
  // 미국장 마감은 서울 시각 05:00(서머타임) 또는 06:00(겨울)이라 05:20 과 06:20 을 둘 다 넣었다.
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

/** 예약된 시각마다 돈다. 지금 시각이 TARGETS 의 hours 에 들어 있으면 깨운다. 같은 시각에 두 번 깨우지 않는다. */
function tick() {
  var now = new Date();
  var hour = Number(Utilities.formatDate(now, "Asia/Seoul", "H"));
  var stamp = Utilities.formatDate(now, "Asia/Seoul", "yyyy-MM-dd HH");
  var props = PropertiesService.getScriptProperties();
  TARGETS.forEach(function (t) {
    if (t.hours.indexOf(hour) < 0) return;
    var key = "last_" + t.repo + "_" + t.workflow;
    if (props.getProperty(key) === stamp) return;
    if (wake_(t)) props.setProperty(key, stamp);
  });
}

function wakeNow() { TARGETS.forEach(wake_); }

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
