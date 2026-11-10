from __future__ import annotations

import copy
import json

import boto3
from botocore.stub import Stubber

from infra_agent.config import AgentConfig, AppSettings
from infra_agent.events.sqs import poll_once
from infra_agent.persistence.queue import InMemoryQueue
from infra_agent.persistence.store import InMemoryStore
from infra_agent.runtime import Platform
from tests.conftest import config_dict

URL = "https://sqs.ap-south-1.amazonaws.com/111122223333/events"


def _platform() -> Platform:
    data = copy.deepcopy(config_dict())
    data["triggers"] = [
        {
            "id": "alarm",
            "source": "aws",
            "event": "CloudWatch Alarm State Change",
            "match": {"detail.state.value": "ALARM"},
            "account_alias": "dev",
            "intent": "Investigate the alarm",
        }
    ]
    return Platform(
        config=AgentConfig.model_validate(data),
        settings=AppSettings(app_env="test"),
        store=InMemoryStore(),
        queue=InMemoryQueue(),
    )


def _msg(mid: str, event: dict[str, object]) -> dict[str, str]:
    return {"MessageId": mid, "ReceiptHandle": f"rh-{mid}", "Body": json.dumps(event)}


def test_poll_dedupes_and_leaves_poison_for_dlq() -> None:
    p = _platform()
    alarm = {
        "id": "e1",
        "detail-type": "CloudWatch Alarm State Change",
        "source": "aws.cloudwatch",
        "account": "111122223333",
        "region": "ap-south-1",
        "detail": {"state": {"value": "ALARM"}},
    }
    foreign = {**alarm, "id": "e2", "account": "999988887777"}
    sqs = boto3.Session(region_name="ap-south-1").client("sqs")
    stub = Stubber(sqs)
    stub.add_response(
        "receive_message", {"Messages": [_msg("1", alarm), _msg("2", alarm), _msg("3", foreign)]}
    )
    stub.add_response("delete_message", {}, {"QueueUrl": URL, "ReceiptHandle": "rh-1"})
    stub.add_response("delete_message", {}, {"QueueUrl": URL, "ReceiptHandle": "rh-2"})
    with stub:
        stats = poll_once(p, sqs, URL, wait_seconds=0)
    stub.assert_no_pending_responses()  # foreign message NOT deleted -> redrive to DLQ
    assert stats == {"received": 3, "tasks": 1, "duplicates": 1, "rejected": 1}
    assert len(p.queue.jobs) == 1  # type: ignore[attr-defined]
