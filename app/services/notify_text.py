"""알림 문구 모음. 문구를 바꿀 때는 이 파일만 고치면 된다.

{count} = 과제 수, {minutes} = 총 분, {m}/{d}/{wd} = 월/일/요일.
푸시 본문은 짧게(200바이트 미만) 유지한다. 자세한 내용은 알림을 눌러 연 /today 화면에서 보여준다.
"""

APP_TITLE = "afterwork"
TEST_SUFFIX = " (테스트)"

# 정기 알림 (근무 유형의 알림 시각)
DAILY_PUSH_BODY = "오늘의 공부 과제 {count}개 · 총 {minutes}분"
DAILY_PUSH_EMPTY = "오늘 배정된 과제가 없습니다."
DAILY_MAIL_SUBJECT = "[afterwork] {m}/{d}({wd}) 오늘의 과제 {count}개"
DAILY_MAIL_HEADING = "오늘의 과제"

# 재알림 (근무 유형의 재알림 시각, 아직 기록하지 않은 과제가 있을 때) — 임시 문구
REMIND_PUSH_BODY = "오늘 남은 과제 {count}개"
REMIND_MAIL_SUBJECT = "[afterwork] 오늘 남은 과제 {count}개"
REMIND_MAIL_HEADING = "아직 기록하지 않은 과제"
