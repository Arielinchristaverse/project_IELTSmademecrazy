// 서비스 워커 등록 (푸시 알림 수신용). 지원하지 않는 브라우저에서는 아무것도 하지 않는다.
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(function () {});
}
