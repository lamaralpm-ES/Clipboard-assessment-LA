# Clipboard Health — Bellhaven CRM Reconciliation

Review-first Sales Operations reconciliation pipeline for Bellhaven facilities.

## Final result

The pipeline was run against the live assessment CRM and the current Bellhaven website.

- 34 current Bellhaven website communities identified
- 121 CRM accounts evaluated during the initial reconciliation
- 20 reconciliation proposals reviewed
- All proposals were explicitly reviewed before any CRM write
- Approved changes were applied to the CRM
- Final rerun: **0 open proposals**

The final zero-proposal rerun validates that the corrected CRM state reconciles cleanly against the current website and that previously handled items are not repeatedly proposed.

## What it does

The pipeline:

1. Scrapes every current Bellhaven community and its detail page.
2. Extracts facility name, street, city, state, ZIP, and care offerings.
3. Reads CRM accounts through the assessment API.
4. Matches website facilities to CRM accounts using normalized location and name evidence.
5. Classifies discrepancies as create, update, CHOW, duplicate, or stale/review-needed.
6. Presents every proposed change and supporting evidence in a Streamlit review app.
7. Writes to the CRM only after explicit reviewer approval.
8. Persists reviewer decisions so reruns are safe.

## Matching and CRM conventions

Address/location evidence is weighted more heavily than name similarity because ownership changes can leave the same physical facility under a different name.

Matching uses normalized street, city, state, ZIP, and name evidence with deterministic tie-breaking. Exact physical-location matches are preferred, while name-based matching is constrained by geography to reduce false positives.

### Parent changes and CHOW

When a matched facility belongs to the wrong parent:

- If `lifetime_revenue > 0` **and** `outstanding_ar > 0`, the CHOW SOP applies. The old account is preserved exactly as-is, a new account is created under Bellhaven, and `chow_current_account` on the old account points to the new account.
- Otherwise, the existing account is re-parented directly.

This rule was triggered for **Bellhaven of Marietta** and **Bellhaven of Tiffin**, preserving their historical billing accounts while creating the current Bellhaven accounts.

### Duplicates

Duplicate classification is intentionally conservative. Automatic duplicate/inactive proposals are limited to same-location copies already under the Bellhaven parent.

For a confirmed duplicate, the losing record is marked `Inactive` and its `duplicate_of_account` field points to the surviving account.

Same-location accounts belonging to other parents are not automatically treated as duplicates because they may represent legitimate ownership history.

### Stale Bellhaven accounts

Existing Bellhaven children that no longer appear on the current website are not automatically assigned to another parent. They are marked `Needs Review` with an explanatory note.

This preserves uncertainty rather than inventing an unsupported ownership relationship.

### Ambiguous ownership history

Where location evidence did not support modifying an existing other-parent CRM account, the pipeline favored preserving that historical account and creating the current Bellhaven facility instead.

For example, **Bellhaven at Union Square** was created as a new Bellhaven account because the existing New Albany account had a materially different street address, making an automatic re-parent unsafe.

## Safety and idempotence

Scraping and matching are read-only.

Every proposal has a stable SHA-256 fingerprint derived from its action, target, and payload. Approve/reject decisions and applied API responses are stored in SQLite, and decided fingerprints are suppressed on future runs.

CRM state is re-read on every run so the pipeline evaluates the current system of record rather than assuming a previous state.

After completing the reconciliation, the pipeline was run again and returned **0 open proposals**.

The assessment token is read only from the `CANDIDATE_TOKEN` environment variable and is never committed.

## Review app

The Streamlit app displays each proposed reconciliation action with supporting evidence.

Nothing writes to the CRM automatically. **Approve & apply** is the only path that performs a CRM mutation. Rejected proposals are also persisted so they are not repeatedly surfaced on subsequent runs.

## Run locally

```bash
pip install -r requirements.txt
export CANDIDATE_TOKEN="..."
python dry_run.py
streamlit run app.py