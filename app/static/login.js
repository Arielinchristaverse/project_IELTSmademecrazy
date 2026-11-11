// 홈 화면 앱(iOS)에서 시작한 구글 로그인이 다른 창에서 끝났을 때, 앱으로 돌아오면 로그인을 넘겨받는다.
(function () {
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
