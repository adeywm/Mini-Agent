"""Discover text models and inference profiles available from a Bedrock region."""

from __future__ import annotations

from typing import Any

import boto3
from botocore.config import Config

from app.logs import get_logger
from app.security.aws_errors import expired_login_method
from app.settings import Settings

log = get_logger("mini_agent.models")


def discover_models(settings: Settings, region: str) -> dict[str, Any]:
    """List catalog entries; listing does not establish invocation permission."""
    client = boto3.Session(profile_name=settings.bedrock_profile or None).client(
        "bedrock",
        region_name=region,
        config=Config(connect_timeout=5, read_timeout=20, retries={"max_attempts": 1}),
    )
    try:
        items: list[dict[str, str]] = []
        warnings: list[str] = []
        try:
            for model in client.list_foundation_models(byOutputModality="TEXT").get(
                "modelSummaries", []
            ):
                if model.get("inputModalities") and "TEXT" not in model["inputModalities"]:
                    continue
                if model.get("responseStreamingSupported") is False:
                    continue
                if model.get("modelId"):
                    items.append(
                        {
                            "id": model["modelId"],
                            "name": model.get("modelName", model["modelId"]),
                            "kind": "foundation",
                            "status": model.get("modelLifecycle", {}).get("status", "UNKNOWN"),
                        }
                    )
        except Exception as exc:
            if expired_login_method(exc):
                raise
            log.warning("Bedrock model catalog unavailable: %s: %s", type(exc).__name__, exc)
            warnings.append(f"Foundation model list unavailable ({type(exc).__name__}).")
        try:
            paginator = client.get_paginator("list_inference_profiles")
            for page in paginator.paginate():
                for profile in page.get("inferenceProfileSummaries", []):
                    ident = profile.get("inferenceProfileId", "")
                    if profile.get("type") == "APPLICATION":
                        ident = profile.get("inferenceProfileArn", ident)
                    if ident:
                        items.append(
                            {
                                "id": ident,
                                "name": profile.get("inferenceProfileName", ident),
                                "kind": "profile",
                                "status": profile.get("status", "UNKNOWN"),
                            }
                        )
        except Exception as exc:
            if expired_login_method(exc):
                raise
            log.warning(
                "Bedrock inference profile list unavailable: %s: %s", type(exc).__name__, exc
            )
            warnings.append(f"Inference profile list unavailable ({type(exc).__name__}).")
        unique = {item["id"]: item for item in items}
        ordered = sorted(
            unique.values(),
            key=lambda item: (
                not item["id"].startswith("in."),
                item["name"].casefold(),
                item["id"],
            ),
        )
        return {"models": ordered, "warnings": warnings, "region": region}
    finally:
        client.close()
