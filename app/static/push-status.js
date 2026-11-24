// 앱을 열 때 이 기기의 알림 상태를 확인하고, 꺼져 있거나 연결이 끊겼으면 상단 배너로 알린다.
// - 권한 요청은 '다시 켜기' 버튼을 탭했을 때만 한다 (iOS 요구사항 · 갑작스러운 권한 창 방지).
// - 판단은 decide() 하나에 모았다 (tests/js/push-status.test.js 에서 확인).
(function (root) {
  function decide(s) {
    if (!s.ready) return null;                         // 서버에 VAPID 키가 없음
    if (!s.supported) return s.isIOS && !s.standalone ? "install" : null;
    if (s.permission === "denied") return "denied";
    if (s.permission !== "granted" || !s.hasSub) return "off";
    if (s.serverKnows === false) return "lost";        // 서버에서 지워짐(만료 등) → 다시 연결
    return null;
  }
  root.afterworkPushDecide = decide;
  if (typeof module !== "undefined") module.exports = { decide: decide };

  var doc = root.document;
  var box = doc && doc.getElementById("push-alert");
  if (!box) return;
  var KEY = "aw-push-alert-later";
  try { if (root.sessionStorage.getItem(KEY)) return; } catch (e) { /* 저장소를 못 쓰면 매번 확인 */ }

  var nav = root.navigator;
  var isIOS = /iPad|iPhone|iPod/.test(nav.userAgent) || (nav.platform === "MacIntel" && nav.maxTouchPoints > 1);
  var standalone = nav.standalone === true || root.matchMedia("(display-mode: standalone)").matches;
  var supported = "serviceWorker" in nav && "PushManager" in root && "Notification" in root;
  var textEl = box.querySelector("[data-text]");
  var enableBtn = box.querySelector("[data-enable]");
  var publicKey = null, registration = null, current = null;

  var TEXT = {
    install: "알림은 홈 화면에 추가한 앱에서만 받을 수 있어요.",
    denied: "이 기기에서 알림이 차단되어 있어요. 기기 설정에서 afterwork 알림을 허용해 주세요.",
    off: "알림이 꺼져 있어요. 정한 시각에 오늘의 과제를 알려 드릴게요.",
    lost: "알림 연결이 끊겼어요. 다시 켜면 오늘부터 받을 수 있어요.",
  };

  function show(kind) {
    current = kind;
    box.dataset.kind = kind;
    textEl.textContent = TEXT[kind];
    enableBtn.hidden = !(kind === "off" || kind === "lost");
    box.querySelector("[data-install]").hidden = kind !== "install";
    box.querySelector("[data-howto]").hidden = kind !== "denied";
    box.hidden = false;
  }

  function keyBytes(b64) {
    var pad = "=".repeat((4 - (b64.length % 4)) % 4);
    var raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
    var out = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
    return out;
  }
  function postJSON(url, body) {
    return fetch(url, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
  }

  function enable() {
    enableBtn.disabled = true;
    // ↓ 탭 처리 안에서 곧바로 권한 요청
    var ask = Notification.permission === "granted" ? Promise.resolve("granted") : Notification.requestPermission();
    ask.then(function (perm) {
      if (perm !== "granted") { show(perm === "denied" ? "denied" : "off"); return; }
      return registration.pushManager.getSubscription()
        .then(function (old) { return old && current === "lost" ? old.unsubscribe() : null; })
        .then(function () {
          return registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(publicKey) });
        })
        .then(function (sub) { return postJSON("/push/subscribe", sub.toJSON()); })
        .then(function (r) {
          if (!r.ok) throw new Error(r.status);
          textEl.textContent = "알림을 켰어요.";
          enableBtn.hidden = true;
          setTimeout(function () { box.hidden = true; }, 2500);
        });
    }).catch(function () {
      textEl.textContent = "알림을 켜지 못했어요. 설정 > 알림에서 다시 시도해 주세요.";
    }).then(function () { enableBtn.disabled = false; });
  }

  enableBtn.addEventListener("click", enable);
  box.querySelector("[data-later]").addEventListener("click", function () {
    box.hidden = true;
    try { root.sessionStorage.setItem(KEY, "1"); } catch (e) { /* 무시 */ }
  });

  var base = { supported: supported, isIOS: isIOS, standalone: standalone, ready: true };
  if (!supported) { var k = decide(base); if (k) show(k); return; }
  Promise.all([
    fetch("/push/key", { credentials: "same-origin" }).then(function (r) { return r.json(); }),
    nav.serviceWorker.register("/sw.js", { scope: "/" }).then(function () { return nav.serviceWorker.ready; }),
  ]).then(function (res) {
    publicKey = res[0].publicKey;
    registration = res[1];
    base.ready = res[0].ready;
    base.permission = Notification.permission;
    return registration.pushManager.getSubscription().then(function (sub) {
      base.hasSub = !!sub;
      if (!sub || base.permission !== "granted") return;
      return postJSON("/push/status", { endpoint: sub.endpoint })
        .then(function (r) { return r.json(); })
        .then(function (d) { base.serverKnows = !!d.known; });
    });
  }).then(function () {
    var kind = decide(base);
    if (kind) show(kind);
  }).catch(function () { /* 확인 실패 시 배너를 띄우지 않는다 */ });
})(typeof window !== "undefined" ? window : globalThis);
