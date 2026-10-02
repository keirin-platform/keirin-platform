"""Commit local changes of a checkout through the GitHub GraphQL API.

Commits created with ``createCommitOnBranch`` are signed by GitHub and show
as "Verified", which lets the data workflow produce signed commits with
nothing but the workflow's GITHUB_TOKEN (no GPG key in CI).

The commit is based on the branch head *at commit time*, not on the checkout:
a collection run takes up to half an hour and other commits (e.g. merged
config changes) may land meanwhile. The changed paths are data files written
only by the collector, so applying them on top of the newer head is safe.
"""

from __future__ import annotations

import base64
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

GRAPHQL_URL = "https://api.github.com/graphql"
# Keep each request comfortably below GitHub's request size limits.
MAX_BATCH_BYTES = 20 * 1024 * 1024

MUTATION = """
mutation($input: CreateCommitOnBranchInput!) {
  createCommitOnBranch(input: $input) { commit { oid url } }
}
"""
HEAD_QUERY = """
query($owner: String!, $name: String!, $ref: String!) {
  repository(owner: $owner, name: $name) { ref(qualifiedName: $ref) { target { oid } } }
}
"""
MAX_ATTEMPTS = 3


@dataclass
class Changes:
    additions: list[str] = field(default_factory=list)
    deletions: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.additions or self.deletions)


def _git(repo_dir: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo_dir), *args], check=True, capture_output=True, text=True
    ).stdout


def list_changes(repo_dir: Path) -> Changes:
    """Changed paths relative to the repository root (renames are not expected)."""
    changes = Changes()
    out = _git(repo_dir, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    for entry in filter(None, out.split("\0")):
        status, path = entry[:2], entry[3:]
        if "D" in status:
            changes.deletions.append(path)
        else:
            changes.additions.append(path)
    return changes


def _batches(repo_dir: Path, changes: Changes) -> list[Changes]:
    batches = [Changes(deletions=list(changes.deletions))]
    size = 0
    for path in sorted(changes.additions):
        file_size = (repo_dir / path).stat().st_size * 4 // 3
        if batches[-1].additions and size + file_size > MAX_BATCH_BYTES:
            batches.append(Changes())
            size = 0
        batches[-1].additions.append(path)
        size += file_size
    return [b for b in batches if b]


def commit_changes(
    repo_dir: Path,
    *,
    repo: str,
    branch: str,
    message: str,
    token: str,
    transport: httpx.BaseTransport | None = None,
) -> list[str]:
    """Commit all working tree changes; returns the created commit oids."""
    changes = list_changes(repo_dir)
    if not changes:
        log.info("nothing to commit")
        return []
    oids = []
    headers = {"Authorization": f"bearer {token}"}
    with httpx.Client(headers=headers, timeout=120, transport=transport) as http:

        def graphql(query: str, variables: dict) -> dict:
            resp = http.post(GRAPHQL_URL, json={"query": query, "variables": variables})
            resp.raise_for_status()
            return resp.json()

        def remote_head() -> str:
            owner, name = repo.split("/", 1)
            body = graphql(
                HEAD_QUERY, {"owner": owner, "name": name, "ref": f"refs/heads/{branch}"}
            )
            return body["data"]["repository"]["ref"]["target"]["oid"]

        head = remote_head()
        batches = _batches(repo_dir, changes)
        for i, batch in enumerate(batches, 1):
            headline = message if len(batches) == 1 else f"{message} ({i}/{len(batches)})"
            file_changes = {
                "additions": [
                    {"path": p, "contents": base64.b64encode((repo_dir / p).read_bytes()).decode()}
                    for p in batch.additions
                ],
                "deletions": [{"path": p} for p in batch.deletions],
            }
            for attempt in range(1, MAX_ATTEMPTS + 1):
                body = graphql(
                    MUTATION,
                    {
                        "input": {
                            "branch": {"repositoryNameWithOwner": repo, "branchName": branch},
                            "expectedHeadOid": head,
                            "message": {"headline": headline},
                            "fileChanges": file_changes,
                        }
                    },
                )
                if not body.get("errors"):
                    break
                if attempt == MAX_ATTEMPTS:
                    raise RuntimeError(f"GraphQL error: {body['errors']}")
                # Most likely the branch moved since we read its head: re-read and retry.
                log.warning("commit attempt %d failed (%s), retrying", attempt, body["errors"])
                head = remote_head()
            commit = body["data"]["createCommitOnBranch"]["commit"]
            head = commit["oid"]
            oids.append(head)
            log.info(
                "committed %d files: %s", len(batch.additions) + len(batch.deletions), commit["url"]
            )
    return oids
