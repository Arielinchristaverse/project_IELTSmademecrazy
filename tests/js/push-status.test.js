// push-status.js 의 decide(): 어떤 배너를 띄울지
const assert = require("assert");
const { decide } = require("../../app/static/push-status.js");
const base = { ready: true, supported: true, isIOS: true, standalone: true, permission: "granted", hasSub: true, serverKnows: true };
const d = (o) => decide({ ...base, ...o });

assert.strictEqual(d({}), null);                                          // 정상: 배너 없음
assert.strictEqual(d({ ready: false }), null);                            // 서버 키 없음: 배너 없음
assert.strictEqual(d({ supported: false, standalone: false }), "install");// iPhone Safari: 홈 화면 앱 안내
assert.strictEqual(d({ supported: false, isIOS: false }), null);          // 지원 안 하는 다른 브라우저
assert.strictEqual(d({ permission: "denied" }), "denied");                // 차단: 기기 설정 안내 (권한 요청 안 함)
assert.strictEqual(d({ permission: "default", hasSub: false }), "off");   // 아직 안 켬: 다시 켜기
assert.strictEqual(d({ hasSub: false }), "off");                          // 권한은 있으나 구독 없음
assert.strictEqual(d({ serverKnows: false }), "lost");                    // 서버에서 만료 삭제됨
console.log("ok");
