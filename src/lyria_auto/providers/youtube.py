from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from pathlib import Path

from ..errors import UploadError
from ..models import Metadata

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]


class YouTubeClient:
    def __init__(self, channel_config: dict, root: str | Path = "."):
        self.config = channel_config
        self.root = Path(root).resolve()
        self.client_secret_file = self._resolve(channel_config["client_secret_file"])
        self.token_file = self._resolve(channel_config["token_file"])
        self.youtube = self._build_service()

    def _resolve(self, value: str | Path) -> Path:
        p = Path(value)
        return p if p.is_absolute() else self.root / p

    @staticmethod
    def authorize(channel_config: dict, root: str | Path = ".") -> Path:
        try:
            from google_auth_oauthlib.flow import InstalledAppFlow
        except ImportError as exc:
            raise UploadError("尚未安裝 Google OAuth 套件") from exc
        root_path = Path(root).resolve()
        secret = Path(channel_config["client_secret_file"])
        token = Path(channel_config["token_file"])
        secret = secret if secret.is_absolute() else root_path / secret
        token = token if token.is_absolute() else root_path / token
        if not secret.exists():
            raise UploadError(f"找不到 OAuth 檔：{secret}")
        flow = InstalledAppFlow.from_client_secrets_file(str(secret), SCOPES)
        creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
        token.parent.mkdir(parents=True, exist_ok=True)
        token.write_text(creds.to_json(), encoding="utf-8")
        return token

    def _credentials(self):
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
        except ImportError as exc:
            raise UploadError("尚未安裝 Google OAuth 套件") from exc
        if not self.token_file.exists():
            raise UploadError(f"找不到 token：{self.token_file}，請先執行 authorize-youtube")
        creds = Credentials.from_authorized_user_file(str(self.token_file), SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            self.token_file.write_text(creds.to_json(), encoding="utf-8")
        if not creds.valid:
            raise UploadError("YouTube OAuth token 無效，請重新授權")
        return creds

    def _build_service(self):
        try:
            from googleapiclient.discovery import build
        except ImportError as exc:
            raise UploadError("尚未安裝 google-api-python-client") from exc
        return build("youtube", "v3", credentials=self._credentials(), cache_discovery=False)

    def channel_identity(self) -> dict:
        response = self.youtube.channels().list(part="snippet", mine=True).execute()
        items = response.get("items", [])
        if not items:
            raise UploadError("OAuth 帳號未連結可用的 YouTube 頻道")
        item = items[0]
        return {"id": item["id"], "title": item["snippet"]["title"]}

    def ensure_playlist(self) -> str | None:
        playlist_cfg = self.config.get("playlist", {})
        playlist_id = str(playlist_cfg.get("id", self.config.get("playlist_id", ""))).strip()
        if playlist_id:
            return playlist_id
        title = str(playlist_cfg.get("title", "")).strip()
        if not title or not playlist_cfg.get("create_if_missing", False):
            return None
        request = self.youtube.playlists().list(part="snippet", mine=True, maxResults=50)
        while request is not None:
            response = request.execute()
            for item in response.get("items", []):
                if item.get("snippet", {}).get("title", "").strip().casefold() == title.casefold():
                    return item["id"]
            request = self.youtube.playlists().list_next(request, response)
        created = self.youtube.playlists().insert(
            part="snippet,status",
            body={
                "snippet": {"title": title, "description": "Automatically managed by Lyria Auto Publisher"},
                "status": {"privacyStatus": str(playlist_cfg.get("privacy_status", "public"))},
            },
        ).execute()
        return created.get("id")

    def upload(
        self,
        video_path: str | Path,
        thumbnail_path: str | Path,
        metadata: Metadata,
        on_video_created: Callable[[str], None] | None = None,
    ) -> str:
        try:
            from googleapiclient.errors import HttpError
            from googleapiclient.http import MediaFileUpload
        except ImportError as exc:
            raise UploadError("尚未安裝 YouTube API 套件") from exc

        status = {
            "privacyStatus": metadata.privacy_status,
            "selfDeclaredMadeForKids": metadata.made_for_kids,
            "containsSyntheticMedia": metadata.contains_synthetic_media,
        }
        if metadata.publish_at:
            status["publishAt"] = metadata.publish_at
        body = {
            "snippet": {
                "title": metadata.title,
                "description": metadata.description,
                "tags": metadata.tags,
                "categoryId": metadata.category_id,
                "defaultLanguage": metadata.default_language,
            },
            "status": status,
        }
        media = MediaFileUpload(str(video_path), chunksize=8 * 1024 * 1024, resumable=True, mimetype="video/mp4")
        request = self.youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
            notifySubscribers=metadata.notify_subscribers,
        )
        response = None
        retries = 0
        while response is None:
            try:
                progress, response = request.next_chunk()
                if progress:
                    logger.info("YouTube 上傳進度 %.1f%%", progress.progress() * 100)
            except HttpError as exc:
                if exc.resp.status not in {500, 502, 503, 504} or retries >= 8:
                    raise UploadError(f"YouTube 上傳失敗：{exc}") from exc
                sleep = min(64, (2 ** retries) + random.random())
                retries += 1
                time.sleep(sleep)
        video_id = response.get("id")
        if not video_id:
            raise UploadError("YouTube 回應未包含 video ID")
        if on_video_created is not None:
            on_video_created(video_id)

        if thumbnail_path:
            suffix = Path(thumbnail_path).suffix.lower()
            mime = "image/png" if suffix == ".png" else "image/jpeg"
            thumb_media = MediaFileUpload(str(thumbnail_path), mimetype=mime)
            self.youtube.thumbnails().set(videoId=video_id, media_body=thumb_media).execute()

        playlist_id = self.ensure_playlist()
        if playlist_id:
            self.youtube.playlistItems().insert(
                part="snippet",
                body={
                    "snippet": {
                        "playlistId": playlist_id,
                        "resourceId": {"kind": "youtube#video", "videoId": video_id},
                    }
                },
            ).execute()
        return video_id
