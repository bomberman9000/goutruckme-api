from typing import Any, Dict

from fastmcp import FastMCP

# твои реальные движки
from app.moderation.engine import review_deal, review_document

mcp = FastMCP("gruzpotok-mcp")


@mcp.tool()
def moderation_review_deal(deal: Dict[str, Any]) -> Dict[str, Any]:
    """
    Проверка сделки (risk / flags / comment / recommended_action)
    Обертка над app.moderation.engine.review_deal
    """
    return review_deal(deal)


@mcp.tool()
def moderation_review_document(document: Dict[str, Any]) -> Dict[str, Any]:
    """
    Проверка документа (пустота, дубликат, тип, контекст)
    Обертка над app.moderation.engine.review_document
    """
    return review_document(document)


if __name__ == "__main__":
    # stdio-режим для Cursor
    mcp.run()
