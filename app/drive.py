"""Google Drive: resolve a folder/file link and download the images in it."""

import json
import re
from dataclasses import dataclass

import requests

API = "https://www.googleapis.com/drive/v3"
FOLDER_MIME = "application/vnd.google-apps.folder"
SHORTCUT_MIME = "application/vnd.google-apps.shortcut"
# Formats Meta's /adimages endpoint accepts.
SUPPORTED_IMAGE_MIMES = {"image/jpeg", "image/png", "image/gif", "image/bmp", "image/tiff"}


class DriveError(RuntimeError):
    pass


@dataclass
class DriveFile:
    id: str
    name: str
    mime_type: str


def parse_drive_id(url: str) -> str:
    for pattern in (r"/folders/([\w-]+)", r"/file/d/([\w-]+)", r"/d/([\w-]+)", r"[?&]id=([\w-]+)"):
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    raise DriveError(f"Could not find a file or folder ID in Drive link: {url}")


def natural_key(name: str):
    """Sort "Image 2" before "Image 10"."""
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", name)]


class DriveClient:
    def __init__(
        self,
        service_account_file: str | None = None,
        service_account_json: str | None = None,
        api_key: str | None = None,
        session: requests.Session | None = None,
    ):
        self.api_key = api_key
        if session is not None:
            self.session = session
        elif service_account_file or service_account_json:
            from google.auth.transport.requests import AuthorizedSession
            from google.oauth2 import service_account

            scopes = ["https://www.googleapis.com/auth/drive.readonly"]
            if service_account_json:
                creds = service_account.Credentials.from_service_account_info(
                    json.loads(service_account_json), scopes=scopes
                )
            else:
                creds = service_account.Credentials.from_service_account_file(service_account_file, scopes=scopes)
            self.session = AuthorizedSession(creds)
        else:
            self.session = requests.Session()

    def _get(self, path: str, params: dict | None = None, raw: bool = False):
        params = {"supportsAllDrives": "true", **(params or {})}
        if self.api_key:
            params["key"] = self.api_key
        resp = self.session.get(f"{API}{path}", params=params, timeout=120)
        if resp.status_code >= 400:
            hint = " Is the folder shared with the service account (or 'anyone with the link')?" if resp.status_code in (403, 404) else ""
            raise DriveError(f"Drive request failed ({resp.status_code}): {resp.text[:300]}{hint}")
        return resp.content if raw else resp.json()

    def _metadata(self, file_id: str) -> dict:
        return self._get(f"/files/{file_id}", {"fields": "id,name,mimeType,shortcutDetails"})

    def list_images(self, url: str) -> tuple[list[DriveFile], list[str]]:
        """Return (images sorted by name, names of skipped non-image files)."""
        root = self._metadata(parse_drive_id(url))
        if root["mimeType"] != FOLDER_MIME:
            entries = [root]
        else:
            entries, token = [], None
            while True:
                params = {
                    "q": f"'{root['id']}' in parents and trashed = false",
                    "fields": "nextPageToken, files(id,name,mimeType,shortcutDetails)",
                    "includeItemsFromAllDrives": "true",
                    "pageSize": "1000",
                }
                if token:
                    params["pageToken"] = token
                page = self._get("/files", params)
                entries.extend(page.get("files", []))
                token = page.get("nextPageToken")
                if not token:
                    break

        images, skipped = [], []
        for entry in entries:
            file_id, mime = entry["id"], entry["mimeType"]
            if mime == SHORTCUT_MIME:
                details = entry.get("shortcutDetails") or {}
                file_id, mime = details.get("targetId", file_id), details.get("targetMimeType", mime)
            if mime in SUPPORTED_IMAGE_MIMES:
                images.append(DriveFile(id=file_id, name=entry["name"], mime_type=mime))
            elif mime != FOLDER_MIME:
                skipped.append(entry["name"])
        images.sort(key=lambda f: natural_key(f.name))
        return images, skipped

    def download(self, file: DriveFile) -> bytes:
        return self._get(f"/files/{file.id}", {"alt": "media"}, raw=True)
