#!/usr/bin/env bash
# Codespaces가 시작될 때 서버를 백그라운드로 켠다. 로그: /tmp/server.log
cd "$(dirname "$0")/.."
if curl -s -o /dev/null http://localhost:8000/login; then
  echo "서버가 이미 실행 중입니다."
  exit 0
fi
setsid nohup python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 > /tmp/server.log 2>&1 < /dev/null &
sleep 3
echo "서버를 시작했습니다. 아래 PORTS 탭에서 afterwork(8000) 주소를 여세요."
