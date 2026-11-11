"""테스트용 가짜 OIDC 제공자: 실제 Authlib 경로(토큰 교환 + ID 토큰 서명·nonce·at_hash·PKCE 검증)를 네트워크로 시험."""
import base64, hashlib, time
from authlib.jose import JsonWebKey, jwt
from fastapi import FastAPI, Form, Request
from fastapi.responses import JSONResponse

ISS = "http://127.0.0.1:8765"
key = JsonWebKey.generate_key("RSA", 2048, is_private=True, options={"kid": "k1"})
app = FastAPI()
codes = {}

@app.get("/.well-known/openid-configuration")
def meta():
    return {"issuer": ISS, "authorization_endpoint": ISS + "/auth", "token_endpoint": ISS + "/token",
            "jwks_uri": ISS + "/jwks", "id_token_signing_alg_values_supported": ["RS256"]}

@app.get("/jwks")
def jwks():
    return {"keys": [key.as_dict(is_private=False)]}

@app.post("/register")  # 테스트 편의: code 와 nonce/challenge 를 미리 등록
def register(code: str = Form(...), nonce: str = Form(...), challenge: str = Form(...)):
    codes[code] = (nonce, challenge)
    return {"ok": True}

@app.post("/token")
def token(request: Request, code: str = Form(...), code_verifier: str = Form(""), client_id: str = Form(""), client_secret: str = Form(""),
          grant_type: str = Form(""), redirect_uri: str = Form("")):
    nonce, challenge = codes.pop(code)
    calc = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest()).rstrip(b"=").decode()
    if calc != challenge:
        return JSONResponse({"error": "invalid_grant", "error_description": "PKCE mismatch"}, status_code=400)
    auth = request.headers.get("authorization", "")
    if auth.startswith("Basic "):
        client_secret = base64.b64decode(auth[6:]).decode().split(":", 1)[1]
    if client_secret != "secret":
        return JSONResponse({"error": "invalid_client", "error_description": "bad secret"}, status_code=401)
    at = "access-token-123"
    at_hash = base64.urlsafe_b64encode(hashlib.sha256(at.encode()).digest()[:16]).rstrip(b"=").decode()
    now = int(time.time())
    claims = {"iss": ISS, "aud": "cid", "sub": "g-1", "email": "me@example.com", "email_verified": True,
              "name": "Me", "iat": now, "exp": now + 600, "nonce": nonce, "at_hash": at_hash}
    idt = jwt.encode({"alg": "RS256", "kid": "k1"}, claims, key).decode()
    return {"access_token": at, "token_type": "Bearer", "expires_in": 3600, "id_token": idt, "scope": "openid email profile"}
