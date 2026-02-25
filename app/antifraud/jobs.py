from __future__ import annotations

import argparse
import asyncio
from typing import Any

from app.antifraud.graph import rebuild_components_full
from app.antifraud.learning import recompute_route_stats
from app.antifraud.ml import train_model
from app.db.database import SessionLocal


async def job_recompute_route_stats() -> dict[str, Any]:
    session = SessionLocal()
    try:
        return await recompute_route_stats(session)
    finally:
        session.close()


async def job_train_model() -> dict[str, Any]:
    session = SessionLocal()
    try:
        return await train_model(session)
    finally:
        session.close()


async def job_rebuild_components_full() -> dict[str, Any]:
    session = SessionLocal()
    try:
        return await rebuild_components_full(session)
    finally:
        session.close()


def _print_result(result: dict[str, Any]) -> None:
    for key, value in result.items():
        print(f"{key}={value}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Antifraud jobs")
    parser.add_argument(
        "command",
        choices=["recompute_route_stats", "train_model", "rebuild_components_full"],
        help="Job command",
    )
    args = parser.parse_args()

    if args.command == "recompute_route_stats":
        result = asyncio.run(job_recompute_route_stats())
    elif args.command == "train_model":
        result = asyncio.run(job_train_model())
    else:
        result = asyncio.run(job_rebuild_components_full())

    _print_result(result)


if __name__ == "__main__":
    main()
