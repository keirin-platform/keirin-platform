import base64
import json
import subprocess

import httpx

from keirin import github_commit


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def make_repo(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "user.name", "test")
    git(tmp_path, "config", "commit.gpgsign", "false")
    (tmp_path / "old.txt").write_text("old\n")
    (tmp_path / "keep.txt").write_text("keep\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


def test_list_changes(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "old.txt").unlink()
    (repo / "keep.txt").write_text("changed\n")
    (repo / "dir" / "sub").mkdir(parents=True)
    (repo / "dir" / "sub" / "new.csv").write_text("a,b\n")

    changes = github_commit.list_changes(repo)
    assert sorted(changes.additions) == ["dir/sub/new.csv", "keep.txt"]
    assert changes.deletions == ["old.txt"]


class FakeGitHub:
    """Answers the head query and createCommitOnBranch like the GraphQL API."""

    def __init__(self, heads, fail_first_commit=False):
        self.heads = list(heads)  # successive answers of the head query
        self.fail_first_commit = fail_first_commit
        self.mutations = []
        self.requests = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        payload = json.loads(request.content)
        if "createCommitOnBranch" not in payload["query"]:
            oid = self.heads.pop(0) if len(self.heads) > 1 else self.heads[0]
            return httpx.Response(
                200, json={"data": {"repository": {"ref": {"target": {"oid": oid}}}}}
            )
        self.mutations.append(payload["variables"]["input"])
        if self.fail_first_commit and len(self.mutations) == 1:
            return httpx.Response(200, json={"errors": [{"message": "Expected branch head"}]})
        return httpx.Response(
            200,
            json={
                "data": {
                    "createCommitOnBranch": {
                        "commit": {"oid": "abc123", "url": "https://example.invalid/c/abc123"}
                    }
                }
            },
        )


def test_commit_changes_posts_create_commit_on_branch(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "new.csv").write_text("a,b\n")
    github = FakeGitHub(heads=["remote-head"])

    oids = github_commit.commit_changes(
        repo,
        repo="owner/data",
        branch="main",
        message="chore(data): test",
        token="t0ken",
        transport=httpx.MockTransport(github),
    )
    assert oids == ["abc123"]
    assert github.requests[0].headers["authorization"] == "bearer t0ken"
    payload = github.mutations[0]
    assert payload["branch"] == {"repositoryNameWithOwner": "owner/data", "branchName": "main"}
    # Based on the branch head at commit time, not on the (possibly stale) checkout.
    assert payload["expectedHeadOid"] == "remote-head"
    assert payload["message"] == {"headline": "chore(data): test"}
    additions = payload["fileChanges"]["additions"]
    assert [a["path"] for a in additions] == ["new.csv"]
    assert base64.b64decode(additions[0]["contents"]) == b"a,b\n"


def test_commit_changes_retries_when_the_branch_moved(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "new.csv").write_text("a,b\n")
    github = FakeGitHub(heads=["old-head", "new-head"], fail_first_commit=True)

    oids = github_commit.commit_changes(
        repo, repo="o/r", branch="main", message="m", token="t",
        transport=httpx.MockTransport(github),
    )  # fmt: skip
    assert oids == ["abc123"]
    assert [m["expectedHeadOid"] for m in github.mutations] == ["old-head", "new-head"]


def test_commit_changes_noop_without_changes(tmp_path):
    repo = make_repo(tmp_path)

    def handler(request):
        raise AssertionError("must not call the API")

    assert (
        github_commit.commit_changes(
            repo,
            repo="o/r",
            branch="main",
            message="m",
            token="t",
            transport=httpx.MockTransport(handler),
        )
        == []
    )
