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


def test_commit_changes_posts_create_commit_on_branch(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "new.csv").write_text("a,b\n")
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
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

    oids = github_commit.commit_changes(
        repo,
        repo="owner/data",
        branch="main",
        message="chore(data): test",
        token="t0ken",
        transport=httpx.MockTransport(handler),
    )
    assert oids == ["abc123"]
    assert requests[0].headers["authorization"] == "bearer t0ken"
    payload = json.loads(requests[0].content)["variables"]["input"]
    assert payload["branch"] == {"repositoryNameWithOwner": "owner/data", "branchName": "main"}
    assert payload["expectedHeadOid"] == head
    assert payload["message"] == {"headline": "chore(data): test"}
    additions = payload["fileChanges"]["additions"]
    assert [a["path"] for a in additions] == ["new.csv"]
    assert base64.b64decode(additions[0]["contents"]) == b"a,b\n"


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
