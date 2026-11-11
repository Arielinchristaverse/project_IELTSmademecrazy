// sw.js 를 가짜 서비스 워커 환경(vm)에 올려 push / notificationclick 동작을 검증한다.
// 실행: node tests/js/sw.test.js   (pytest 가 tests/test_pwa.py 에서 자동 실행)
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const assert = require("assert");

function load({ windows = [] } = {}) {
  const listeners = {};
  const shown = [];
  const opened = [];
  const self = {
    location: { origin: "https://app.example" },
    addEventListener: (t, fn) => (listeners[t] = fn),
    skipWaiting: () => {},
    registration: { showNotification: (title, opts) => (shown.push({ title, ...opts }), Promise.resolve()) },
    clients: {
      claim: () => Promise.resolve(),
      matchAll: () => Promise.resolve(windows),
      openWindow: (url) => (opened.push(url), Promise.resolve(null)),
    },
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../../app/static/sw.js"), "utf8"), { self, URL });
  async function fire(type, event) {
    let p = Promise.resolve();
    event.waitUntil = (x) => (p = x);
    listeners[type](event);
    await p;
  }
  return { fire, shown, opened };
}

const data = (v) => ({ json: () => JSON.parse(v), text: () => v });

(async () => {
  // 1) 정상 payload → 알림 표시
  let sw = load();
  await sw.fire("push", { data: data(JSON.stringify({ title: "Study Shift", body: "오늘의 공부 과제 3개 · 총 90분", url: "/today" })) });
  assert.strictEqual(sw.shown.length, 1);
  assert.strictEqual(sw.shown[0].body, "오늘의 공부 과제 3개 · 총 90분");
  assert.strictEqual(sw.shown[0].data.url, "/today");
  assert.ok(!("actions" in sw.shown[0]), "알림 액션 버튼은 쓰지 않음");

  // 2) 깨진 payload / 빈 payload 에도 반드시 알림 표시 (iOS 구독 취소 방지)
  sw = load();
  await sw.fire("push", { data: data("not json") });
  await sw.fire("push", { data: null });
  assert.strictEqual(sw.shown.length, 2);
  assert.strictEqual(sw.shown[0].body, "not json");
  assert.strictEqual(sw.shown[1].body, "오늘의 공부를 확인하세요.");
  assert.strictEqual(sw.shown[1].data.url, "/today");

  // 3) 외부 주소는 무시하고 /today
  sw = load();
  await sw.fire("push", { data: data(JSON.stringify({ url: "//evil.example" })) });
  assert.strictEqual(sw.shown[0].data.url, "/today");

  // 4) 클릭: 열린 창이 없으면 /today 새로 열기
  sw = load();
  let closed = false;
  await sw.fire("notificationclick", { notification: { data: { url: "/today" }, close: () => (closed = true) } });
  assert.ok(closed);
  assert.deepStrictEqual(sw.opened, ["https://app.example/today"]);

  // 5) 클릭: 앱 창이 이미 열려 있으면 그 창을 앞으로 + /today 로 이동
  const nav = [];
  const win = { url: "https://app.example/settings", focus() { return Promise.resolve(this); }, navigate: (u) => (nav.push(u), Promise.resolve()) };
  sw = load({ windows: [win] });
  await sw.fire("notificationclick", { notification: { data: { url: "/today" }, close() {} } });
  assert.deepStrictEqual(nav, ["https://app.example/today"]);
  assert.deepStrictEqual(sw.opened, []);

  console.log("sw.js OK");
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
