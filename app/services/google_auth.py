"""구글 로그인(OIDC) — Authlib 사용. 요청 권한은 openid / email / profile 만.

state·PKCE·nonce 는 세션 쿠키가 아니라 login_requests 테이블에 저장한다(auth.py).
이 모듈은 Authlib 호출 두 가지만 감싼다: 인증 URL 만들기, 콜백에서 토큰 교환 + ID 토큰 검증.
"""
from authlib.integrations.starlette_client import OAuth

from ..config import settings

GOOGLE_METADATA = "https://accounts.google.com/.well-known/openid-configuration"

_oauth = OAuth()
_oauth.register(
    "google",
    client_id=settings.google_client_id,
    client_secret=settings.google_client_secret,
    server_metadata_url=GOOGLE_METADATA,
    client_kwargs={"scope": "openid email profile", "code_challenge_method": "S256"},
)


def is_configured() -> bool:
    return bool(settings.google_client_id and settings.google_client_secret)


async def authorization(redirect_uri: str) -> dict:
    """반환: {url, state, code_verifier, nonce}"""
    return await _oauth.google.create_authorization_url(redirect_uri, prompt="select_account")


async def verify(redirect_uri: str, code: str, code_verifier: str, nonce: str) -> dict:
    """인가 코드를 토큰으로 바꾸고 ID 토큰(서명·발급자·대상·nonce·만료)을 검증한 뒤 클레임을 돌려준다."""
    token = await _oauth.google.fetch_access_token(
        redirect_uri=redirect_uri, code=code, code_verifier=code_verifier
    )
    claims = await _oauth.google.parse_id_token(token, nonce=nonce)
    if claims is None:
        raise ValueError("ID 토큰이 없습니다.")
    return dict(claims)
