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


def test_model(settings: Settings, model_id: str, region: str) -> dict[str, Any]:
    """Ask the model for a few tokens over ConverseStream (what chats use) and time it.

    Returns ``ok`` with the time to the first event and to the end, or the Bedrock error code
    and message (secrets masked) so the user sees why a model cannot be used.
    """
    import time

    from botocore.exceptions import ClientError

    from app.security.redaction import redact_text

    client = boto3.Session(profile_name=settings.bedrock_profile or None).client(
        "bedrock-runtime",
        region_name=region,
        config=Config(
            connect_timeout=5, read_timeout=settings.model_read_timeout, retries={"max_attempts": 1}
        ),
    )
    start = time.monotonic()
    first: float | None = None
    text = ""
    stop = ""
    try:
        response = client.converse_stream(
            modelId=model_id,
            messages=[{"role": "user", "content": [{"text": "Reply with the word OK."}]}],
            inferenceConfig={"maxTokens": 16},
        )
        for event in response["stream"]:
            if first is None:
                first = time.monotonic() - start
            delta = event.get("contentBlockDelta", {}).get("delta", {})
            text += delta.get("text", "")
            stop = event.get("messageStop", {}).get("stopReason", stop)
    except Exception as exc:
        method = expired_login_method(exc)
        if method:
            command = "aws sso login" if method == "sso" else "aws login"
            return {
                "ok": False,
                "code": "SignInRequired",
                "message": f"Run {command}, then test again.",
            }
        if isinstance(exc, ClientError):
            error = exc.response.get("Error", {})
            code, message = error.get("Code", "ClientError"), error.get("Message", "")
        else:
            code, message = type(exc).__name__, str(exc)
        log.warning("model test %s in %s failed: %s: %s", model_id, region, code, message)
        return {"ok": False, "code": code, "message": redact_text(message)[:400]}
    total = time.monotonic() - start
    log.info(
        "model test %s in %s ok: first event %.1fs, done %.1fs",
        model_id,
        region,
        first or total,
        total,
    )
    return {
        "ok": True,
        "first_token_s": round(first or total, 2),
        "total_s": round(total, 2),
        "reply": text.strip()[:40],
        "stop_reason": stop,
    }
