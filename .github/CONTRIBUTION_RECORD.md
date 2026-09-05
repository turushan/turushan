# Contribution record automation

The README displays a number with no activity date. The initial value of 689 comes
from turushan's contribution calendar screenshot. The first successful run checks
all available contribution years before marking the history scan complete.

The workflow runs daily at 06:37 UTC. GitHub may delay scheduled runs. It checks
the seven completed UTC calendar days ending yesterday, allowing for delayed
contribution updates. It excludes today. Every Sunday and every manual run scans
all available years again to catch missed runs and older changes.

The number only increases. A run commits only when the record increases or the
initial history scan completes. No record date, daily counts, repository names,
or API responses are saved in files, commit messages, artifacts, or workflow logs.
The number is a highest observed contribution count; it does not decrease if
GitHub later revises its calendar. Contributions include more than commits.

## One-time setup

1. Create a GitHub personal access token (classic) belonging to turushan with only
   the `read:user` scope. Choose an expiry and replace the secret before it expires.
   No `repo`, `workflow`, or write scope is needed.
2. In this repository's Settings, Secrets and variables, Actions, add the repository
   secret `CONTRIBUTIONS_TOKEN` with that token as its value.
3. Run the **Update contribution record** workflow from the Actions tab.

The script requires `read:user` so it cannot silently substitute public-only
counts. The built-in repository `GITHUB_TOKEN` handles commits; the personal token
is only passed to the calendar step. Tokens for a different account are rejected.
There are no third-party Python dependencies. Tests use synthetic data.

If another session pushes while the job runs, GitHub rejects the job's push and
the next run retries from the new main branch. No force push is used. GitHub may
disable scheduled workflows in a public repository after 60 days without repository
activity; re-enable it from Actions if that happens.

Run checks locally with `python3 -m unittest discover -s tests -v`.

Sources:

- https://docs.github.com/en/graphql/reference/users#contributionscollection
- https://docs.github.com/en/account-and-profile/concepts/contributions-on-your-profile
- https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule
