# AWS IAM, STS and Credential Broker

## Identity hierarchy

1. **Runtime identity:** trusted FastAPI/agent worker principal; can read configuration, publish job status, request scoped credentials by server-defined capability ID. No blanket infrastructure admin rights.
2. **Discovery identity:** an investigation role used by AWS MCP and read-only SDK adapters. It may still need explicit **deny** for sensitive `GetSecretValue`, SSM secure parameters, decrypted keys, and harmful read-looking APIs.
3. **Execution roles:** one role per bounded capability/environment (`sandbox_ecs_redeploy`, `sandbox_terraform_apply`, etc.), never selected by model-supplied ARN. Trust policies limit which broker principal can assume them.
4. **Human identity:** SSO/OIDC authenticated, member of allowed approval group; must not derive approval from chat text alone.
5. **GitHub identity:** repository-scoped GitHub App installation token. Separate discovery and change permissions where possible.

## AssumeRole sequence

`Agent request -> policy/broker -> registry capability mapping -> STS AssumeRole with session metadata -> short-lived AWS SDK client -> one approved action -> verification -> discard credentials`.

The STS session must be as narrow as the operation allows. Distinguish session policies, permissions boundaries and organization SCPs. All should provide defense in depth. Where a service cannot support resource-level restrictions, mark the action as higher risk and require stronger environmental controls.

## AWS MCP specifics

The official AWS MCP Server uses the caller's IAM identity for downstream authorization. Via-MCP IAM condition keys can distinguish MCP-routed API calls, but they are **not** a universal authorization mechanism for other Boto3/CLI routes. Connect model-visible `aws___run_script` with only investigation-safe identity. Do not give that model-visible endpoint a write-capable principal and assume string matching of scripts will block dangerous operations.

For multi-account/headless operation, use supported SigV4 proxy/client integration and **server-controlled credential routing**. Multiple local profiles are a development convenience; production roles must not be selected by arbitrary model text.

## Sandbox build sequence

1. Create separate sandbox account or isolated organizational account with billing alerts.
2. Provision a discovery role with only explicitly allowed reads and sensitive-data denials.
3. Provision one ECS redeploy role scoped to a tag and resource ARN (where supported).
4. Create STS trust from broker runtime identity only; no wildcard external principals.
5. Configure CloudTrail, CloudWatch logs, budget notifications, SCP/permissions boundaries as appropriate.
6. Simulate allowed and denied API calls with IAM tooling where possible **and test actual denial** in sandbox.
7. Retain role mapping on server, under change control; prevent agents from modifying its own policy files or trust configuration.

## Authorization checklist

- [ ] API action is actually needed.
- [ ] Account/region resolved from registry and verified by STS.
- [ ] Role policy allows only needed action/resource.
- [ ] AssumeRole trust is limited to broker.
- [ ] No `AdministratorAccess` or wildcard delegation to agent.
- [ ] `iam:PassRole`, policy attachments, auditing deletion/disablement, destructive backups are excluded by default.
- [ ] Session lifetime, auditable principal tags and credentials storage are managed.
- [ ] Failing role assumption results in denied task (no fallback to broader credentials).

## Official references

- https://docs.aws.amazon.com/agent-toolkit/latest/userguide/security_iam_service-with-iam.html
- https://docs.aws.amazon.com/agent-toolkit/latest/userguide/getting-started-aws-mcp-server.html
- https://docs.aws.amazon.com/IAM/latest/UserGuide/id_credentials_temp.html
