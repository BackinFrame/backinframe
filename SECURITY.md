# Security policy

Please report vulnerabilities **privately** through GitHub's *Report a vulnerability* (Security
tab), or by e-mail to the address in https://backinframe.com/.well-known/security.txt, not in a
public issue.

- First answer within 3 working days; within 10 working days whether we can reproduce it, how
  serious it is and when a fix is expected.
- We publish a GitHub security advisory with the fix and, if you like, name you in it. There is no
  bounty.
- Test only with your own installation or your own accounts on the subscription, do as little as
  needed to show the problem, no denial-of-service or social engineering. If you stay within these
  rules we will not take legal action. We aim to publish within 90 days.
- Installations other people host are theirs: report problems in the code to us, problems in their
  setup to them.

The full policy, in eleven languages: https://backinframe.com/#security

The threat model is described in [docs/how-it-works.md](docs/how-it-works.md#privacy-and-security-model)
and [docs/admin-guide.md](docs/admin-guide.md#10-security). In short:

- topic names are secrets;
- the admin page is meant for your LAN or VPN;
- the photo folder is mounted read-only.

What is checked automatically (OWASP ASVS 5.0 and the OWASP Top 10 2025: headers, CSRF,
authentication throttling, access between recipients, injection, uploads, secrets, dependencies,
container) and how to run it: [docs/security.md](docs/security.md). In short: `pytest -m security`.
