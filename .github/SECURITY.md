# Security policy

## Reporting a vulnerability

Please report security issues privately through
[GitHub private vulnerability reporting](https://github.com/randallawhitlock/sales-call-simulator/security/advisories/new).
Don't open a public issue for anything that could be exploited.

Include what you found, how to reproduce it, and what an attacker could do with it. You'll get
an acknowledgment within 3 business days and a status update within 10.

## Scope

This project is pre-alpha and runs locally. Reports are most useful when they cover:

- Hidden scenario data (answer keys, customer truth) reaching the browser or the AI customer's
  prompt
- API keys or other secrets exposed by the app, its logs or its build output
- Prompt injection that makes the AI customer or judge ignore scenario facts or grading rules
- Dependency vulnerabilities that affect the shipped code

## Data

The simulator uses synthetic people and fictional plans only. Never put real beneficiary data,
real Medicare numbers or production credentials into it.

## Supported versions

Only the latest commit on `main` is supported until the first tagged release.
