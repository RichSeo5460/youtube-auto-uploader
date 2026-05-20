"""
YouTube 자동 업로드 스크립트 v4
- 구글 시트 G열에 날짜 있으면 예약 공개, 없으면 즉시 공개
- F열 채널 번호에 따라 해당 채널로 업로드
- 컬럼: A=제목, B=대본, C=영상url, D=dropbox_url, E=업로드여부, F=유튜브채널, G=예약날짜
- 상태: 업로드전 → 업로드완료
"""

# ──────────────────────────────────────────
# 채널 번호 → 채널 ID 매핑
# ──────────────────────────────────────────
CHANNEL_MAP = {
    "1": "UCMujLGISA9sRh0ki9H5xXLg",   # 모먼트랩
    "2": "UCuyhcW0c4QCcCRtA5oeMn1w",   # 데일리인사이트
    "3": "UCqr08lng11l-14li4vaLc3g",   # 생활정보TV
}

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

KST = timezone(timedelta(hours=9))

# ──────────────────────────────────────────
# 환경변수 로드
# ──────────────────────────────────────────
DROPBOX_TOKEN      = os.environ["DROPBOX_TOKEN"]
GOOGLE_SHEET_ID    = os.environ["GOOGLE_SHEET_ID"]
GOOGLE_SHEET_NAME  = os.environ.get("GOOGLE_SHEET_NAME", "숏츠시트")
YOUTUBE_TOKEN_JSON = os.environ["YOUTUBE_TOKEN_JSON"]
GOOGLE_SA_JSON     = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]


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
    now_kst = datetime.now(KST)
    today_str = now_kst.strftime("%Y-%m-%d")
    all_rows = sheet.get_all_values()

    # 예약 날짜가 오늘인 행 먼저
    for i, row in enumerate(all_rows[1:], start=2):
        while len(row) < 7:
            row.append("")
        status    = row[4].strip()
        scheduled = row[6].strip()
        if status == "업로드전" and scheduled:
            try:
                if scheduled[:10] == today_str:
                    return i, row, scheduled
            except:
                pass

    # 예약 없는 행 (즉시 공개)
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
    sheet.update_cell(row_num, 8, f"https://youtube.com/shorts/{video_id}")
    print(f"✅ 시트 업데이트: {row_num}행 → 업로드완료")


# ──────────────────────────────────────────
# 드롭박스 다운로드
# ──────────────────────────────────────────
def download_from_dropbox_url(dropbox_url):
    direct_url = re.sub(r'&st=[^&]*', '', dropbox_url)
    direct_url = re.sub(r'\?st=[^&]*&', '?', direct_url)
    direct_url = re.sub(r'\?st=[^&]*$', '', direct_url)

    if "dl=0" in direct_url:
        direct_url = direct_url.replace("dl=0", "dl=1")
    elif "dl=1" not in direct_url:
        direct_url += "&dl=1" if "?" in direct_url else "?dl=1"

    print(f"📥 드롭박스 다운로드 중... {direct_url[:70]}...")
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


def upload_to_youtube(service, video_path, title, description, scheduled="", channel_num="1"):
    # 채널 ID 결정
    channel_id = CHANNEL_MAP.get(str(channel_num).strip(), CHANNEL_MAP["1"])
    channel_name = {v: k for k, v in {
        "모먼트랩": CHANNEL_MAP["1"],
        "데일리인사이트": CHANNEL_MAP["2"],
        "생활정보TV": CHANNEL_MAP["3"],
    }.items()}.get(channel_id, f"채널{channel_num}")
    print(f"📺 업로드 채널: {channel_name} ({channel_id})")

    # 해시태그 추출
    tags = []
    for word in description.split():
        if word.startswith("#"):
            tags.append(word.lstrip("#"))
    if "shorts" not in [t.lower() for t in tags]:
        tags.insert(0, "shorts")
    if "#shorts" not in description.lower():
        description += "\n\n#shorts"

    # 공개 상태
    if scheduled:
        try:
            if len(scheduled) == 10:
                scheduled += " 09:00"
            sched_dt = datetime.strptime(scheduled, "%Y-%m-%d %H:%M")
            sched_kst = sched_dt.replace(tzinfo=KST)
            sched_utc = sched_kst.astimezone(timezone.utc)
            publish_at = sched_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")
            privacy = "private"
            print(f"⏰ 예약: {scheduled} KST")
        except Exception as e:
            print(f"⚠️ 날짜 파싱 실패({e}), 즉시공개로 전환")
            publish_at = None
            privacy = "public"
    else:
        publish_at = None
        privacy = "public"
        print("🚀 즉시 공개")

    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": tags[:500],
            "categoryId": "22",
            "defaultLanguage": "ko",
            "channelId": channel_id,  # 채널 지정
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
    print(f"✅ 완료! https://youtube.com/shorts/{video_id}")
    return video_id


# ──────────────────────────────────────────
# 메인
# ──────────────────────────────────────────
def main():
    print("=" * 50)
    now_kst = datetime.now(KST)
    print(f"🎬 YouTube 자동 업로드 v4 ({now_kst.strftime('%Y-%m-%d %H:%M KST')})")
    print("=" * 50)

    sheet = get_sheet()
    row_num, row, scheduled = get_next_video(sheet)

    if row is None:
        print("⚠️  업로드할 영상이 없습니다.")
        return

    title       = row[0].strip()
    script      = row[1].strip()
    dropbox_url = row[3].strip()
    channel_num = row[5].strip() if len(row) > 5 else "1"  # F열

    # 채널명 출력
    channel_names = {"1": "모먼트랩", "2": "데일리인사이트", "3": "생활정보TV"}
    print(f"\n📋 업로드 정보:")
    print(f"   제목: {title}")
    print(f"   채널: {channel_names.get(channel_num, channel_num)}번")
    print(f"   예약: {scheduled if scheduled else '즉시공개'}")

    local_path = download_from_dropbox_url(dropbox_url)

    try:
        yt_service = get_youtube_service()
        video_id = upload_to_youtube(
            yt_service, local_path, title, script, scheduled, channel_num
        )
        mark_as_done(sheet, row_num, video_id)
        print(f"\n🎉 완료!")
    finally:
        if os.path.exists(local_path):
            os.remove(local_path)

if __name__ == "__main__":
    main()
