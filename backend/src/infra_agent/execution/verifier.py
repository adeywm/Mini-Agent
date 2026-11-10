"""Independent post-execution verification by reading authoritative state.

An accepted API response is not proof of success; the verifier re-reads with the
READ-only path and compares expected values, polling for eventual consistency.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import Any

from infra_agent.aws.reader import AwsReader
from infra_agent.core.operations import OperationRef, OperationRequest, ResourceScope
from infra_agent.models.domain import PlannedOperation

_TOKEN = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


def get_path(data: Any, path: str) -> Any:
    cur = data
    for name, index in _TOKEN.findall(path):
        if name:
            if not isinstance(cur, dict) or name not in cur:
                raise KeyError(path)
            cur = cur[name]
        else:
            if not isinstance(cur, list) or int(index) >= len(cur):
                raise KeyError(path)
            cur = cur[int(index)]
    return cur


def verify_aws(
    reader: AwsReader,
    op: PlannedOperation,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[bool, str]:
    spec = op.verify
    if spec is None:
        return False, "no verification spec; outcome unverified"
    request = OperationRequest(
        ref=OperationRef(provider="aws", service=spec.service, operation=spec.operation),
        scope=ResourceScope(account_alias=op.account_alias, region=op.region),
        params=spec.params,
    )
    last = ""
    for attempt in range(spec.attempts):
        try:
            data = reader.call(request).data
            mismatches = []
            for path, expected in spec.expect.items():
                try:
                    actual = get_path(data, path)
                except KeyError:
                    actual = "<missing>"
                if actual != expected:
                    mismatches.append(f"{path}={actual!r} (want {expected!r})")
            if not mismatches:
                return True, f"verified after {attempt + 1} read(s)"
            last = "; ".join(mismatches)
        except Exception as exc:
            last = f"{type(exc).__name__}: {exc}"
        if attempt + 1 < spec.attempts:
            sleep(spec.delay_seconds)
    return False, f"verification failed: {last}"
