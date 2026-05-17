from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from history_loader import UsageEntry
from pricing import calculate_cost

BLOCK_DURATION = timedelta(hours=5)
GAP_THRESHOLD = timedelta(minutes=5)


@dataclass(slots=True)
class SessionBlock:
    start_time: datetime
    end_time: datetime
    entries: list[UsageEntry] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    is_active: bool = False
    is_gap: bool = False
    burn_rate: float = 0.0


def analyze_blocks(entries: list[UsageEntry]) -> list[SessionBlock]:
    if not entries:
        return []

    blocks: list[SessionBlock] = []
    current: SessionBlock | None = None

    for entry in sorted(entries, key=lambda item: item.timestamp):
        if current is None or entry.timestamp >= current.end_time:
            if current is not None and entry.timestamp - current.end_time > GAP_THRESHOLD:
                blocks.append(
                    SessionBlock(
                        start_time=current.end_time,
                        end_time=entry.timestamp,
                        is_gap=True,
                    )
                )

            current = SessionBlock(
                start_time=entry.timestamp,
                end_time=entry.timestamp + BLOCK_DURATION,
            )
            blocks.append(current)

        _add_entry(current, entry)

    _mark_active_blocks(blocks)
    return blocks


def current_block(blocks: list[SessionBlock]) -> SessionBlock | None:
    for block in blocks:
        if block.is_active:
            return block
    return None


def _add_entry(block: SessionBlock, entry: UsageEntry) -> None:
    block.entries.append(entry)
    block.input_tokens += entry.input_tokens
    block.output_tokens += entry.output_tokens
    block.cache_creation_tokens += entry.cache_creation_tokens
    block.cache_read_tokens += entry.cache_read_tokens
    block.total_tokens += entry.total_tokens
    block.cost_usd += calculate_cost(entry)


def _mark_active_blocks(blocks: list[SessionBlock]) -> None:
    now = datetime.now(UTC)
    for block in blocks:
        if block.is_gap or not block.entries or not (block.start_time <= now < block.end_time):
            continue

        block.is_active = True
        elapsed_minutes = max(1.0, (now - block.start_time).total_seconds() / 60.0)
        block.burn_rate = block.total_tokens / elapsed_minutes
