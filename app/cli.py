"""Command line entry points (used by the GitHub Actions workflow).

  python -m app.cli poll [--dry-run]           process every task in the trigger status
  python -m app.cli run <task_id> [--dry-run]  process one task by hand
  python -m app.cli check                      test every key, ID, status and field
"""

import argparse
import logging
import sys

from .check import check
from .clickup import ClickUpClient
from .config import Config, ConfigError
from .pipeline import process_task, summary

log = logging.getLogger("app")


def poll(cfg: Config, dry_run: bool = False) -> int:
    if not cfg.clickup_team_id:
        raise ConfigError("Set CLICKUP_TEAM_ID (the number after app.clickup.com/ in any ClickUp URL)")
    task_ids = ClickUpClient(cfg.clickup_token).tasks_in_status(
        cfg.clickup_team_id, cfg.trigger_status, cfg.clickup_list_ids
    )
    log.info("%d task(s) in '%s'", len(task_ids), cfg.trigger_status)
    failures = 0
    for task_id in task_ids:
        try:
            result = process_task(task_id, cfg, dry_run=dry_run)
            if dry_run:
                print(f"\n=== {task_id} ===\n{summary(result)}")
            elif not result.ok:
                failures += 1
        except Exception:
            failures += 1  # already logged and commented on the task
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    poll_p = sub.add_parser("poll", help="Push every task in the trigger status to Meta")
    poll_p.add_argument("--dry-run", action="store_true", help="List what would be created; change nothing")
    run_p = sub.add_parser("run", help="Push one ClickUp task to Meta")
    run_p.add_argument("task_id")
    run_p.add_argument("--dry-run", action="store_true", help="Read ClickUp only; create nothing, change nothing")

    sub.add_parser("check", help="Test every key, ID, status and field; change nothing")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if args.cmd == "check":
        return check()
    cfg = Config.from_env()

    if args.cmd == "poll":
        return poll(cfg, dry_run=args.dry_run)

    result = process_task(args.task_id, cfg, dry_run=args.dry_run)
    print(summary(result))
    if args.dry_run:
        print("\nPrimary text as it will appear in Meta:\n" + "-" * 40)
        print(result.brief.primary_text)
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
