/* afterwork 서비스 워커
 * - push: 받은 즉시 반드시 알림을 띄운다. iOS 는 알림을 띄우지 않는 푸시가 반복되면 구독을 취소한다.
 *   그래서 내용이 비었거나 형식이 깨져도 기본 문구로 알림을 띄운다.
 * - notificationclick: '/today' (또는 같은 사이트의 지정 경로)를 연다. 이미 열린 창이 있으면 그 창을 쓴다.
 * - 알림 액션 버튼은 쓰지 않는다 (iOS 미지원). 오프라인 캐시도 하지 않는다.
 */
const DEFAULT_URL = "/today";

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

function safePath(url) {
  // 같은 사이트 경로만 허용 ("/..." 이지만 "//..." 는 안 됨)
  return typeof url === "string" && url.startsWith("/") && !url.startsWith("//") ? url : DEFAULT_URL;
}

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (e) {
    data = { body: event.data ? event.data.text() : "" };
  }
  const title = data.title || "afterwork";
  const options = {
    body: data.body || "오늘의 공부를 확인하세요.",
    icon: "/static/icons/icon-192.png",
    badge: "/static/icons/badge-72.png",
    tag: data.tag || "daily",
    renotify: true,
    data: { url: safePath(data.url) },
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = new URL(safePath(event.notification.data && event.notification.data.url), self.location.origin).href;
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((windows) => {
      for (const w of windows) {
        if (w.url.startsWith(self.location.origin) && "focus" in w) {
          return w
            .focus()
            .then((f) => (f && "navigate" in f ? f.navigate(target) : f))
            .catch(() => self.clients.openWindow(target));
        }
      }
      return self.clients.openWindow(target);
    })
  );
});
