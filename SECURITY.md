# Security

## Scope

autopoiesis places orders against an Alpaca **paper** account. The security properties it
claims are:

- credentials are read only from `${XDG_CONFIG_HOME:-$HOME/.config}/autopoiesis/env` (the pre-rename `min-agent` path is still honoured as a fallback), never
  from the repository, and are never written to the journal, logs, or `doctor` output;
- no order reaches the broker except through the Guardian, which has no bypass;
- a non-paper base URL is refused before a broker client is constructed;
- no model output is ever executed as code or shell.

A way to break any of these is a vulnerability.

## Reporting

Do **not** open a public issue for a vulnerability, and do not paste credentials anywhere.
Use GitHub's private vulnerability reporting on this repository ("Security" tab → "Report a
vulnerability"). Include the commit, the command, and what you observed. You should get an
acknowledgement within a week.

If you have accidentally published an Alpaca key, revoke it in the Alpaca dashboard first;
deleting the text does not un-publish it.

## Not in scope

Losses on a paper account, a strategy that performs badly, or the agent refusing a trade.
