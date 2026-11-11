// login.js: iOS 이고 홈 화면 앱이 아니면 /install 로, 그 외에는 그대로 (handoff 확인만)
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const assert = require("assert");
const src = fs.readFileSync(path.join(__dirname, "../../app/static/login.js"), "utf8");

function run({ ua, platform = "iPhone", touch = 5, standalone = false, displayStandalone = false, search = "" }) {
  const replaced = [];
  const ctx = {
    navigator: { userAgent: ua, platform, maxTouchPoints: touch, standalone },
    window: { navigator: { standalone }, matchMedia: () => ({ matches: displayStandalone }), addEventListener() {} },
    location: { search, replace: (u) => replaced.push(u) },
    URLSearchParams,
    document: { addEventListener() {}, visibilityState: "visible" },
    fetch: () => new Promise(() => {}),
  };
  ctx.window.navigator = ctx.navigator;
  vm.runInNewContext(src, ctx);
  return replaced;
}

const IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 Version/17.4 Mobile/15E148 Safari/604.1";
const IPAD = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/17.4 Safari/605.1.15";
const ANDROID = "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/124 Mobile Safari/537.36";

assert.deepStrictEqual(run({ ua: IPHONE }), ["/install"]);                       // iPhone Safari → 설치 안내
assert.deepStrictEqual(run({ ua: IPAD, platform: "MacIntel" }), ["/install"]);  // iPadOS(데스크톱 UA) 도 감지
assert.deepStrictEqual(run({ ua: IPHONE, standalone: true }), []);              // 홈 화면 앱 → 그대로 로그인
assert.deepStrictEqual(run({ ua: IPHONE, search: "?web=1" }), []);              // '알림 없이 Safari 에서 계속'
assert.deepStrictEqual(run({ ua: ANDROID, platform: "Linux", touch: 5 }), []);  // 안드로이드 → 그대로
assert.deepStrictEqual(run({ ua: IPAD, platform: "MacIntel", touch: 0 }), []);  // 진짜 Mac → 그대로
console.log("login.js OK");
