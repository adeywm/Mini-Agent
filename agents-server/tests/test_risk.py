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


def test_aws_change_levels() -> None:
    def level(service: str, operation: str) -> int | None:
        return aws_risk("aws_change", {"service": service, "operation": operation}).level

    assert level("ec2", "ModifyInstanceAttribute") == WRITE
    assert level("opensearchserverless", "DeleteCollection") == DELETE
    assert level("iam", "CreateUser") == DELETE  # IAM is always L3
    assert level("ec2", "DescribeInstances") is None  # reads go through aws_read
    assert level("secretsmanager", "GetSecretValue") is None


def test_change_tool_only_for_change_levels() -> None:
    from app.db import Connection
    from app.tools.aws import local_tools

    def names(max_level: int) -> list[str]:
        conn = Connection(
            id="c", created_at="t", kind="aws", name="dev", max_level=max_level,
            aws_region="ap-south-1",
        )  # fmt: skip
        return [t.name for t in local_tools(conn)]

    assert "aws_change" not in names(1)
    assert "aws_change" in names(2) and "aws_change" in names(3)
