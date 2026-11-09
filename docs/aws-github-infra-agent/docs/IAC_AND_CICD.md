# Infrastructure as Code and CI/CD Changes

## Default for multi-resource infrastructure changes

Prefer `propose IaC -> PR -> validate -> produce plan -> review exact plan -> approve -> apply through protected pipeline -> verify` rather than issuing a long chain of direct Boto3 calls. Direct Boto3 remains useful for low-risk operational actions such as a bounded ECS redeploy.

## Terraform strategy

1. Agent reads target repo, state organization, IaC conventions and allowed modules (under read-only GitHub token).
2. Agent proposes patch in a new branch; GitHub broker enforces path and repo allowlists.
3. CI runs `fmt`, `validate`, static security scanning and policy checks on pinned Terraform version.
4. CI produces a **specific immutable plan artifact** (avoid revealing secrets in logs and plan storage). Record digest and base Git commit.
5. Reviewers approve the exact plan, costs, security impacts and drift state.
6. Protected GitHub Actions workflow assumes a scoped AWS role via OIDC, applies the approved artifact under lock and verifies postconditions.
7. Any plan refresh, code change, environment shift or drift invalidates prior approval and requires re-review.

## CloudFormation alternative

For teams using CloudFormation or CDK, generate and inspect change sets, review IAM-related capabilities, approve the exact change set ID/digest, execute through scoped CI role, and verify stack events and outputs. Treat replacement/deletion of stateful resources as high risk.

## GitHub Actions OIDC

- Avoid long-lived AWS access keys in repository secrets.
- Restrict AWS role trust to expected GitHub organization/repository, branch/environment and audience conditions.
- Use protected deployment environments, required reviews and separate promotion roles.
- Don't give an agent PR permission to directly modify and execute workflows that can assume elevated roles.

## Drift and rollback

Plan against current state, check for drift shortly before apply, use state locking, and document backups for stateful resources. Terraform `apply` is not an automatic reversible transaction; a rollback may require another planned change and could lose data. Never promise rollback without an explicit tested procedure.

## Reviewer checklist

- [ ] Scope: exact account, region, workspace and resource list.
- [ ] No unexpected resources created/destroyed/replaced.
- [ ] Changes to IAM, KMS, audit, security groups and networking individually reviewed.
- [ ] Monthly cost estimate with uncertainty and maximum spend control.
- [ ] CI role, OIDC conditions and branch protection verified.
- [ ] Plan hash and commit SHA match approval.
- [ ] Verification, failure escalation and restoration steps documented.
