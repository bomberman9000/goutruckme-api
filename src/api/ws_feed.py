"""WebSocket real-time feed for push-based cargo updates.

Clients connect to ``/api/v1/feed/ws`` and receive new cargo events
as they appear.  The server polls for new events every few seconds
and pushes JSON messages to all connected clients.
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select, func

from src.core.database import async_session
from src.core.models import ParserIngestEvent

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ws"])

_POLL_INTERVAL_SEC = 5
_MAX_BATCH = 10


class FeedBroadcaster:
    """Manages connected WebSocket clients and broadcasts new events."""

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._last_id: int | None = None

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)
        logger.info("ws_feed: client connected (%d total)", len(self._clients))

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)
        logger.info("ws_feed: client disconnected (%d remaining)", len(self._clients))

    async def broadcast(self, message: str) -> None:
        dead: list[WebSocket] = []
        for ws in self._clients:
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._clients.discard(ws)

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def poll_and_push(self) -> int:
        """Check for new events since last_id and push to clients."""
        if not self._clients:
            return 0

        async with async_session() as session:
            if self._last_id is None:
                max_id = await session.scalar(
                    select(func.max(ParserIngestEvent.id))
                )
                self._last_id = max_id or 0
                return 0

            rows = (
                await session.execute(
                    select(ParserIngestEvent)
                    .where(
                        ParserIngestEvent.id > self._last_id,
                        ParserIngestEvent.is_spam.is_(False),
                        ParserIngestEvent.status == "synced",
                    )
                    .order_by(ParserIngestEvent.id.asc())
                    .limit(_MAX_BATCH)
                )
            ).scalars().all()

        if not rows:
            return 0

        self._last_id = rows[-1].id

        for event in rows:
            msg = json.dumps({
                "type": "new_cargo",
                "data": {
                    "id": event.id,
                    "from_city": event.from_city,
                    "to_city": event.to_city,
                    "body_type": event.body_type,
                    "rate_rub": event.rate_rub,
                    "weight_t": event.weight_t,
                    "load_date": event.load_date,
                    "load_time": event.load_time,
                    "is_hot_deal": event.is_hot_deal,
                    "trust_verdict": event.trust_verdict,
                    "created_at": event.created_at.isoformat() if event.created_at else None,
                },
            }, ensure_ascii=False)
            await self.broadcast(msg)

        logger.info("ws_feed: pushed %d events to %d clients", len(rows), self.client_count)
        return len(rows)


broadcaster = FeedBroadcaster()


@router.websocket("/api/v1/feed/ws")
async def ws_feed(ws: WebSocket):
    await broadcaster.connect(ws)
    try:
        while True:
            try:
                data = await asyncio.wait_for(ws.receive_text(), timeout=_POLL_INTERVAL_SEC)
                if data == "ping":
                    await ws.send_text(json.dumps({"type": "pong"}))
            except asyncio.TimeoutError:
                pass
            await broadcaster.poll_and_push()
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.debug("ws_feed error: %s", exc)
    finally:
        broadcaster.disconnect(ws)
