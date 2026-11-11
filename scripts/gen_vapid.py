"""웹 푸시용 VAPID 키 생성: `python -m scripts.gen_vapid`

출력된 세 줄을 Render(또는 .env / Codespaces Secrets) 환경 변수에 그대로 넣는다.
- 키는 한 번 만들면 바꾸지 않는다. 바꾸면 기존 기기 구독이 모두 무효가 되어 다시 '알림 켜기'를 해야 한다.
- VAPID_PRIVATE_KEY 는 비밀값이다. 채팅·저장소에 올리지 말 것.
"""
import sys

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid
from py_vapid.utils import b64urlencode


def generate() -> tuple[str, str]:
    v = Vapid()
    v.generate_keys()
    private_raw = v.private_key.private_numbers().private_value.to_bytes(32, "big")
    public_raw = v.public_key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return b64urlencode(public_raw), b64urlencode(private_raw)


if __name__ == "__main__":
    subject = sys.argv[1] if len(sys.argv) > 1 else "mailto:you@example.com"
    public, private = generate()
    print(f"VAPID_PUBLIC_KEY={public}")
    print(f"VAPID_PRIVATE_KEY={private}")
    print(f"VAPID_SUBJECT={subject}")
