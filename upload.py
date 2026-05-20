"""
YouTube 자동 업로드 스크립트 v3
- 구글 시트 G열에 날짜 있으면 예약 공개, 없으면 즉시 공개
- 컬럼: A=제목, B=대본, C=영상url, D=dropbox_url, E=업로드여부, F=유튜브채널, G=예약날짜
- 상태: 업로드전 → 업로드완료
"""

import os
import json
import re
import tempfile
import requests
import gspread
from datetime import datetime, timezone, timedelta
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials as OAuthCredentials
from google.auth.transport.requests import Request

# 한국시간 (KST = UTC+9)
KST = timezone(timedelta(hours=9))

# ──────────────────────────────────────────
# 환경변수 로드
# ──────────────────────────────────────────
DROPBOX_TOKEN     = os.environ["DROPBOX_TOKEN"]
GOOGLE_SHEET_ID   = os.environ["GOOGLE_SHEET_ID"]
GOOGLE_SHEET_NAME = os.environ.get("GOOGLE_SHEET_NAME", "숏츠시트")
YOUTUBE_TOKEN_JSON = os.environ["YOUTUBE_TOKEN_JSON"]
GOOGLE_SA_JSON    = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]


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
    return client.open_by_key(GOOGLE_SHEET_ID).worksheet(GOOGLE_SHEET_NAME)


def get_next_video(sheet):
    """
    E열이 '업로드전'인 행 중:
    - G열(예약날짜)이 있으면 → 오늘 날짜 일치하는 것만
    - G열이 비어있으면 → 첫 번째 행 즉시 업로드
    """
    now_kst = datetime.now(KST)
    today_str = now_kst.strftime("%Y-%m-%d")
    all_rows = sheet.get_all_values()

    # 예약 날짜가 오늘인 행 먼저 탐색
    for i, row in enumerate(all_rows[1:], start=2):
        while len(row) < 7:
            row.append("")
        status       = row[4].strip()  # E열
        scheduled    = row[6].strip()  # G열

        if status == "업로드전" and scheduled:
            # 날짜 파싱 (2026-05-22 또는 2026-05-22 19:00)
            try:
                sched_date = scheduled[:10]  # YYYY-MM-DD 부분만
                if sched_date == today_str:
                    return i, row, scheduled
            except:
                pass

    # 예약 없는 행 탐색 (G열 비어있는 것)
    for i, row in enumerate(all_rows[1:], start=2):
        while len(row) < 7:
            row.append("")
        status    = row[4].strip()
        scheduled = row[6].strip()

        if status == "업로드전" and not scheduled:
            return i, row, ""

    return None, None, None


def mark_as_done(sheet, row_num, video_id):
    sheet.update_cell(row_num, 5, "업로드완료")
    sheet.update_cell(row_num, 7, f"https://youtube.com/shorts/{video_id}")
    print(f"✅ 시트 업데이트: {row_num}행 → 업로드완료")


# ──────────────────────────────────────────
# 드롭박스 다운로드
# ──────────────────────────────────────────
def download_from_dropbox_url(dropbox_url):
    """
    드롭박스 공유 URL (scl/fi 형식) 직접 다운로드
    - st= 파라미터 제거 (세션 토큰, 있으면 400 에러)
    """
    # st= 파라미터 제거
    direct_url = re.sub(r'&st=[^&]*', '', dropbox_url)
    direct_url = re.sub(r'\?st=[^&]*&', '?', direct_url)
    direct_url = re.sub(r'\?st=[^&]*$', '', direct_url)

    # dl=1 추가
    if "dl=0" in direct_url:
        direct_url = direct_url.replace("dl=0", "dl=1")
    elif "dl=1" not in direct_url:
        direct_url += "&dl=1" if "?" in direct_url else "?dl=1"

    print(f"📥 드롭박스 다운로드 중...")
    print(f"   URL: {direct_url[:80]}...")

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

    print(f"✅ 다운로드 완료: {total / 1024 / 1024:.1f}MB")
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


def upload_to_youtube(service, video_path, title, description, scheduled=""):
    """
    scheduled: "2026-05-22 19:00" 형식이면 예약공개, 없으면 즉시공개
    """
    # 해시태그 추출
    tags = []
    for word in description.split():
        if word.startswith("#"):
            tags.append(word.lstrip("#"))
    if "shorts" not in [t.lower() for t in tags]:
        tags.insert(0, "shorts")
    if "#shorts" not in description.lower():
        description += "\n\n#shorts"

    # 공개 상태 결정
    if scheduled:
        try:
            # 예약 시간 파싱 (KST → UTC ISO 형식)
            if len(scheduled) == 10:
                scheduled += " 09:00"
            sched_dt = datetime.strptime(scheduled, "%Y-%m-%d %H:%M")
            sched_kst = KST.localize(sched_dt) if hasattr(KST, 'localize') else sched_dt.replace(tzinfo=KST)
            sched_utc = sched_kst.astimezone(timezone.utc)
            publish_at = sched_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")
            privacy = "private"  # 예약은 private + publishAt
            print(f"⏰ 예약 공개: {scheduled} KST → {publish_at}")
        except Exception as e:
            print(f"⚠️ 날짜 파싱 실패 ({e}), 즉시 공개로 전환")
            publish_at = None
            privacy = "public"
    else:
        publish_at = None
        privacy = "public"
        print("🚀 즉시 공개 업로드")

    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": tags[:500],
            "categoryId": "22",
            "defaultLanguage": "ko",
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
        }
    }

    if publish_at:
        body["status"]["publishAt"] = publish_at

    media = MediaFileUpload(
        video_path,
        mimetype="video/mp4",
        resumable=True,
        chunksize=1024 * 1024 * 5
    )

    print(f"🎬 업로드 시작: {title}")
    request = service.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media
    )

    response = None
    while response is None:
        status_obj, response = request.next_chunk()
        if status_obj:
            print(f"   {int(status_obj.progress() * 100)}%...")

    video_id = response["id"]
    if publish_at:
        print(f"✅ 예약 업로드 완료! {scheduled} KST에 공개됩니다")
    else:
        print(f"✅ 즉시 업로드 완료! https://youtube.com/shorts/{video_id}")
    return video_id


# ──────────────────────────────────────────
# 메인
# ──────────────────────────────────────────
def main():
    print("=" * 50)
    now_kst = datetime.now(KST)
    print(f"🎬 YouTube 자동 업로드 v3 ({now_kst.strftime('%Y-%m-%d %H:%M KST')})")
    print("=" * 50)

    sheet = get_sheet()
    row_num, row, scheduled = get_next_video(sheet)

    if row is None:
        print("⚠️  업로드할 영상이 없습니다.")
        return

    title       = row[0].strip()
    script      = row[1].strip()
    dropbox_url = row[3].strip()

    print(f"\n📋 업로드할 영상:")
    print(f"   제목: {title}")
    print(f"   예약: {scheduled if scheduled else '즉시공개'}")

    local_path = download_from_dropbox_url(dropbox_url)

    try:
        yt_service = get_youtube_service()
        video_id = upload_to_youtube(yt_service, local_path, title, script, scheduled)
        mark_as_done(sheet, row_num, video_id)
        print(f"\n🎉 완료!")
    finally:
        if os.path.exists(local_path):
            os.remove(local_path)


if __name__ == "__main__":
    main()
