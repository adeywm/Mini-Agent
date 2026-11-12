from pathlib import Path

import pytest

from app.db import ConnectionIn, ModelSelection, Store


def test_model_selection_survives_reopening_database(tmp_path: Path) -> None:
    path = tmp_path / "settings.db"
    store = Store(path)
    assert store.get_model_selection() is None
    choice = ModelSelection(model_id="in.example.chat-v1", region="ap-south-1")
    store.set_model_selection(choice)
    assert Store(path).get_model_selection() == choice
    changed = ModelSelection(model_id="global.example.chat-v1", region="us-east-1")
    store.set_model_selection(changed)
    assert Store(path).get_model_selection() == changed


def test_connections_and_chats(tmp_path: Path) -> None:
    store = Store(tmp_path / "t.db")
    aws = store.add_connection(
        ConnectionIn(kind="aws", name="personal-aws", aws_profile="dev", aws_region="ap-south-1")
    )
    gh = store.add_connection(
        ConnectionIn(kind="github", name="me", github_user="adeywm", max_level=2)
    )
    assert [c.name for c in store.list_connections()] == ["personal-aws", "me"]
    assert aws.slug == "personal_aws" and aws.max_level == 1 and gh.aws_region is None
    with pytest.raises(ValueError, match="already exists"):
        store.add_connection(ConnectionIn(kind="github", name="me", github_user="x"))
    assert store.delete_connection(aws.id)

    chat = store.create_chat()
    store.touch_chat(chat.id, title="Review latest commit")
    assert store.get_chat(chat.id).title == "Review latest commit"  # type: ignore[union-attr]
    assert [c.id for c in store.list_chats()] == [chat.id]
    assert store.delete_chat(chat.id) and store.get_chat(chat.id) is None


@pytest.mark.parametrize(
    "data",
    [
        {"kind": "aws", "name": "x"},  # no region
        {"kind": "aws", "name": "Bad Name", "aws_region": "ap-south-1"},
        {"kind": "aws", "name": "x", "aws_region": "ap-south-1", "aws_profile": "a b"},
        {"kind": "github", "name": "x"},  # no user
        {"kind": "github", "name": "x", "github_user": "a", "max_level": 4},
    ],
)
def test_invalid_connections(data: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        ConnectionIn.model_validate(data)
