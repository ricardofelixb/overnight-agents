#!/usr/bin/env python3
"""Compatibility entrypoint; use controller.py for the PR-agent runtime."""

from controller import main


if __name__ == "__main__":
    raise SystemExit(main())
