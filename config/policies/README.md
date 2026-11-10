# Policy configuration

The running policy engine reads operator rules from the `[[policy.rules]]` entries in
`config/agent.toml`. See `config/agent.example.toml` for the supported format. Built-in
prohibitions and permission checks are enforced in `backend/src/infra_agent/policy/` and
cannot be overridden by those rules.

Files in this directory are not loaded by the application. Keep example or reviewed policy
material here; configure active rules in `config/agent.toml`.
