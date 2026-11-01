# Study Shift — 교대근무자용 공부 과제 메일러

근무 스케줄(데이/이브닝/나이트/오프)에 맞춰 한 주의 공부 과제를 **규칙 기반으로** 배정하고,
매일 정해진 시각에 그날의 과제를 Gmail로 보내 줍니다. 메일의 [달성] / [미달성] 버튼으로 기록합니다.

- 스택: Python 3.11+ · FastAPI · SQLite(SQLAlchemy) · Jinja2 · APScheduler · Gmail SMTP
- 인증: `.env`의 비밀번호 1개 (메일 버튼 링크는 1회용 토큰이라 로그인 없이 동작)

## 로컬 실행

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # 값 수정 (아래 참고)
uvicorn app.main:app --port 8000
```

브라우저에서 http://localhost:8000 → `.env`의 `APP_PASSWORD`로 로그인.

### 처음 쓰는 순서
1. **근무 유형 · 메일**: 유형별 공부 가능 시간(분)과 발송 시각, 받는 사람 주소
2. **근무 달력**: 날짜별 근무 유형 선택, 공부가 어려운 날은 `제외` 체크
3. **과제**: 과제명, 과목, 소요시간, 주당 횟수
4. **이번 주** → `주간 배정 생성` → `테스트 메일 보내기`
5. 받은 메일에서 [달성]/[미달성] 클릭 → 완료 페이지와 주간 보기에서 확인

### 메일 발송 설정
- `MAIL_BACKEND=console`(기본): 실제로 보내지 않고 `outbox/` 폴더에 HTML로 저장 → 브라우저로 열어 확인
- `MAIL_BACKEND=smtp`: Gmail로 실제 발송
  1. Google 계정 → 보안 → **2단계 인증** 켜기
  2. https://myaccount.google.com/apppasswords 에서 **앱 비밀번호**(16자리) 생성
  3. `.env`에 `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD` 입력

### 휴대폰에서 메일 버튼을 누르려면
버튼 링크는 `BASE_URL` 기준으로 만들어집니다. `localhost`면 같은 PC에서만 열립니다.
- 같은 와이파이: `BASE_URL=http://<PC의 내부 IP>:8000` 그리고 `uvicorn ... --host 0.0.0.0`
- 외부에서도: Cloudflare Tunnel(`cloudflared tunnel --url http://localhost:8000`)이 준 https 주소를 `BASE_URL`에 넣기,
  또는 작은 VPS / Fly.io 등에 배포 (서버가 **계속 켜져 있어야** 정해진 시각에 발송됩니다)

### 테스트
```bash
pytest -q
```

## 규칙 요약

| 항목 | 규칙 |
|---|---|
| 한 주 | 월 ~ 일 |
| 주말(보충일) | 근무 유형이 **오프**인 날 + **일요일** |
| 평일 | 그 외의 날. 공부 가능 시간 안에서 정규 과제만 배정 |
| 우선순위 | 긴급도(남은 횟수 ÷ 남은 가능일) → 이번 주 배정 비율 낮은 순 → 같은 과목 적은 순 → 긴 과제 먼저 |
| 연속 회피 | 어제 한 과제는 피함. 단 오늘 빼면 남은 날이 빠듯할 때는 허용. 끝에 남은 횟수는 여유 시간에 보정 배정 |
| 분할 | 과제는 쪼개지 않음. 안 들어가면 다음 후보/다른 날로 |
| 주말 | 보류 과제(오래된 순) 먼저 → 남은 시간에 정규 과제 |
| 보류 재배정 | 주말 날의 메일 발송 직전에 그날 이후를 다시 계산(기록된 배정은 유지). 보류는 '이번 주' 주말에만 배정 |
| 미기록 | 다음 메일 준비 시점에 지난 날의 미기록 배정은 자동 미달성(보류) |
| 진척도 | 이번 주 정규 과제 달성 횟수 ÷ 주당 횟수 합계 (%) |
| 보충 달성 | 원래 배정이 달성으로 바뀌어 진척도에 반영 |
| 못 넣은 보류 | 다음 주 주말로 이월 |
| 미배정 | 시간이 부족해 못 넣은 횟수는 주간 보기에 경고만 표시 (이월 안 함) |
| 버튼 | 1회용. 한 번 기록하면 같은 과제의 다른 버튼도 무효. 수정은 주간 보기에서 |
| 발송 안 함 | 제외일, 근무 미입력, 공부 시간 0분, 배정 없음 |
| 영어 문장 | 기본 50문장(IELTS 라이팅/스피킹용, 뜻 포함) 순환. `영어 문장` 화면에서 수정 가능 |

## 폴더 구조
```
app/
  main.py  config.py  db.py  models.py  auth.py  scheduler.py  clock.py  seed.py  templating.py
  routers/   weeks.py  settings.py  calendar.py  tasks.py  sentences.py  actions.py
  services/  allocator.py(배분 로직)  daily.py(메일 준비/발송)  mailer.py  progress.py
  templates/ 화면 템플릿, email/daily.html(Gmail용 table+인라인 CSS)
  static/style.css
tests/       test_allocator.py  test_flow.py
```
