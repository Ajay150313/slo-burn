# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| 0.3.x   | ✅ Yes    |
| < 0.3   | ❌ No     |

## Reporting a vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Email **security@your-org.com** with:

- What the vulnerability is
- Steps to reproduce it
- Potential impact
- Any suggested fix

You'll get a response within 48 hours. We follow responsible disclosure — patch first, disclosure after.

## Important: never put credentials in slos.yaml

`slo-burn` reads API keys from environment variables only. Always use `${VAR_NAME}` syntax:

```yaml
# ✅ correct
notify:
  - type: pagerduty
    integration_key: ${PD_KEY}

# ❌ never do this
notify:
  - type: pagerduty
    integration_key: "abc123def456"
```

If you've accidentally committed a credential, rotate it immediately. Git history is permanent.
