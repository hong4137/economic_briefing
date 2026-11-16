/**
 * GitHub 실행 깨우기 — GitHub 의 예약 실행이 몇 시간씩 밀리기 때문에, 정시 실행을 이 스크립트가 맡는다.
 * 이 스크립트는 수집도 거래도 하지 않는다. 정해진 때에 "지금 돌려라"고 부르기만 한다.
 *
 * 처음 한 번:
 *   1) 왼쪽 톱니바퀴(프로젝트 설정) → 스크립트 속성 → 속성 추가:  GH_TOKEN = (GitHub 토큰)
 *      토큰은 github.com → Settings → Developer settings → Fine-grained tokens 에서 만든다.
 *      Repository access: Only select repositories → 깨울 저장소만 / Permissions: Actions = Read and write (그것 하나만)
 *      ※ 토큰을 이 코드나 시트 칸에 적지 않는다. 스크립트 속성에만 둔다.
 *   2) 프로젝트 설정에서 시간대가 (GMT+09:00) 서울인지 확인한다.
 *   3) setup() 을 한 번 실행한다.  wakeNow() 로 전부, wakeExchangeNow() 로 거래소만 바로 깨워 볼 수 있다.
 *
 * 2026-11-17 (3판): 예약을 "시각마다 하나"에서 **"10분마다 한 번 깨어나 지금 부를 것이 있는지 본다"** 로 바꿨다.
 *   예약 하나로 어떤 시각표든 되고(한도 20개에 걸리지 않는다), 정한 시각이 지난 뒤 10분 안에 부른다.
 *   거래소 정기 실행은 장이 열려 있는 동안 20분마다 돈다 — 사람이 장중에 넣은 주문의 체결이 7시간씩 안 보이던 것을 고치려는 것이다.
 *   코드를 바꿔 붙였으면 setup() 을 다시 실행한다 (옛 예약을 지우고 새로 만든다).
 */
var TARGETS = [
  // times: 정한 시각(서울)에 한 번씩.   every: 그 시간대 안에서 몇 분마다.   weekdays: 1=월 … 7=일 (없으면 매일)
  // 자정을 넘는 시간대(예: 22:30~06:00)의 weekdays 는 "시작한 날"의 요일이다 — 금요일 밤에 시작한 미국장은 토요일 새벽까지 친다.

  { name: "경제 브리핑 수집", repo: "hong4137/economic_briefing", workflow: "collect.yml",
    times: ["00:20", "03:20", "05:20", "06:20", "09:20", "12:20", "15:20", "18:20", "21:20"] },
  // 미국장 마감은 서울 05:00(서머타임) 또는 06:00(겨울)이라 05:20 과 06:20 을 둘 다 넣었다.

  // 모의 거래소 정기 실행 — 시세 갱신·체결 계산·화면 자료.
  //  · 정한 시각: 미국 마감 뒤(06·07시), 한국 마감 뒤(16시), 장후 마감 뒤(20시), 선수의 미국장 전 점검 전(21·22시). 07시는 한국장 전 점검(08시쯤) 전.
  //  · 장중: 한국 정규장과 미국 정규장(서머타임·겨울 둘 다 덮게 22:30~06:00) 동안 20분마다.
  //  ※ 이 시각표를 바꾸면 거래소 방에 알린다 — 거래소 화면의 "다음 정기 실행" 표시가 같은 규칙을
  //    엔진 `broker/markets.py` 의 WAKE_TIMES·WAKE_EVERY 에 따로 갖고 있다(엔진 452d914). 둘이 어긋나면 화면이 틀린 시각을 보여 준다.
  { name: "모의 거래소 정기 실행", repo: "hong4137/paper-exchange", workflow: "daily.yml",
    times: ["06:20", "07:20", "16:20", "20:20", "21:20", "22:20"],
    every: [ { minutes: 20, from: "09:00", to: "15:40", weekdays: [1, 2, 3, 4, 5] },
             { minutes: 20, from: "22:30", to: "06:00", weekdays: [1, 2, 3, 4, 5] } ] },

  // 한국 장전(넥스트레이드 08:00~08:50) 시세는 그 시간에 돌아야만 모인다. 평일만. 일찍 깨워도 거래소 쪽이 08:52 까지 기다린다.
  { name: "모의 거래소 장전 수집", repo: "hong4137/paper-exchange", workflow: "premarket.yml",
    times: ["07:50"], weekdays: [1, 2, 3, 4, 5] },

  // 아침 한 장 — 모의투자의 모든 선수가 똑같이 읽는 사실 모음. 한국장 전 점검(08:04)과 미국장 전 점검(22:50, 서머타임에는 21:53) 전에 만든다.
  // 시장(KR·US·WEEK)은 워크플로가 깨어난 시각으로 정한다.
  { name: "아침 한 장 (평일)", repo: "hong4137/economic_briefing", workflow: "brief.yml",
    times: ["07:25", "21:25", "22:25"], weekdays: [1, 2, 3, 4, 5] },
  { name: "아침 한 장 (주간 회의)", repo: "hong4137/economic_briefing", workflow: "brief.yml",
    times: ["19:15"], weekdays: [7] }
  // 다른 저장소도 정시에 돌리고 싶으면 여기에 한 줄 더한다. 그 저장소의 워크플로에 workflow_dispatch 가 있어야 하고, 토큰에 그 저장소를 추가해야 한다.
];

var GRACE_MIN = 25;   // 정한 시각이 지나고 이만큼 안에 깨어나면 부른다 (10분마다 깨어나므로 넉넉하다)

function setup() {
  ScriptApp.getProjectTriggers().forEach(function (t) { if (t.getHandlerFunction() === "tick") ScriptApp.deleteTrigger(t); });
  ScriptApp.newTrigger("tick").timeBased().everyMinutes(10).create();
  log_("예약 만듦", "10분마다 한 번 깨어나 살핀다 (예약 1개)");
}

function hm_(s) { var p = s.split(":"); return Number(p[0]) * 60 + Number(p[1]); }

/** 지금(서울) 부를 것들: [{target, key, stamp}] — 같은 차례를 두 번 부르지 않게 key 에 stamp 를 적어 둔다. now 를 주면 그 시각인 것처럼 본다(시험용). */
function dueNow_(now) {
  now = now || new Date();
  var hm = Utilities.formatDate(now, "Asia/Seoul", "H:m").split(":");
  var nowMin = Number(hm[0]) * 60 + Number(hm[1]);
  var day = Utilities.formatDate(now, "Asia/Seoul", "yyyy-MM-dd");
  var wd = Number(Utilities.formatDate(now, "Asia/Seoul", "u"));       // 1=월 … 7=일
  var ywd = wd === 1 ? 7 : wd - 1;                                      // 어제의 요일
  var out = [];
  TARGETS.forEach(function (t) {
    var hit = null;
    (t.times || []).forEach(function (s) {
      if (t.weekdays && t.weekdays.indexOf(wd) < 0) return;
      var d = nowMin - hm_(s);
      if (d >= 0 && d < GRACE_MIN) hit = hit || (day + " " + s);
    });
    (t.every || []).forEach(function (w) {
      var a = hm_(w.from), b = hm_(w.to), inside, startWd;
      if (a <= b) { inside = nowMin >= a && nowMin < b; startWd = wd; }
      else if (nowMin >= a) { inside = true; startWd = wd; }            // 자정 전 부분
      else { inside = nowMin < b; startWd = ywd; }                      // 자정을 넘긴 부분 — 어제 시작한 시간대
      if (!inside) return;
      if (w.weekdays && w.weekdays.indexOf(startWd) < 0) return;
      hit = hit || (day + " 칸" + Math.floor(nowMin / w.minutes));
    });
    // 한 번 깨어날 때 한 대상은 한 번만 부른다. 정한 시각과 장중 칸이 겹치면 정한 시각으로 친다.
    if (hit) out.push({ target: t, key: "last_" + t.repo + "_" + t.workflow, stamp: hit });
  });
  return out;
}

/** 10분마다 돈다. 같은 대상의 같은 차례는 한 번만 부른다. */
function tick() {
  var props = PropertiesService.getScriptProperties();
  dueNow_().forEach(function (d) {
    var key = d.key + "_" + d.target.name;
    if (props.getProperty(key) === d.stamp) return;
    if (wake_(d.target)) props.setProperty(key, d.stamp);
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
    if (sh.getLastRow() > 1500) sh.deleteRows(2, 500);
  } catch (e) { console.log(line.join(" | ")); }
}
