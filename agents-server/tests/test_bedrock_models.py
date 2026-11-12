"""Model discovery uses the configured Bedrock region without live AWS calls."""

from typing import Any

import pytest
from botocore.exceptions import ClientError, LoginRefreshRequired

from app import bedrock_models
from app.settings import Settings


class FakePaginator:
    def paginate(self) -> list[dict[str, Any]]:
        return [
            {
                "inferenceProfileSummaries": [
                    {
                        "inferenceProfileId": "in.example.chat-v1",
                        "inferenceProfileName": "India chat",
                        "status": "ACTIVE",
                        "type": "SYSTEM_DEFINED",
                    },
                    {
                        "inferenceProfileId": "global.example.chat-v1",
                        "inferenceProfileName": "Global chat",
                        "status": "ACTIVE",
                        "type": "SYSTEM_DEFINED",
                    },
                ]
            },
            {
                "inferenceProfileSummaries": [
                    {
                        "inferenceProfileId": "custom",
                        "inferenceProfileArn": (
                            "arn:aws:bedrock:ap-south-1:111122223333:"
                            "application-inference-profile/example"
                        ),
                        "inferenceProfileName": "Custom",
                        "status": "ACTIVE",
                        "type": "APPLICATION",
                    },
                ]
            },
        ]


class FakeClient:
    def close(self) -> None:
        pass

    def list_foundation_models(self, **kwargs: Any) -> dict[str, Any]:
        assert kwargs == {"byOutputModality": "TEXT"}
        return {
            "modelSummaries": [
                {
                    "modelId": "example.chat-v1",
                    "modelName": "Chat",
                    "inputModalities": ["TEXT"],
                    "responseStreamingSupported": True,
                    "modelLifecycle": {"status": "ACTIVE"},
                },
                {
                    "modelId": "example.no-stream",
                    "inputModalities": ["TEXT"],
                    "responseStreamingSupported": False,
                },
            ]
        }

    def get_paginator(self, operation: str) -> FakePaginator:
        assert operation == "list_inference_profiles"
        return FakePaginator()


def test_discovery_includes_india_and_paginated_profiles(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[tuple[str | None, str]] = []

    class FakeSession:
        def __init__(self, profile_name: str | None) -> None:
            self.profile = profile_name

        def client(self, service: str, region_name: str, **_kwargs: Any) -> FakeClient:
            assert service == "bedrock"
            created.append((self.profile, region_name))
            return FakeClient()

    monkeypatch.setattr(bedrock_models.boto3, "Session", FakeSession)
    catalog = bedrock_models.discover_models(Settings(bedrock_profile="dev"), "ap-south-1")
    ids = [item["id"] for item in catalog["models"]]
    assert ids[0] == "in.example.chat-v1"
    assert "example.chat-v1" in ids and "global.example.chat-v1" in ids
    assert "arn:aws:bedrock:ap-south-1:111122223333:application-inference-profile/example" in ids
    assert "example.no-stream" not in ids
    assert created == [("dev", "ap-south-1")]
    assert catalog["warnings"] == []


def test_partial_catalog_failure_does_not_hide_other_profiles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class PartialClient(FakeClient):
        def list_foundation_models(self, **_kwargs: Any) -> dict[str, Any]:
            raise ClientError(
                {"Error": {"Code": "AccessDeniedException", "Message": "private-detail"}},
                "ListFoundationModels",
            )

    class FakeSession:
        def __init__(self, profile_name: str | None) -> None:
            pass

        def client(self, *_args: Any, **_kwargs: Any) -> PartialClient:
            return PartialClient()

    monkeypatch.setattr(bedrock_models.boto3, "Session", FakeSession)
    catalog = bedrock_models.discover_models(Settings(), "ap-south-1")
    assert any(item["id"] == "in.example.chat-v1" for item in catalog["models"])
    assert catalog["warnings"] and "private-detail" not in str(catalog)


def test_expired_signin_does_not_silently_return_empty_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ExpiredClient(FakeClient):
        def list_foundation_models(self, **_kwargs: Any) -> dict[str, Any]:
            raise LoginRefreshRequired()

    class FakeSession:
        def __init__(self, profile_name: str | None) -> None:
            pass

        def client(self, *_args: Any, **_kwargs: Any) -> ExpiredClient:
            return ExpiredClient()

    monkeypatch.setattr(bedrock_models.boto3, "Session", FakeSession)
    with pytest.raises(LoginRefreshRequired):
        bedrock_models.discover_models(Settings(), "ap-south-1")


def test_chat_model_does_not_force_temperature() -> None:
    from unittest.mock import patch

    from app.agent import build_model
    from app.settings import Settings

    with patch("langchain_aws.ChatBedrockConverse") as model:
        build_model(Settings(model_id="in.moonshotai.kimi-k3", model_region="ap-south-1"))
    assert "temperature" not in model.call_args.kwargs
