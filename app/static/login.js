// 로그인 화면 보조 스크립트
// 1) iOS 인데 아직 홈 화면 앱이 아니면 설치 안내(/install)를 먼저 보여준다.
//    (iOS 는 Safari 와 홈 화면 앱의 로그인이 분리되므로 '홈 화면에 추가 → 앱에서 로그인' 순서가 되게)
// 2) 홈 화면 앱에서 시작한 구글 로그인이 다른 창에서 끝났으면, 앱으로 돌아왔을 때 로그인을 넘겨받는다.
(function () {
  var ua = navigator.userAgent;
  var isIOS = /iPad|iPhone|iPod/.test(ua) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  var standalone = window.navigator.standalone === true || window.matchMedia("(display-mode: standalone)").matches;
  var params = new URLSearchParams(location.search);
  if (isIOS && !standalone && !params.has("web")) {
    location.replace("/install");
    return;
  }

  function check() {
    fetch("/auth/handoff", { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (d) { if (d.ok) location.replace("/today"); })
      .catch(function () {});
  }
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") check();
  });
  window.addEventListener("pageshow", check);
})();
