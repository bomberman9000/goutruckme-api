from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import ResponseError


@dataclass(slots=True)
class StreamMessage:
    entry_id: str
    raw_text: str
    chat_id: str
    message_id: int
    source: str
    external_url: str | None
    received_at: int
    retry_count: int


class RedisLogisticsStream:
    def __init__(self, redis_client: Redis, *, stream_name: str, maxlen: int) -> None:
        self.redis = redis_client
        self.stream_name = stream_name
        self.maxlen = max(1000, int(maxlen))

    async def add_raw_message(
        self,
        *,
        raw_text: str,
        chat_id: str,
        message_id: int,
        source: str,
        external_url: str | None = None,
        received_at: int,
        retry_count: int = 0,
    ) -> str:
        fields = {
            "raw_text": raw_text,
            "chat_id": str(chat_id),
            "message_id": str(int(message_id)),
            "source": source,
            "received_at": str(int(received_at)),
            "retry_count": str(int(retry_count)),
        }
        if external_url:
            fields["external_url"] = str(external_url)
        return await self.redis.xadd(
            self.stream_name,
            fields,
            maxlen=self.maxlen,
            approximate=True,
        )

    async def ensure_group(self, group_name: str) -> None:
        try:
            await self.redis.xgroup_create(self.stream_name, group_name, id="$", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def read_group(
        self,
        *,
        group_name: str,
        consumer_name: str,
        count: int,
        block_ms: int,
    ) -> list[StreamMessage]:
        raw_rows = await self.redis.xreadgroup(
            group_name,
            consumer_name,
            streams={self.stream_name: ">"},
            count=max(1, int(count)),
            block=max(100, int(block_ms)),
        )
        return self._flatten_rows(raw_rows)

    async def claim_stale(
        self,
        *,
        group_name: str,
        consumer_name: str,
        min_idle_ms: int,
        count: int,
    ) -> list[StreamMessage]:
        try:
            next_start, rows, _deleted = await self.redis.xautoclaim(
                self.stream_name,
                group_name,
                consumer_name,
                min_idle_time=max(1000, int(min_idle_ms)),
                start_id="0-0",
                count=max(1, int(count)),
            )
            _ = next_start
            return [self._to_message(entry_id, fields) for entry_id, fields in rows]
        except ResponseError as exc:
            if "NOGROUP" in str(exc):
                return []
            raise

    async def ack(self, *, group_name: str, entry_id: str) -> None:
        await self.redis.xack(self.stream_name, group_name, entry_id)

    def _flatten_rows(self, rows: Any) -> list[StreamMessage]:
        messages: list[StreamMessage] = []
        for _stream_name, entries in rows or []:
            for entry_id, fields in entries:
                messages.append(self._to_message(entry_id, fields))
        return messages

    def _to_message(self, entry_id: str, fields: dict[str, Any]) -> StreamMessage:
        return StreamMessage(
            entry_id=entry_id,
            raw_text=str(fields.get("raw_text") or ""),
            chat_id=str(fields.get("chat_id") or "unknown"),
            message_id=self._to_int(fields.get("message_id"), default=0),
            source=str(fields.get("source") or "unknown"),
            external_url=str(fields.get("external_url") or "").strip() or None,
            received_at=self._to_int(fields.get("received_at"), default=0),
            retry_count=self._to_int(fields.get("retry_count"), default=0),
        )

    @staticmethod
    def _to_int(value: Any, *, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
