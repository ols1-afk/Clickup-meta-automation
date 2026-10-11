"""Webhook receiver: ClickUp status change -> background Meta upload.

Run with:  gunicorn -w 1 --threads 4 -b 0.0.0.0:8000 app.server:app
(keep a single worker so the in-process queue sees every job).
"""

import json
import logging
import queue
import threading

from flask import Flask, abort, request

from .clickup import status_change_target, verify_signature
from .config import Config
from .pipeline import process_task

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)

cfg = Config.from_env()
app = Flask(__name__)

_jobs: "queue.Queue[str]" = queue.Queue()
_pending: set[str] = set()
_lock = threading.Lock()


def _worker() -> None:
    while True:
        task_id = _jobs.get()
        try:
            process_task(task_id, cfg)
        except Exception:
            pass  # already logged and commented on the task
        finally:
            with _lock:
                _pending.discard(task_id)
            _jobs.task_done()


threading.Thread(target=_worker, daemon=True).start()


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/webhooks/clickup")
def clickup_webhook():
    body = request.get_data()
    if cfg.clickup_webhook_secret and not verify_signature(
        cfg.clickup_webhook_secret, body, request.headers.get("X-Signature")
    ):
        abort(401)
    try:
        payload = json.loads(body or b"{}")
    except ValueError:
        abort(400)

    new_status = status_change_target(payload)
    task_id = payload.get("task_id")
    if not task_id or not new_status or new_status.strip().lower() != cfg.trigger_status.strip().lower():
        return {"queued": False}

    with _lock:
        if task_id in _pending:  # ClickUp retries / double clicks
            return {"queued": False, "reason": "already queued"}
        _pending.add(task_id)
    _jobs.put(task_id)
    log.info("Queued task %s", task_id)
    return {"queued": True}
