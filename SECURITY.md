# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.1.x   | Yes       |

## Reporting a Vulnerability

If you discover a security vulnerability, please report it responsibly:

1. **Do not** open a public GitHub issue
2. Email **minhtribk12@gmail.com** with details
3. Include steps to reproduce, impact assessment, and any suggested fixes
4. You will receive a response within 48 hours

## Security Considerations

- **API keys** are stored in `~/.cra/secrets.env` with file-system permissions only
- **No secrets** are logged, committed, or included in error messages
- **Prompt injection defense** is built into the review pipeline (random delimiters, instruction anchoring)
- **All user input and configuration** is validated using Pydantic models before use
- **No telemetry** -- the tool does not phone home or collect usage data
