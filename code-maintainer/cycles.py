"""Persistent, atomic state for perpetual semantic maintenance cycles."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class CycleFailure(ValueError):
    pass


@dataclass(frozen=True)
class CyclePosition:
    cycle: int
    index: int


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, value: dict[str, Any], mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def load_position(path: Path, slice_ids: tuple[str, ...]) -> CyclePosition:
    if not slice_ids:
        raise CycleFailure("maintenance cycle requires at least one slice")
    if not path.exists():
        return CyclePosition(cycle=1, index=0)
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise CycleFailure(f"invalid maintenance cycle state: {error}") from error
    if not isinstance(value, dict):
        raise CycleFailure("invalid maintenance cycle position")
    next_slice = value.get("next_slice")
    if (
        value.get("version") != 1
        or not isinstance(value.get("cycle"), int)
        or isinstance(value.get("cycle"), bool)
        or value["cycle"] < 1
        or not isinstance(next_slice, str)
        or next_slice not in slice_ids
    ):
        raise CycleFailure("invalid maintenance cycle position")
    return CyclePosition(cycle=value["cycle"], index=slice_ids.index(next_slice))


def _cycle_state(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise CycleFailure(f"invalid maintenance cycle state: {error}") from error
    if (
        not isinstance(value, dict)
        or value.get("version") != 1
        or not isinstance(value.get("cycle"), int)
        or isinstance(value.get("cycle"), bool)
        or value["cycle"] < 1
        or not isinstance(value.get("next_slice"), str)
    ):
        raise CycleFailure("invalid maintenance cycle position")
    return value


def reconcile_registry(
    path: Path,
    slice_ids: tuple[str, ...],
    *,
    previous_slice_ids: tuple[str, ...] = (),
) -> CyclePosition:
    """Move a removed next slice to the next surviving semantic owner."""

    if not slice_ids:
        raise CycleFailure("maintenance cycle requires at least one slice")
    if not path.exists():
        return CyclePosition(cycle=1, index=0)
    value = _cycle_state(path)
    next_slice = value["next_slice"]
    if next_slice in slice_ids:
        return CyclePosition(cycle=value["cycle"], index=slice_ids.index(next_slice))

    next_cycle = value["cycle"]
    successor: str | None = None
    if next_slice in previous_slice_ids:
        old_index = previous_slice_ids.index(next_slice)
        for offset in range(1, len(previous_slice_ids) + 1):
            candidate_index = old_index + offset
            candidate = previous_slice_ids[candidate_index % len(previous_slice_ids)]
            if candidate in slice_ids:
                successor = candidate
                if candidate_index >= len(previous_slice_ids):
                    next_cycle += 1
                break
    if successor is None:
        completed = value.get("last_completed")
        completed_slice = (
            completed.get("slice") if isinstance(completed, dict) else None
        )
        if completed_slice in slice_ids:
            completed_index = slice_ids.index(completed_slice)
            successor = slice_ids[(completed_index + 1) % len(slice_ids)]
            completed_cycle = completed.get("cycle")
            if isinstance(completed_cycle, int) and not isinstance(
                completed_cycle, bool
            ):
                next_cycle = max(
                    next_cycle,
                    completed_cycle + int(completed_index + 1 == len(slice_ids)),
                )
        else:
            successor = slice_ids[0]

    value["cycle"] = next_cycle
    value["next_slice"] = successor
    atomic_json(path, value)
    return CyclePosition(cycle=next_cycle, index=slice_ids.index(successor))


def retire_removed_slice(
    path: Path,
    slice_ids: tuple[str, ...],
    *,
    cycle: int,
    slice_id: str,
    outcome: str,
    previous_slice_ids: tuple[str, ...] = (),
) -> CyclePosition:
    """Record a terminal pending slice that disappeared from the registry."""

    if not path.exists():
        raise CycleFailure("maintenance cycle position is missing")
    value = _cycle_state(path)
    if value["cycle"] != cycle or value["next_slice"] != slice_id:
        raise CycleFailure("maintenance cycle position changed concurrently")
    result = reconcile_registry(
        path,
        slice_ids,
        previous_slice_ids=previous_slice_ids,
    )
    value = _cycle_state(path)
    value["last_completed"] = {
        "cycle": cycle,
        "slice": slice_id,
        "outcome": outcome,
        "completed_at": now_iso(),
    }
    atomic_json(path, value)
    return result


def checkpoint(
    path: Path, position: CyclePosition, slice_ids: tuple[str, ...]
) -> None:
    if path.exists():
        if load_position(path, slice_ids) != position:
            raise CycleFailure("maintenance cycle position changed concurrently")
        return
    atomic_json(
        path,
        {
            "version": 1,
            "cycle": position.cycle,
            "next_slice": slice_ids[position.index],
        },
    )


def advance(
    path: Path,
    position: CyclePosition,
    slice_ids: tuple[str, ...],
    *,
    slice_id: str,
    outcome: str,
) -> CyclePosition:
    current = load_position(path, slice_ids)
    if current != position:
        raise CycleFailure("maintenance cycle position changed concurrently")
    next_index = position.index + 1
    next_cycle = position.cycle
    if next_index == len(slice_ids):
        next_index = 0
        next_cycle += 1
    result = CyclePosition(cycle=next_cycle, index=next_index)
    atomic_json(
        path,
        {
            "version": 1,
            "cycle": result.cycle,
            "next_slice": slice_ids[result.index],
            "last_completed": {
                "cycle": position.cycle,
                "slice": slice_id,
                "outcome": outcome,
                "completed_at": now_iso(),
            },
        },
    )
    return result
