"""
Storage & Cloud Archival Manager for DCC Institutional Trading Environment.
Enforces rolling 7-day local retention to keep VPS disk usage lean,
with automated archival to Google Drive and Telegram file backup.
"""

from __future__ import annotations

import base64
import glob
import io
import json
import mimetypes
import os
import shutil
import sys
import time
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple, Any


class StorageManager:
    def __init__(
        self,
        audit_dir: str = "reports/daily_audits",
        retention_days: int = 7,
        config_file: str = "bot_accounts_config.json",
    ):
        self.project_root = os.path.dirname(os.path.abspath(__file__))
        self.audit_dir = os.path.join(self.project_root, audit_dir)
        self.retention_days = retention_days
        self.config_file = os.path.join(self.project_root, config_file)
        self._ensure_dirs()

    def _ensure_dirs(self):
        os.makedirs(self.audit_dir, exist_ok=True)

    def _load_config(self) -> Dict[str, Any]:
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[StorageManager] Warn: Could not read config {self.config_file}: {e}")
        return {}

    def save_daily_report(self, date_str: str, report_dict: Dict[str, Any], markdown_summary: Optional[str] = None) -> Tuple[str, Optional[str]]:
        """
        Saves daily JSON scorecard and optional Markdown summary.
        Returns paths to saved files: (json_path, md_path).
        """
        self._ensure_dirs()
        json_filename = f"audit_reconciliation_{date_str}.json"
        json_path = os.path.join(self.audit_dir, json_filename)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(report_dict, f, indent=2, default=str)

        md_path = None
        if markdown_summary:
            md_filename = f"audit_reconciliation_{date_str}.md"
            md_path = os.path.join(self.audit_dir, md_filename)
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(markdown_summary)

        # Also write a latest symlink/copy for instant dashboard access
        latest_json = os.path.join(self.audit_dir, "audit_reconciliation_latest.json")
        try:
            shutil.copyfile(json_path, latest_json)
        except Exception:
            pass

        return json_path, md_path

    # -------------------------------------------------------------------------
    # Cloud Archival: Google Drive
    # -------------------------------------------------------------------------
    def archive_to_google_drive(
        self,
        file_path: str,
        folder_name: str = "bot backtest",
        subfolder_date: Optional[str] = None
    ) -> Tuple[bool, str]:
        """
        Uploads a file to Google Drive under folder_name / subfolder_date (e.g. 'bot backtest/2026-09-14/').
        Supports:
        1. Google Drive Webhook URL (Google Apps Script)
        2. Google Service Account credentials JSON
        """
        if not os.path.exists(file_path):
            return False, f"File not found: {file_path}"

        filename = os.path.basename(file_path)
        cfg = self._load_config()

        # Determine subfolder date if not provided
        if not subfolder_date:
            import re
            m = re.search(r"20\d{2}[-_]?\d{2}[-_]?\d{2}", filename)
            if m:
                raw_d = m.group(0).replace("-", "").replace("_", "")
                if len(raw_d) == 8:
                    subfolder_date = f"{raw_d[:4]}-{raw_d[4:6]}-{raw_d[6:8]}"
            if not subfolder_date:
                subfolder_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Method 1: Google Apps Script / Drive Webhook Endpoint
        webhook_url = cfg.get("google_drive_webhook_url", "").strip() or os.getenv("GDRIVE_WEBHOOK_URL", "").strip()
        if not webhook_url:
            for acc_cfg in cfg.values():
                if isinstance(acc_cfg, dict):
                    if "google_drive_webhook_url" in acc_cfg:
                        webhook_url = acc_cfg["google_drive_webhook_url"].strip()
                        break
                    elif "storage" in acc_cfg and isinstance(acc_cfg["storage"], dict):
                        webhook_url = acc_cfg["storage"].get("google_drive_webhook_url", "").strip()
                        break
        if webhook_url:
            try:
                with open(file_path, "rb") as f:
                    content_b64 = base64.b64encode(f.read()).decode("utf-8")
                
                payload = {
                    "filename": filename,
                    "folder": folder_name,
                    "subfolder": subfolder_date,
                    "content_b64": content_b64,
                    "uploaded_at": datetime.now(timezone.utc).isoformat()
                }
                data = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(
                    webhook_url,
                    data=data,
                    headers={"Content-Type": "application/json"},
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=30) as resp:
                    if resp.status in (200, 201):
                        return True, f"Uploaded {filename} to Google Drive under '{folder_name}/{subfolder_date}/'."
                    return False, f"Google Drive Webhook HTTP {resp.status}"
            except Exception as e:
                return False, f"Google Drive Webhook error: {e}"

        # Method 2: Google Service Account credentials if installed
        creds_file = cfg.get("google_drive_credentials_file", "google_drive_credentials.json")
        creds_path = os.path.join(self.project_root, creds_file) if not os.path.isabs(creds_file) else creds_file
        if os.path.exists(creds_path):
            try:
                from google.oauth2 import service_account
                from googleapiclient.discovery import build
                from googleapiclient.http import MediaFileUpload

                SCOPES = ['https://www.googleapis.com/auth/drive.file']
                creds = service_account.Credentials.from_service_account_file(creds_path, scopes=SCOPES)
                service = build('drive', 'v3', credentials=creds)

                # 1. Search or create root folder (e.g. 'bot backtest')
                query = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
                results = service.files().list(q=query, spaces='drive', fields='files(id, name)').execute()
                items = results.get('files', [])
                if items:
                    root_folder_id = items[0]['id']
                else:
                    folder_metadata = {
                        'name': folder_name,
                        'mimeType': 'application/vnd.google-apps.folder'
                    }
                    folder = service.files().create(body=folder_metadata, fields='id').execute()
                    root_folder_id = folder.get('id')

                # 2. Search or create subfolder by date (e.g. '2026-09-14') inside root folder
                sub_query = f"name='{subfolder_date}' and '{root_folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
                sub_results = service.files().list(q=sub_query, spaces='drive', fields='files(id, name)').execute()
                sub_items = sub_results.get('files', [])
                if sub_items:
                    target_folder_id = sub_items[0]['id']
                else:
                    sub_metadata = {
                        'name': subfolder_date,
                        'parents': [root_folder_id],
                        'mimeType': 'application/vnd.google-apps.folder'
                    }
                    sub_folder = service.files().create(body=sub_metadata, fields='id').execute()
                    target_folder_id = sub_folder.get('id')

                # 3. Upload file inside target subfolder
                file_metadata = {
                    'name': filename,
                    'parents': [target_folder_id]
                }
                media = MediaFileUpload(file_path, resumable=True)
                uploaded = service.files().create(body=file_metadata, media_body=media, fields='id').execute()
                return True, f"Uploaded {filename} to Google Drive: '{folder_name}/{subfolder_date}/' (ID: {uploaded.get('id')})"
            except ImportError:
                return False, "Google API client not installed (run: pip install google-api-python-client google-auth)"
            except Exception as e:
                return False, f"Google Drive API error: {e}"

        return False, "No Google Drive credentials or Webhook URL configured in bot_accounts_config.json."

    # -------------------------------------------------------------------------
    # Zero-Setup Cloud Backup: Telegram Document
    # -------------------------------------------------------------------------
    def backup_to_telegram(self, file_path: str, caption: Optional[str] = None) -> Tuple[bool, str]:
        """
        Sends the file directly as a document to Telegram chat/channel.
        Ensures a permanent remote backup even without Google Drive API keys.
        """
        if not os.path.exists(file_path):
            return False, f"File not found: {file_path}"

        cfg = self._load_config()
        token = ""
        chat_id = ""

        # Extract telegram credentials from any configured account
        for acc_cfg in cfg.values():
            if isinstance(acc_cfg, dict):
                t = acc_cfg.get("telegram_bot_token", "").strip()
                c = acc_cfg.get("telegram_chat_id", "").strip()
                if t and c:
                    token, chat_id = t, c
                    break

        if not token or not chat_id:
            return False, "No Telegram Bot Token or Chat ID configured."

        filename = os.path.basename(file_path)
        url = f"https://api.telegram.org/bot{token}/sendDocument"

        try:
            with open(file_path, "rb") as f:
                file_bytes = f.read()

            boundary = "----WebKitFormBoundary" + os.urandom(16).hex()
            body = io.BytesIO()

            # Chat ID field
            body.write(f"--{boundary}\r\n".encode("utf-8"))
            body.write(b'Content-Disposition: form-data; name="chat_id"\r\n\r\n')
            body.write(f"{chat_id}\r\n".encode("utf-8"))

            # Caption field
            if caption:
                body.write(f"--{boundary}\r\n".encode("utf-8"))
                body.write(b'Content-Disposition: form-data; name="caption"\r\n\r\n')
                body.write(f"{caption}\r\n".encode("utf-8"))

            # Document file field
            mime_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
            body.write(f"--{boundary}\r\n".encode("utf-8"))
            body.write(f'Content-Disposition: form-data; name="document"; filename="{filename}"\r\n'.encode("utf-8"))
            body.write(f"Content-Type: {mime_type}\r\n\r\n".encode("utf-8"))
            body.write(file_bytes)
            body.write(b"\r\n")

            body.write(f"--{boundary}--\r\n".encode("utf-8"))
            data = body.getvalue()

            req = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                if resp.status == 200:
                    return True, f"Sent {filename} to Telegram cloud backup."
                return False, f"Telegram HTTP {resp.status}"
        except Exception as e:
            return False, f"Telegram sendDocument error: {e}"

    # -------------------------------------------------------------------------
    # 7-Day Rolling Pruning Policy
    # -------------------------------------------------------------------------
    def prune_aged_reports(self, archive_before_delete: bool = True) -> List[str]:
        """
        Finds files in audit_dir older than self.retention_days (default 7 days).
        Optionally archives them to Google Drive and Telegram before deleting.
        Returns list of deleted file paths.
        """
        self._ensure_dirs()
        cutoff_time = time.time() - (self.retention_days * 86400)
        deleted_files = []

        patterns = [
            os.path.join(self.audit_dir, "audit_reconciliation_2*.json"),
            os.path.join(self.audit_dir, "audit_reconciliation_2*.md"),
        ]

        for pat in patterns:
            for fpath in glob.glob(pat):
                try:
                    mtime = os.path.getmtime(fpath)
                    if mtime < cutoff_time:
                        fname = os.path.basename(fpath)
                        print(f"[StorageManager] File aged > {self.retention_days} days: {fname}")

                        if archive_before_delete:
                            ok_drive, msg_drive = self.archive_to_google_drive(fpath)
                            if ok_drive:
                                print(f"[StorageManager] Archived to Google Drive: {fname}")
                            else:
                                print(f"[StorageManager] Drive not configured or failed ({msg_drive}). Attempting Telegram backup...")
                                ok_tg, msg_tg = self.backup_to_telegram(fpath, caption=f"Archived DCC Nightly Report: {fname}")
                                if ok_tg:
                                    print(f"[StorageManager] Backup dispatched to Telegram: {fname}")

                        os.remove(fpath)
                        deleted_files.append(fpath)
                        print(f"[StorageManager] Deleted local aged file: {fname}")
                except Exception as e:
                    print(f"[StorageManager] Error pruning {fpath}: {e}")

        return deleted_files
