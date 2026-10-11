"""Command line helpers.

  python -m app.cli run <task_id> [--dry-run]   process one task by hand
  python -m app.cli register-webhook <team_id> <public_url> [--list-id ID]
"""

import argparse
import logging
import sys

from .clickup import ClickUpClient
from .config import Config
from .pipeline import process_task, summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("run", help="Push one ClickUp task to Meta")
    run_p.add_argument("task_id")
    run_p.add_argument("--dry-run", action="store_true", help="Read ClickUp + Drive only; create nothing")

    hook_p = sub.add_parser("register-webhook", help="Create the ClickUp webhook")
    hook_p.add_argument("team_id", help="ClickUp Workspace ID (the number in app.clickup.com/<id>/...)")
    hook_p.add_argument("url", help="Public base URL of this service, e.g. https://ads-bot.example.com")
    hook_p.add_argument("--list-id", help="Only watch this list")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    cfg = Config.from_env()

    if args.cmd == "run":
        result = process_task(args.task_id, cfg, dry_run=args.dry_run)
        print(summary(result))
        if args.dry_run:
            print("\nPrimary text as it will appear in Meta:\n" + "-" * 40)
            print(result.brief.primary_text)
        return 0 if result.ok else 1

    res = ClickUpClient(cfg.clickup_token).create_webhook(
        args.team_id, args.url.rstrip("/") + "/webhooks/clickup", args.list_id
    )
    print(f"Webhook id: {res.get('id')}")
    secret = (res.get("webhook") or {}).get("secret")
    if secret:
        print(f"Set CLICKUP_WEBHOOK_SECRET={secret}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
