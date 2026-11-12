import pytest

from app.risk import DELETE, READ, WRITE, aws_cli_risk, aws_risk, github_risk


@pytest.mark.parametrize(
    ("tool", "args", "level"),
    [
        ("list_commits", {}, READ),
        ("get_file_contents", {}, READ),
        ("issue_read", {"method": "get"}, READ),
        ("get_me", {}, READ),
        ("create_issue", {}, WRITE),
        ("issue_write", {"method": "update"}, WRITE),
        ("create_pull_request", {}, WRITE),
        ("push_files", {}, WRITE),
        ("merge_pull_request", {}, WRITE),
        ("delete_file", {}, DELETE),
        ("label_write", {"method": "delete"}, DELETE),
        ("something_new", {}, WRITE),  # unknown never auto-runs
    ],
)
def test_github_risk(tool: str, args: dict[str, str], level: int) -> None:
    assert github_risk(tool, args).level == level


@pytest.mark.parametrize(
    ("command", "level"),
    [
        ("aws ec2 describe-instances --region ap-south-1", READ),
        ("aws s3api list-buckets", READ),
        ("aws s3 ls s3://bucket --recursive --summarize", READ),
        ("aws ec2 modify-instance-attribute --instance-id i-1", WRITE),
        ("aws ec2 terminate-instances --instance-ids i-1", DELETE),
        ("aws s3 rm s3://bucket/key", DELETE),
    ],
)
def test_aws_cli_risk(command: str, level: int) -> None:
    assert aws_cli_risk(command).level == level


@pytest.mark.parametrize(
    "command",
    [
        "aws ec2 describe-instances; rm -rf /",
        "aws ec2 describe-instances | sh",
        "aws s3 presign s3://b/k",
        "aws secretsmanager get-secret-value --secret-id x",
        "aws",
        "aws ec2 describe-instances 'unterminated",
    ],
)
def test_aws_cli_blocked(command: str) -> None:
    assert aws_cli_risk(command).blocked


def test_aws_tools() -> None:
    assert aws_risk("aws___run_script", {}).blocked
    assert aws_risk("aws___read_documentation", {}).level == READ
    assert aws_risk("aws_read", {"service": "s3", "operation": "ListBuckets"}).level == READ
    assert aws_risk("aws_read", {"service": "ec2", "operation": "RunInstances"}).blocked
    assert aws_risk("aws_read", {"service": "ssm", "operation": "GetParameter"}).blocked
    assert aws_risk("aws___call_aws", {"cli_command": "aws s3api list-buckets"}).level == READ


def test_aws_change_tools_match_their_level() -> None:
    def risk(tool: str, service: str, operation: str) -> tuple[int | None, str]:
        r = aws_risk(tool, {"service": service, "operation": operation})
        return r.level, r.reason

    assert risk("aws_write", "ec2", "RunInstances")[0] == WRITE
    assert risk("aws_write", "rds", "ModifyDBInstance")[0] == WRITE
    assert risk("aws_critical", "ec2", "TerminateInstances")[0] == DELETE
    assert risk("aws_critical", "opensearchserverless", "DeleteCollection")[0] == DELETE
    assert risk("aws_critical", "iam", "CreateRole")[0] == DELETE
    assert risk("aws_critical", "iam", "ListRoles")[0] == DELETE  # IAM reads are L3 too
    # The wrong tool for the operation's level is refused, naming the right tool.
    assert risk("aws_write", "ec2", "TerminateInstances") == (
        None,
        "ec2:TerminateInstances is L3 delete/IAM; use aws_critical",
    )
    assert risk("aws_critical", "ec2", "RunInstances")[0] is None
    assert risk("aws_write", "iam", "CreateUser")[0] is None
    assert risk("aws_write", "ec2", "DescribeInstances")[0] is None
    assert risk("aws_critical", "secretsmanager", "GetSecretValue")[0] is None
    assert aws_risk("aws_read", {"service": "iam", "operation": "ListRoles"}).blocked


def test_change_tools_always_available() -> None:
    from app.db import Connection
    from app.tools.aws import local_tools

    def names(max_level: int) -> set[str]:
        conn = Connection(
            id="c",
            created_at="t",
            kind="aws",
            name="dev",
            max_level=max_level,
            aws_region="ap-south-1",
        )
        return {t.name for t in local_tools(conn)} & {"aws_write", "aws_critical"}

    # Every level gets them; the approval card elevates when needed.
    assert names(1) == names(3) == {"aws_write", "aws_critical"}


def test_describe_operation_lists_required_parameters() -> None:
    import json

    from app.tools.aws import describe_operation

    info = json.loads(describe_operation("opensearchserverless", "DeleteCollection"))
    assert info["access"] == "DELETE" and info["params"]["id"] == "string"
    assert "clientToken?" in info["params"]
    assert "unknown operation" in describe_operation("ec2", "NoSuchThing")
