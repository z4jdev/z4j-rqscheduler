# Security Policy

## Reporting a vulnerability

If you believe you have found a security vulnerability in `z4j-rqscheduler`,
**do not open a public GitHub issue**. Email `security@z4j.com` instead.

We acknowledge reports within **48 hours**, provide a preliminary assessment
within **5 business days**, and target fixes within **30 days** (**7 days** for
confirmed critical issues). Reporting timelines, safe harbor, supported-version
policy, and published advisories are maintained in the
[canonical z4j project security policy](https://github.com/z4jdev/z4j/blob/main/SECURITY.md).

## Security-critical surface

This adapter inventories and controls the host application's native scheduler.
Signed commands are bound to the current producer connection and compare the
observed definition before mutation. Dashboard task choices must resolve to
registered application tasks; control must never import arbitrary operator input.
Native state changes, task arguments, manual dispatch and source-confirmed
receipts are package-specific security surfaces. Transport, redaction and
project authorization remain owned by `z4j-core`, `z4j-bare` and the brain.
