"""Localhost approval server for image and video review gates."""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


@dataclass
class ReviewDecision:
    """Record of a human approval decision."""

    asset_id: str
    decision: str  # "approve" | "reject"
    comment: str
    decided_at: str
    operator: str = "local"


@dataclass
class ReviewSession:
    """State for a single review session."""

    job_id: str
    token: str
    decisions: list[ReviewDecision] = field(default_factory=list)
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    completed: bool = False


class ReviewHandler(BaseHTTPRequestHandler):
    """HTTP handler for the review server."""

    # Injected by ReviewServer
    _session: ReviewSession | None = None
    _artifact_dir: Path | None = None
    _callback: Any = None

    def log_message(self, format, *args):  # type: ignore[override]
        """Suppress default logging to keep console clean."""

    def _send_json(self, status: int, data: Any) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/health":
            self._send_json(200, {"status": "ok"})
            return

        if parsed.path == "/session":
            if self._session is None:
                self._send_json(404, {"error": "no active session"})
                return
            self._send_json(
                200,
                {
                    "job_id": self._session.job_id,
                    "started_at": self._session.started_at,
                    "decisions": self._session.decisions,
                    "completed": self._session.completed,
                },
            )
            return

        if parsed.path == "/assets":
            if self._artifact_dir is None:
                self._send_json(500, {"error": "artifact dir not configured"})
                return
            assets = []
            for child in sorted(self._artifact_dir.iterdir()):
                if child.is_file() and not child.name.endswith(".partial"):
                    assets.append(
                        {
                            "name": child.name,
                            "size": child.stat().st_size,
                            "sha256": hashlib.sha256(child.read_bytes()).hexdigest(),
                        }
                    )
            self._send_json(200, {"assets": assets})
            return

        # Default: serve HTML review page
        self._serve_review_page()

    def do_POST(self):
        parsed = urlparse(self.path)

        if parsed.path == "/decision":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            data = json.loads(body) if body else {}

            decision = ReviewDecision(
                asset_id=data.get("asset_id", ""),
                decision=data.get("decision", "reject"),
                comment=data.get("comment", ""),
                decided_at=datetime.now(UTC).isoformat(),
                operator=data.get("operator", "local"),
            )

            if self._session is not None:
                self._session.decisions.append(decision)

            # Notify callback if registered
            if self._callback is not None:
                self._callback(decision)

            self._send_json(200, {"accepted": True, "decision": decision.__dict__})
            return

        if parsed.path == "/complete":
            if self._session is not None:
                self._session.completed = True
            self._send_json(200, {"completed": True})
            return

        self._send_json(404, {"error": "not found"})

    def _serve_review_page(self) -> None:
        html = _REVIEW_PAGE_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)


class ReviewServer:
    """Localhost HTTP server for manual approval gates."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8765):
        self._host = host
        self._port = port
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._session: ReviewSession | None = None
        self._callback = None
        self._artifact_dir: Path | None = None

    @property
    def session(self) -> ReviewSession | None:
        return self._session

    @property
    def url(self) -> str:
        return f"http://{self._host}:{self._port}"

    def start(
        self,
        job_id: str,
        artifact_dir: Path | None = None,
        callback=None,
    ) -> str:
        """Start the review server and return the session token."""
        token = secrets.token_urlsafe(32)
        self._session = ReviewSession(job_id=job_id, token=token)
        self._artifact_dir = artifact_dir
        self._callback = callback

        ReviewHandler._session = self._session
        ReviewHandler._artifact_dir = artifact_dir
        ReviewHandler._callback = callback

        self._server = HTTPServer((self._host, self._port), ReviewHandler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

        return token

    def stop(self) -> None:
        """Stop the review server."""
        if self._server is not None:
            self._server.shutdown()
            self._server = None
            self._thread = None


# -- Embedded HTML review page --

_REVIEW_PAGE_HTML = """\
<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="utf-8">
<title>Lyria Visual Review</title>
<style>
  body { font-family: system-ui, sans-serif; max-width: 960px; margin: 2rem auto; padding: 0 1rem; background: #f9fafb; color: #111827; }
  h1 { font-size: 1.5rem; margin-bottom: 1rem; }
  .card { background: white; border-radius: 8px; padding: 1.5rem; margin-bottom: 1rem; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
  .btn { padding: 0.5rem 1rem; border: none; border-radius: 6px; cursor: pointer; font-size: 1rem; margin-right: 0.5rem; }
  .btn-approve { background: #10b981; color: white; }
  .btn-reject { background: #ef4444; color: white; }
  .btn-complete { background: #3b82f6; color: white; }
  textarea { width: 100%; min-height: 60px; margin-top: 0.5rem; border-radius: 6px; border: 1px solid #d1d5db; padding: 0.5rem; }
  .asset { margin-bottom: 1rem; padding: 1rem; background: #f3f4f6; border-radius: 6px; }
  .asset img, .asset video { max-width: 100%; max-height: 400px; border-radius: 4px; margin-top: 0.5rem; }
  #decisions { margin-top: 1rem; }
  .decision { padding: 0.5rem; margin-bottom: 0.5rem; border-radius: 4px; }
  .decision.approve { background: #d1fae5; }
  .decision.reject { background: #fee2e2; }
</style>
</head>
<body>
<h1>🎨 Lyria 視覺審批</h1>
<div class="card">
  <p>請檢視生成的視覺素材，並決定是否批准。</p>
  <div id="assets"></div>
</div>
<div class="card">
  <h3>提交決定</h3>
  <input type="text" id="assetId" placeholder="素材 ID" style="padding:0.5rem;border-radius:6px;border:1px solid #d1d5db;width:200px">
  <br><br>
  <textarea id="comment" placeholder="評論（可選）"></textarea>
  <br><br>
  <button class="btn btn-approve" onclick="submitDecision('approve')">✅ 批准</button>
  <button class="btn btn-reject" onclick="submitDecision('reject')">❌ 拒絕</button>
  <button class="btn btn-complete" onclick="completeReview()">🏁 完成審批</button>
</div>
<div class="card" id="decisions">
  <h3>決定記錄</h3>
  <div id="decisionList"></div>
</div>
<script>
async function loadAssets() {
  const resp = await fetch('/assets');
  const data = await resp.json();
  const container = document.getElementById('assets');
  container.innerHTML = data.assets.map(a => `
    <div class="asset">
      <strong>${a.name}</strong> (${(a.size / 1024).toFixed(1)} KB)<br>
      <code>${a.sha256.slice(0, 16)}...</code>
    </div>
  `).join('');
}

async function submitDecision(decision) {
  const assetId = document.getElementById('assetId').value;
  const comment = document.getElementById('comment').value;
  if (!assetId) { alert('請輸入素材 ID'); return; }
  await fetch('/decision', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({asset_id: assetId, decision, comment})
  });
  loadDecisions();
  document.getElementById('assetId').value = '';
  document.getElementById('comment').value = '';
}

async function completeReview() {
  await fetch('/complete', {method: 'POST'});
  alert('審批已完成！');
  loadDecisions();
}

async function loadDecisions() {
  const resp = await fetch('/session');
  const data = await resp.json();
  const container = document.getElementById('decisionList');
  container.innerHTML = data.decisions.map(d => `
    <div class="decision ${d.decision}">
      <strong>${d.asset_id}</strong>: ${d.decision} — ${d.comment || '(無評論)'}<br>
      <small>${new Date(d.decided_at).toLocaleString('zh-TW')}</small>
    </div>
  `).join('');
}

loadAssets();
loadDecisions();
setInterval(loadDecisions, 5000);
</script>
</body>
</html>
"""
