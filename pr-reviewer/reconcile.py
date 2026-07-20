#!/usr/bin/env python3
"""Retry pending outbound notifications without starting PR reviews."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from review import load_configuration
from telegram_notify import NotificationFailure, flush_pending


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    config, config_dir = load_configuration(args.config)
    defaults = config.get("defaults", {})
    state_root = Path(config["state_root"]).expanduser()
    if not state_root.is_absolute():
        state_root = (config_dir / state_root).resolve()
    if defaults.get("telegram_notifications_enabled", False):
        env_path = Path(config["telegram_env"]).expanduser()
        if not env_path.is_absolute():
            env_path = (config_dir / env_path).resolve()
        try:
            delivered, failed = flush_pending(state_root, env_path)
            if delivered or failed:
                print(f"Telegram outbox retry: delivered={delivered} pending_failures={failed}")
        except NotificationFailure:
            print("Telegram outbox retry unavailable; pending events were preserved", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
