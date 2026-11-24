# afterwork — 교대근무자 맞춤 학습 플래너

> 오늘의 작은 실행이, 내 삶의 다음 페이지를 만듭니다. *Your next chapter begins with today’s small win.*

근무 스케줄(데이/이브닝/나이트/오프)에 맞춰 한 주의 공부 과제를 **규칙 기반으로** 배정하고,
매일 근무 유형별 시각에 **웹 푸시 알림**으로 오늘의 과제를 알려 줍니다 (메일 알림은 출시 전에 제거, 태그 `mail-feature-final` 에 보관).
알림을 누르면 앱의 **오늘** 화면이 열리고, 거기서 달성/미달성을 바로 기록합니다.
주 타깃은 iPhone(iOS 16.4+, 홈 화면에 추가한 앱)이며 안드로이드·PC 크롬도 동작합니다.

| 영역 | 사용 기술 |
|---|---|
| 서버 | Python 3.12 · FastAPI · Jinja2 (서버 렌더링, 프레임워크 없는 CSS/바닐라 JS) |
| DB | SQLite + SQLAlchemy 2 · Alembic 마이그레이션 (Postgres 이전 가능한 구조) |
| 로그인 | 구글 OIDC (Authlib, openid/email/profile) · 개발용 이메일 로그인(DEV_LOGIN) |
| 알림 | 웹 푸시 전용 (pywebpush + VAPID) |
| 스케줄러 | APScheduler, 1분마다 전체 사용자 순회 · 실패 시 10분 간격 최대 3회 재시도 |
| PWA | manifest · 서비스 워커(sw.js) · iOS 메타 태그 · 아이콘 |
| 디자인 | afterwork 브랜드 토큰(라이트/다크) · 워드마크 크기는 `style.css` 의 `--logo-size` 한 곳(큰 버전 = 2배) · Pretendard + Cormorant Garamond (자체 호스팅, OFL) |

---

## 1. 내 PC에서 실행 (로컬)

```bash
python3 -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                   # Windows: copy .env.example .env
uvicorn app.main:app --port 8000
```

- http://localhost:8000 → **개발용 로그인**에 아무 이메일 입력 → 온보딩(근무 유형 → 과제 → 근무 달력 → 알림)
- 웹 푸시는 `localhost` 에서도 동작합니다(브라우저가 localhost 를 안전한 주소로 취급). VAPID 키가 필요합니다(아래 3-2).
- 테스트: `pytest -q` (Node 가 있으면 서비스 워커·로그인 스크립트 테스트도 함께 실행)

## 2. 아이패드·휴대폰에서 확인 (GitHub Codespaces)

PC 없이 브라우저만으로 실행해 보는 방법입니다. 창을 닫으면 잠시 뒤 꺼지므로 **확인용**입니다.

1. Safari에서 열기 → **Create codespace**
   https://codespaces.new/arielinchristaverse/project_ieltsmademecrazy/tree/claude/shift-study-task-scheduler-cl2wf7
2. 2~3분 뒤 아래 **PORTS** 탭 → `afterwork (8000)` 의 🌐 아이콘
3. **개발용 로그인**(DEV_LOGIN 이 켜져 있음)으로 들어가 온보딩과 화면 확인
4. 서버가 안 떠 있으면 TERMINAL 에 `bash .devcontainer/start.sh`

선택 설정은 https://github.com/settings/codespaces → *Codespaces secrets* 에 넣고 Codespace 를 다시 시작합니다
(`.devcontainer/devcontainer.json` 의 `secrets` 항목에 목록과 설명이 있습니다).

**Codespaces 에서 HTTPS 기능(푸시·서비스 워커) 시험하기**
- Codespaces 주소는 `https://…app.github.dev` 라서 서비스 워커와 웹 푸시가 동작합니다.
- 다만 포트가 기본 *Private* 이라 GitHub 로그인이 필요합니다. PC 크롬처럼 GitHub 에 로그인된 브라우저에서 시험하세요.
- iPhone 홈 화면 앱은 Safari 와 쿠키가 분리되어 GitHub 로그인이 풀리므로, **iPhone 푸시 시험은 Render(3장)에서** 하는 것을 권합니다.
  꼭 Codespaces 에서 하려면 PORTS 탭에서 8000번을 *Public* 으로 바꾸세요 (주소를 아는 누구나 접속 가능해지므로 시험 후 되돌릴 것).
- 구글 로그인을 시험하려면 Google Cloud 의 리디렉션 URI 에 `https://<codespace이름>-8000.app.github.dev/auth/google/callback` 을 추가해야 합니다 (Codespace 마다 주소가 달라짐).

## 3. 24시간 운영 (Render)

`render.yaml`(Blueprint)에 설정이 들어 있어 브라우저만으로 배포할 수 있습니다.
Starter 요금제(약 $7/월) + 디스크 1GB(약 $0.25/월). 무료 요금제는 서버가 잠들어 알림 시각을 놓치고, 디스크를 쓸 수 없습니다.

### 3-1. 구글 로그인 준비 (Google Cloud Console)
1. https://console.cloud.google.com → 새 프로젝트 → **Google Auth Platform**(OAuth 동의 화면)
2. 앱 이름 `afterwork`, 대상 **외부**, 게시 상태 **테스트** → 테스트 사용자에 쓸 사람의 Gmail 추가
3. **클라이언트 → 웹 애플리케이션** 생성 → 클라이언트 ID / 보안 비밀번호 보관
4. Render 주소가 정해지면 **승인된 리디렉션 URI** 에 `https://<render주소>/auth/google/callback` 추가

### 3-2. 웹 푸시 키(VAPID) 만들기 — 한 번만
Codespaces 터미널(또는 로컬)에서:
```bash
python -m scripts.gen_vapid mailto:you@example.com
```
출력된 `VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY / VAPID_SUBJECT` 를 보관합니다.
키를 바꾸면 모든 기기에서 *알림 켜기* 를 다시 해야 하므로, 한 번 정하면 그대로 씁니다.

### 3-3. 배포
1. https://render.com → GitHub 로 가입, 결제 수단 등록
2. **New → Blueprint** → 이 저장소 선택 → 아래 값을 입력 → **Apply**

아래 **환경변수 점검표**의 "직접 입력" 값을 넣습니다. 값 자체는 README·코드·채팅 어디에도 적지 마세요.

#### 환경변수 점검표
서버 시작 로그 첫 줄 `[config] 이름=설정됨|없음 …` 으로 **설정 여부만** 확인할 수 있습니다(값은 출력하지 않음).

| 이름 | 의미 | 필수 | 운영 권장값 / 설정 방법 |
|---|---|---|---|
| `SECRET_KEY` | 세션 쿠키 서명 키 | **필수** (기본값이면 시작 거부) | Render 자동 생성(`generateValue`). 바꾸면 모든 사용자가 다시 로그인 |
| `GOOGLE_CLIENT_ID` | 구글 로그인 클라이언트 ID | **필수** | Google Cloud Console OAuth 클라이언트 (3-1) |
| `GOOGLE_CLIENT_SECRET` | 구글 로그인 클라이언트 보안 비밀 | **필수** | 같은 곳. 유출 시 Console 에서 재발급 |
| `VAPID_PUBLIC_KEY` | 웹 푸시 공개 키 | **필수** (없으면 시작 거부) | `python -m scripts.gen_vapid` 로 한 번 생성. **바꾸면 모든 기기 구독이 끊김** |
| `VAPID_PRIVATE_KEY` | 웹 푸시 개인 키 | **필수** | 위와 한 쌍. 외부에 노출 금지 |
| `VAPID_SUBJECT` | 푸시 서버에 알리는 운영자 연락처 | **필수** | `mailto:운영 문의 주소` 형식 |
| `ADMIN_EMAIL` | 관리자 구글 이메일 (관리자 화면·백업, 정원 제외) | 권장 | 관리자는 이 계정 하나. 바꾸고 재배포하면 예전 관리자 권한은 빠짐. 비우면 관리자 플래그를 바꾸지 않음 |
| `MAX_USERS` | 가입 정원 (관리자 제외) | 선택 (기본 50) | `50`. 3단계부터 가입 시 적용 |
| `BASE_URL` | 앱 주소 (로그인 리디렉션·푸시 링크) | 선택 | 비우면 Render 주소 자동 사용. 커스텀 도메인을 쓰면 `https://…` 로 지정 |
| `ALLOWED_EMAILS` | 가입 허용 목록 (쉼표 구분) | 선택 | 3단계(정원제)에서 제거 예정. 지금은 비우면 누구나 가입 |
| `DATABASE_URL` | DB 위치 | 고정 | `sqlite:////var/data/app.db` (render.yaml) |
| `TIMEZONE` | 날짜·알림 기준 시간대 | 고정 | `Asia/Seoul` |
| `DEV_LOGIN` | 개발용 이메일 로그인 | **운영 금지** | 운영에서 켜져 있으면 시작 거부. 로컬·Codespaces 전용 |
| `SCHEDULER_ENABLED` | 알림 스케줄러 | 선택 | 운영은 기본값(true) 그대로. 서버는 반드시 1대 |

3. 배포가 끝나면 3-1 의 4번(리디렉션 URI)을 등록합니다.

### 3-4. iPhone 에서 확인 (실기기 HTTPS 테스트)
1. iPhone **Safari** 로 Render 주소 접속 → 설치 안내 화면이 나옴
2. **공유 버튼 → 홈 화면에 추가** → 홈 화면의 **afterwork** 앱 실행
3. 앱에서 **Google 계정으로 로그인** → 온보딩 → 마지막 단계에서 **알림 켜기 → 허용**
4. **테스트 알림 보내기** → 알림 수신 → 알림을 눌러 **오늘** 화면이 열리는지 확인
5. 다음 날 근무 유형별 알림 시각에 자동 알림이 오는지 확인

알림을 거부했다면: **설정 앱 → 알림 → afterwork → 알림 허용**. 앱을 열 때 알림이 꺼져 있거나 연결이 끊겼으면 화면 위에 안내와 *다시 켜기* 버튼이 나옵니다.

## 4. 데이터 백업 · 복원 · 마이그레이션

- **매일 자동 백업**: 매일 04:17(한국 시간) `/var/data/backups/app-daily-<시각>.db` 를 만들고, **30일 지난 백업 파일은 자동 삭제**합니다.
  같은 작업에서 로그인 진행 기록은 1일, 알림 발송 기록은 90일, 하루 집계는 30일이 지나면 지웁니다. 로그에는 개수만 남깁니다.
- **마이그레이션 직전 백업**: 서버 시작 시 DB 구조를 바꿀 마이그레이션이 있으면, 먼저 `app-before-<버전>-<시각>.db` 로 복사합니다(30일 뒤 자동 삭제).
  마이그레이션은 하나의 트랜잭션이라 중간에 실패해도 DB 가 반쯤 바뀐 채 남지 않습니다.
- **수동 백업**: 관리자 계정으로 로그인 → **설정 → 관리자 화면 → DB 백업 내려받기** (아이패드에서도 가능).
  또는 Render **Shell** 에서 `python -m scripts.backup_db`.
- **마이그레이션 리허설**: 운영 백업 파일로 `python -m scripts.rehearse_migrations <백업.db>` → 임시 사본에 한 단계씩 적용하며
  기존 데이터 변화·외래키·무결성을 검사하고, 사용자별 주요 화면이 열리는지 확인한 뒤 사본을 지웁니다(출력에 개인정보 없음).
- **복원**: Render Shell 에서 백업 파일을 `/var/data/app.db` 로 복사한 뒤, 그 백업과 같은 시점의 커밋으로 다시 배포합니다.
- **단일 사용자 시절 DB 이전**: `ADMIN_EMAIL` 을 설정하고 새 버전을 배포하면, 기존 데이터가 모두 그 계정으로 옮겨집니다.
  데이터가 있는데 `ADMIN_EMAIL` 이 없으면 서버가 시작을 거부하고 DB 는 그대로 둡니다.

## 5. 규칙 요약

| 항목 | 규칙 |
|---|---|
| 한 주 | 월 ~ 일 |
| 근무 유형 | 사용자가 직접 추가·수정·보관 (이름, 공부 가능 시간, 알림 시각, 쉬는 날, 색). 사용 중 최대 8개, 이름 중복 불가. 새 사용자는 데이·이브닝·나이트·오프 프리셋으로 시작. 근무 유형은 달력에서 고른 날짜에 귀속 |
| 보관·삭제 | 달력에서 쓴 유형은 삭제 불가(DB 외래키도 막음), 보관만 가능. 보관하면 선택지에서만 숨기고 기존 날은 그대로 동작 |
| 유형 수정 | 공부 시간이나 쉬는 날이 바뀌면 **내일부터** 배정 재계산. 오늘·지난 날·기록한 배정은 유지 |
| 보충일 | **쉬는 날**로 표시한 근무 유형의 날. 요일과 무관 (일요일도 다른 요일과 같음) |
| 평일 배정 | 근무 유형별 공부 가능 시간 안에서, 과제를 쪼개지 않고 통째로 |
| 우선순위 | 긴급도(남은 횟수 ÷ 남은 가능일) → 이번 주 배정 비율 낮은 순 → 같은 과목 적은 순 → 긴 과제 먼저 |
| 연속 회피 | 어제 한 과제는 피함(여유가 없으면 허용). 남은 횟수는 여유 시간에 보정 배정 |
| 쉬는 날 | 보류 과제(오래된 순) 먼저 → 남은 시간에 정규 과제. 재배정은 하루 1회 |
| 미기록 | 다음 날 준비 시점에 자동 미달성(보류) |
| 진척도 | (이번 주 정규 과제 달성 + 추가 달성) ÷ 주당 횟수 합계 (%) — 숫자 + 진행률 바. 추가 달성은 과제별로 주당 횟수까지만 셈. 분모는 그대로 |
| 여유가 생기면 해볼 과제 | 시간 부족으로 이번 주 배정에 못 넣은 과제(배정할 때 횟수를 저장)를 이번 주 화면에서 체크해 **추가 달성**으로 기록. 못 넣은 횟수까지만, **이번 주만** 기록·되돌리기 가능, 날짜는 누른 날(Asia/Seoul), 그날 공부 시간 한도와 무관. 재배정으로 못 넣은 횟수가 줄어도 기록은 유지(진척도는 위 상한). 오늘 배정·보류·재알림·푸시 내용과 별개 |
| 알림 | **웹 푸시 전용**. 사용자별 근무 유형 알림 시각에 하루 1회. 알림을 받을 기기마다 켬. 앱을 열 때 꺼져 있거나 끊겼으면 상단 배너로 *다시 켜기* (권한 요청은 버튼 탭으로만) |
| 재알림 | 유형별 재알림 시각(기본: 알림 + 6시간, 자정을 넘으면 없음, 끄기 가능)에, 그날 **아직 기록하지 않은 과제**가 남아 있으면 한 번 더. 정기 알림이 실제로 간 날만, 하루 1회. 서버 재시작 시 그날 안이면 늦게라도, 자정 이후에는 안 보냄. 푸시 문구: 첫 알림 본문은 과제 요약(`오늘의 공부 과제 N개 · 총 M분`), 브랜드 인사말은 **재알림에만** (날짜별 순환). 문구는 `app/services/notify_text.py` |
| 푸시 실패 | 404/410 이면 그 기기 구독 삭제, 그 외 10분 간격 최대 3회 재시도 |
| 영어 문장 | 날짜가 바뀔 때 한 번 다음 문장으로 (그날 오늘 화면은 하루 종일 같은 문장) |
| 관리자 화면 | `/admin` (ADMIN_EMAIL 계정만, 다른 사용자는 404): 오늘 푸시 발송 현황, 만료 삭제 수, 알림을 켠 가입자 비율, 가입자/정원, 백업 |
| 상태 확인 | `/healthz` 는 DB 연결만 확인해 `{"ok": true, "db": "ok"}` (실패 시 503). 설정·사용자 정보는 내보내지 않음 |

## 6. 폴더 구조
```
app/
  main.py  config.py  db.py  models.py  deps.py  auth.py  scheduler.py  clock.py  seed.py
  routers/   today · weeks · calendar · tasks · settings · shifts · sentences · onboarding · account
             push · pwa · install · admin
  services/  allocator(배분) · daily(준비·알림) · shifts(근무 유형) · push · progress · users · google_auth · backup · maintenance(매일 백업·정리) · admin_stats · counters · extras
  templates/ 화면 · partials/ · onboarding/
  static/    style.css · sw.js · push.js · push-status.js · login.js · app.js · icons/ · fonts/
migrations/  Alembic (0001 기준 · 0002 다중 사용자 · 0003 푸시 구독 · 0004 사용자 정의 근무 유형 · 0005 재알림 · 0006 여유가 생기면 해볼 과제 · 0007 푸시 전용 전환)
scripts/     migrate · rehearse_migrations · backup_db · gen_vapid · make_icons.js
tests/       pytest + tests/js (Node)
render.yaml  .devcontainer/  .env.example
```

글꼴: Pretendard (© Kil Hyung-jin), Cormorant Garamond — 모두 SIL Open Font License 1.1 (`app/static/fonts/*/LICENSE.txt`).
