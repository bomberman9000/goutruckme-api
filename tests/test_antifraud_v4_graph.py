from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.antifraud.entities import extract_entities_from_deal, link_entities_for_deal, upsert_entities
from app.antifraud.graph import rebuild_components_incremental
from app.antifraud.lists import add_to_list
from app.antifraud.reputation import get_counterparty_network_risk
from app.db.database import Base
from app.models.models import FraudComponent, FraudEdge, FraudEntity, FraudEntityComponent


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


def _deal() -> dict:
    return {
        "id": 901001,
        "counterparty": {
            "inn": "7701234567",
            "phone": "+7 (999) 000-11-22",
            "email": "OPS@Test.RU",
            "name": "  ООО   Риск  ",
        },
        "payment": {"card": "4111 1111 1111 1111", "bank_account": "40702810900000000001"},
        "metadata": {"ip": "10.1.1.2", "device": "DEVICE-ABC"},
    }


def test_entity_extraction_and_normalization():
    entities = extract_entities_from_deal(_deal())
    by_type = {item["type"]: item["value"] for item in entities}

    assert by_type["inn"] == "7701234567"
    assert by_type["phone"] == "79990001122"
    assert by_type["email"] == "ops@test.ru"
    assert by_type["name"] == "ооо риск"
    assert by_type["card"] == "4111111111111111"
    assert by_type["bank_account"] == "40702810900000000001"


@pytest.mark.asyncio
async def test_deal_node_linking_creates_edges(db_session):
    entities = await upsert_entities(db_session, extract_entities_from_deal(_deal()))
    deal_node = await link_entities_for_deal(db_session, 901001, entities)

    assert deal_node.entity_type == "deal"
    edges = db_session.query(FraudEdge).filter(FraudEdge.src_entity_id == deal_node.id).all()
    assert len(edges) == len(entities)


@pytest.mark.asyncio
async def test_component_build_stable_key(db_session):
    entities = await upsert_entities(db_session, extract_entities_from_deal(_deal()))
    deal_node = await link_entities_for_deal(db_session, 901001, entities)

    start_ids = [deal_node.id] + [item.id for item in entities]
    await rebuild_components_incremental(db_session, start_ids)

    mapping = (
        db_session.query(FraudEntityComponent)
        .filter(FraudEntityComponent.entity_id == deal_node.id)
        .first()
    )
    assert mapping is not None

    component = db_session.query(FraudComponent).filter(FraudComponent.id == mapping.component_id).first()
    assert component is not None
    first_key = component.component_key

    await rebuild_components_incremental(db_session, start_ids)

    mapping_2 = (
        db_session.query(FraudEntityComponent)
        .filter(FraudEntityComponent.entity_id == deal_node.id)
        .first()
    )
    component_2 = db_session.query(FraudComponent).filter(FraudComponent.id == mapping_2.component_id).first()
    assert component_2.component_key == first_key


@pytest.mark.asyncio
async def test_network_risk_connected_blacklist(db_session):
    deal = _deal()
    await add_to_list(db_session, list_type="black", phone=deal["counterparty"]["phone"], note="shared fraud")

    entities = await upsert_entities(db_session, extract_entities_from_deal(deal))
    deal_node = await link_entities_for_deal(db_session, 901001, entities)
    await rebuild_components_incremental(db_session, [deal_node.id] + [item.id for item in entities])

    summary = await get_counterparty_network_risk(
        db_session,
        inn=deal["counterparty"]["inn"],
        phone=deal["counterparty"]["phone"],
        email=deal["counterparty"]["email"],
        name=deal["counterparty"]["name"],
    )

    assert summary["connected_blacklist"] is True
    assert summary["component_key"] is not None
    assert isinstance(summary["entity_risks"], list)
