"""EventBridge -> SQS ingestion.

Uses a dedicated platform identity (``events_profile``) that may only receive/delete from
the events queue; it is not the agent's read or execution identity. Messages are deleted
only after the event is durably deduplicated and its tasks are enqueued. Rejected events are
left for SQS redrive to the dead-letter queue (``maxReceiveCount``), so poison messages are
visible instead of silently dropped.
"""

from __future__ import annotations

import logging
from typing import Any

from infra_agent.events.triggers import EventRejected, parse_eventbridge, tasks_for_event

log = logging.getLogger("infra_agent.events")


def poll_once(platform: Any, sqs: Any, queue_url: str, wait_seconds: int = 10) -> dict[str, int]:
    stats = {"received": 0, "tasks": 0, "duplicates": 0, "rejected": 0}
    resp = sqs.receive_message(
        QueueUrl=queue_url, MaxNumberOfMessages=10, WaitTimeSeconds=wait_seconds
    )
    for msg in resp.get("Messages", []):
        stats["received"] += 1
        try:
            event_id, detail_type, event = parse_eventbridge(platform.config, msg["Body"])
        except (EventRejected, ValueError) as exc:
            stats["rejected"] += 1
            log.warning("rejected event message %s: %s", msg.get("MessageId"), exc)
            continue
        if platform.store.register_event(f"aws:{event_id}", "aws", {"detail-type": detail_type}):
            for task in tasks_for_event(platform.config, "aws", detail_type, event):
                platform.submit(task)
                stats["tasks"] += 1
        else:
            stats["duplicates"] += 1
        sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=msg["ReceiptHandle"])
    return stats
