"""
YouTube OAuth 토큰 최초 발급 스크립트
- 이 스크립트는 로컬 PC에서 딱 한 번만 실행합니다
- 실행하면 브라우저가 열리고 YouTube 로그인 후 토큰을 발급받습니다
- 발급된 토큰 JSON을 GitHub Secrets에 등록하면 됩니다

사전 준비:
  pip install google-auth-oauthlib google-api-python-client
"""

import json
import os
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

# YouTube 업로드 권한
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

def get_token():
    """
    Google Cloud Console에서 다운받은 client_secret.json 파일로
    OAuth 토큰을 발급받습니다
    """
    
    # client_secret.json 파일이 같은 폴더에 있어야 합니다
    if not os.path.exists("client_secret.json"):
        print("❌ client_secret.json 파일이 없습니다!")
        print("   Google Cloud Console → API 및 서비스 → 사용자 인증 정보")
        print("   → OAuth 2.0 클라이언트 ID → JSON 다운로드")
        return
    
    flow = InstalledAppFlow.from_client_secrets_file(
        "client_secret.json",
        SCOPES
    )
    
    # 브라우저 열어서 로그인
    print("🌐 브라우저가 열립니다. YouTube 채널 계정으로 로그인하세요.")
    creds = flow.run_local_server(port=8080)
    
    # 토큰 정보 출력
    token_data = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
    }
    
    # 파일로 저장
    with open("youtube_token.json", "w") as f:
        json.dump(token_data, f, indent=2)
    
    print("\n✅ 토큰 발급 완료!")
    print("📄 youtube_token.json 파일이 생성되었습니다.")
    print("\n⚠️  이 파일 내용을 GitHub Secrets에 등록하세요:")
    print("   Secret 이름: YOUTUBE_TOKEN_JSON")
    print("   Secret 값: (youtube_token.json 파일 내용 전체)")
    print("\n토큰 내용:")
    print(json.dumps(token_data, indent=2))


if __name__ == "__main__":
    get_token()
