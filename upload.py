"""
YouTube 자동 업로드 스크립트 v2
- 기창님 젠스파크 관리시트 구조에 맞게 커스터마이징
- 컬럼: A=제목, B=대본, C=영상url, D=dropbox_url, E=업로드여부, F=유튜브채널
- 상태: 업로드전 → 업로드완료
"""

import os
import json
import tempfile
import requests
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials as OAuthCredentials
from google.auth.transport.requests import Request


# ──────────────────────────────────────────
# 환경변수 로드 (GitHub Secrets)
# ──────────────────────────────────────────
DROPBOX_TOKEN        = os.environ["DROPBOX_TOKEN"]
GOOGLE_SHEET_ID      = os.environ["GOOGLE_SHEET_ID"]
GOOGLE_SHEET_NAME    = os.environ.get("GOOGLE_SHEET_NAME", "쇼츠시트")
YOUTUBE_TOKEN_JSON   = os.environ["YOUTUBE_TOKEN_JSON"]
GOOGLE_SA_JSON       = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]


# ──────────────────────────────────────────
# 구글 시트 연결
# ──────────────────────────────────────────
def get_sheet():
    creds_dict = json.loads(GOOGLE_SA_JSON)
    scopes = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(GOOGLE_SHEET_ID).worksheet(GOOGLE_SHEET_NAME)
    return sheet


def get_next_video(sheet):
    """
    E열(업로드여부)이 '업로드전'인 첫 번째 행 반환
    """
    all_rows = sheet.get_all_values()
    for i, row in enumerate(all_rows[1:], start=2):  # 1행은 헤더
        # 열이 부족할 경우 빈 값으로 채움
        while len(row) < 6:
            row.append("")
        title       = row[0].strip()  # A열: 제목
        script      = row[1].strip()  # B열: 대본
        dropbox_url = row[3].strip()  # D열: dropbox_url
        status      = row[4].strip()  # E열: 업로드여부

        if status == "업로드전" and dropbox_url:
            return i, {
                "title":       title,
                "script":      script,
                "dropbox_url": dropbox_url,
            }
    return None, None


def mark_as_done(sheet, row_num, video_id):
    """업로드 완료 후 E열 → 업로드완료, G열에 YouTube 링크 기록"""
    sheet.update_cell(row_num, 5, "업로드완료")
    sheet.update_cell(row_num, 7, f"https://youtube.com/shorts/{video_id}")
    print(f"✅ 시트 업데이트: {row_num}행 → 업로드완료")


# ──────────────────────────────────────────
# 드롭박스 URL로 파일 다운로드
# ──────────────────────────────────────────
def download_from_dropbox_url(dropbox_url):
    """
    드롭박스 공유 URL (scl/fi 형식) 직접 다운로드
    - rlkey 파라미터가 있는 공유 링크는 토큰 없이 직접 다운로드 가능
    - st= 파라미터는 세션 토큰이라 제거해야 함
    """
    import re

    # st= 파라미터 제거 (세션 토큰, 있으면 400 에러 발생)
    direct_url = re.sub(r'&st=[^&]*', '', dropbox_url)
    direct_url = re.sub(r'\?st=[^&]*&', '?', direct_url)
    direct_url = re.sub(r'\?st=[^&]*$', '', direct_url)

    # dl=1 추가 (직접 다운로드 강제)
    if "dl=0" in direct_url:
        direct_url = direct_url.replace("dl=0", "dl=1")
    elif "dl=1" not in direct_url:
        direct_url += "&dl=1" if "?" in direct_url else "?dl=1"

    print(f"📥 드롭박스 다운로드 중...")
    print(f"   URL: {direct_url[:80]}...")

    # 공유 링크는 토큰 없이 직접 다운로드
    headers = {"User-Agent": "Mozilla/5.0"}
    response = requests.get(direct_url, headers=headers, stream=True, allow_redirects=True)
    response.raise_for_status()

    tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    total = 0
    for chunk in response.iter_content(chunk_size=1024 * 1024):
        tmp.write(chunk)
        total += len(chunk)
    tmp.flush()
    tmp.close()

    print(f"✅ 다운로드 완료: {total / 1024 / 1024:.1f}MB → {tmp.name}")
    return tmp.name


# ──────────────────────────────────────────
# YouTube 업로드
# ──────────────────────────────────────────
def get_youtube_service():
    token_data = json.loads(YOUTUBE_TOKEN_JSON)
    creds = OAuthCredentials(
        token=token_data.get("token"),
        refresh_token=token_data["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=token_data["client_id"],
        client_secret=token_data["client_secret"],
    )
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return build("youtube", "v3", credentials=creds)


def upload_to_youtube(service, video_path, title, description):
    # 설명에서 해시태그 추출
    lines = description.split("\n")
    tags = []
    for line in lines:
        words = line.split()
        for word in words:
            if word.startswith("#"):
                tags.append(word.lstrip("#"))

    # #shorts 태그 필수 포함
    if "shorts" not in [t.lower() for t in tags]:
        tags.insert(0, "shorts")

    # 설명 끝에 #shorts 추가 (알고리즘 인식용)
    if "#shorts" not in description.lower():
        description = description + "\n\n#shorts"

    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": tags[:500],
            "categoryId": "22",
            "defaultLanguage": "ko",
        },
        "status": {
            "privacyStatus": "public",  # 즉시 공개 ← 핵심!
            "selfDeclaredMadeForKids": False,
        }
    }

    media = MediaFileUpload(
        video_path,
        mimetype="video/mp4",
        resumable=True,
        chunksize=1024 * 1024 * 5
    )

    print(f"🚀 YouTube 업로드 시작: {title}")
    request = service.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media
    )

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"   업로드 중... {int(status.progress() * 100)}%")

    video_id = response["id"]
    print(f"✅ 업로드 완료! https://youtube.com/shorts/{video_id}")
    return video_id


# ──────────────────────────────────────────
# 메인
# ──────────────────────────────────────────
def main():
    print("=" * 50)
    print("🎬 YouTube 자동 업로드 시작 (젠스파크 관리시트 v2)")
    print("=" * 50)

    # 1. 구글 시트 연결
    print("\n📊 구글 시트 연결 중...")
    sheet = get_sheet()

    # 2. 업로드전 영상 찾기
    row_num, video_data = get_next_video(sheet)

    if video_data is None:
        print("⚠️  '업로드전' 상태인 영상이 없습니다.")
        return

    print(f"\n📋 업로드할 영상:")
    print(f"   제목: {video_data['title']}")
    print(f"   드롭박스 URL: {video_data['dropbox_url'][:60]}...")

    # 3. 드롭박스에서 다운로드
    local_path = download_from_dropbox_url(video_data["dropbox_url"])

    try:
        # 4. YouTube 업로드
        yt_service = get_youtube_service()
        video_id = upload_to_youtube(
            yt_service,
            local_path,
            video_data["title"],
            video_data["script"]
        )

        # 5. 시트 업데이트
        mark_as_done(sheet, row_num, video_id)

        print(f"\n🎉 완료! https://youtube.com/shorts/{video_id}")

    finally:
        if os.path.exists(local_path):
            os.remove(local_path)
            print(f"🗑️  임시파일 삭제 완료")


if __name__ == "__main__":
    main()
