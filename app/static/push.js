// 이 기기의 웹 푸시 켜기 / 테스트 알림. 설정 화면의 #push-box 에서만 동작한다.
// iOS 요구사항: 알림 권한 요청은 반드시 사용자가 버튼을 탭한 그 처리 안에서 실행해야 한다.
// 그래서 공개키와 서비스 워커 준비는 화면을 열 때 미리 해 두고, 탭 처리 안에서는 바로 권한을 요청한다.
(function () {
  var box = document.getElementById("push-box");
  if (!box) return;
  var statusEl = box.querySelector("[data-status]");
  var enableBtn = box.querySelector("[data-enable]");
  var testBtn = box.querySelector("[data-test]");
  var deniedHelp = box.querySelector("[data-denied]");
  var installHelp = box.querySelector("[data-install]");

  var ua = navigator.userAgent;
  var isIOS = /iPad|iPhone|iPod/.test(ua) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  var standalone = window.navigator.standalone === true || window.matchMedia("(display-mode: standalone)").matches;
  var supported = "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;

  var publicKey = null;
  var registration = null;

  function say(msg) { statusEl.textContent = msg; }
  function show(el, on) { if (el) el.hidden = !on; }

  function keyBytes(b64) {
    var pad = "=".repeat((4 - (b64.length % 4)) % 4);
    var raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
    var out = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
    return out;
  }

  function save(sub) {
    return fetch("/push/subscribe", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(sub.toJSON()),
    }).then(function (r) { if (!r.ok) throw new Error("서버 저장 실패 (" + r.status + ")"); });
  }

  function subscribe() {
    var opts = { userVisibleOnly: true, applicationServerKey: keyBytes(publicKey) };
    return registration.pushManager.subscribe(opts).catch(function (e) {
      // 서버 키가 바뀐 경우 등: 예전 구독을 지우고 다시 시도
      return registration.pushManager.getSubscription().then(function (old) {
        if (!old) throw e;
        return old.unsubscribe().then(function () { return registration.pushManager.subscribe(opts); });
      });
    });
  }

  function onEnable() {
    if (!publicKey || !registration) { say("아직 준비 중이에요. 잠시 후 다시 눌러 주세요."); return; }
    enableBtn.disabled = true;
    // ↓ 탭 처리 안에서 곧바로 권한 요청
    Notification.requestPermission()
      .then(function (perm) {
        if (perm !== "granted") {
          say("알림이 허용되지 않았어요.");
          show(deniedHelp, true);
          return;
        }
        return subscribe().then(save).then(function () {
          say("이 기기에서 알림을 받도록 켰어요. '테스트 알림 보내기'로 확인해 보세요.");
          show(testBtn, true);
          enableBtn.textContent = "다시 연결";
        });
      })
      .catch(function (e) { say("알림을 켜지 못했어요: " + (e && e.message ? e.message : e)); })
      .then(function () { enableBtn.disabled = false; });
  }

  function onTest() {
    testBtn.disabled = true;
    say("보내는 중…");
    fetch("/push/test", { method: "POST", credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (d) { say(d.message || (d.ok ? "보냈어요." : "보내지 못했어요.")); })
      .catch(function () { say("보내지 못했어요."); })
      .then(function () { testBtn.disabled = false; });
  }

  function init() {
    if (!supported) {
      if (isIOS && !standalone) {
        say("아이폰·아이패드에서는 홈 화면에 추가한 앱에서만 알림을 받을 수 있어요.");
        show(installHelp, true);
      } else {
        say("이 브라우저는 웹 푸시 알림을 지원하지 않아요. 알림 받는 방법을 '메일'로 바꿔 주세요.");
      }
      show(enableBtn, false);
      return;
    }
    enableBtn.addEventListener("click", onEnable);
    if (testBtn) testBtn.addEventListener("click", onTest);

    if (Notification.permission === "denied") {
      say("이 기기에서 알림이 차단되어 있어요.");
      show(deniedHelp, true);
      show(enableBtn, false);
      return;
    }
    Promise.all([
      fetch("/push/key", { credentials: "same-origin" }).then(function (r) { return r.json(); }),
      navigator.serviceWorker.register("/sw.js", { scope: "/" }).then(function () { return navigator.serviceWorker.ready; }),
    ])
      .then(function (res) {
        var key = res[0];
        registration = res[1];
        if (!key.ready) {
          say("서버에 알림 키(VAPID)가 아직 설정되지 않았어요.");
          show(enableBtn, false);
          return;
        }
        publicKey = key.publicKey;
        return registration.pushManager.getSubscription().then(function (sub) {
          if (sub && Notification.permission === "granted") {
            say("이 기기에서 알림을 받고 있어요.");
            enableBtn.textContent = "다시 연결";
            show(testBtn, true);
            return save(sub); // 서버 기록이 지워졌어도 다시 맞춰 둔다
          }
          say("이 기기는 아직 알림이 꺼져 있어요. 아래 버튼을 눌러 켜 주세요.");
        });
      })
      .catch(function (e) { say("알림 상태를 확인하지 못했어요: " + (e && e.message ? e.message : e)); });
  }

  init();
})();
