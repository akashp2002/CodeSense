from git import Repo

from codesense.ingestion import github_loader


def test_create_pull_request_uses_fresh_branch_when_previous_remote_branch_is_ahead(
    tmp_path, monkeypatch
):
    remote_path = tmp_path / "remote.git"
    remote = Repo.init(remote_path, bare=True)
    seed_path = tmp_path / "seed"
    seed = Repo.init(seed_path)
    seed.config_writer().set_value("user", "name", "Test User").release()
    seed.config_writer().set_value("user", "email", "test@example.com").release()
    (seed_path / "base.txt").write_text("base\n", encoding="utf-8")
    seed.index.add(["base.txt"])
    seed.index.commit("initial commit")
    base_branch = seed.active_branch.name
    seed.create_remote("origin", str(remote_path))
    seed.git.push("--set-upstream", "origin", base_branch)
    remote.git.symbolic_ref("HEAD", f"refs/heads/{base_branch}")

    seed.git.checkout("-b", "codesense/refactor-existing")
    (seed_path / "remote-only.txt").write_text("remote\n", encoding="utf-8")
    seed.index.add(["remote-only.txt"])
    seed.index.commit("remote branch advanced")
    seed.git.push("--set-upstream", "origin", "codesense/refactor-existing")

    local_path = tmp_path / "local"
    local = Repo.clone_from(str(remote_path), str(local_path))
    local.config_writer().set_value("user", "name", "Test User").release()
    local.config_writer().set_value("user", "email", "test@example.com").release()
    local.remotes.origin.set_url("https://github.com/example/repo.git")
    local.git.config("--add", "remote.origin.pushurl", str(remote_path))
    local.git.checkout("-b", "codesense/refactor-existing", f"origin/{base_branch}")
    (local_path / "approved.py").write_text("value = 1\n", encoding="utf-8")

    class PullRequest:
        html_url = "https://github.com/example/repo/pull/1"

    class GitHubRepo:
        default_branch = base_branch

        def create_pull(self, *, head, **_kwargs):
            self.head = head
            return PullRequest()

    github_repo = GitHubRepo()

    class GitHubClient:
        def __init__(self, **_kwargs):
            pass

        def get_repo(self, _name):
            return github_repo

    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setattr(github_loader, "Github", GitHubClient)

    pull_request_url = github_loader.create_pull_request(
        str(local_path), "Rename database session provider"
    )

    assert pull_request_url == PullRequest.html_url
    assert github_repo.head.startswith("codesense/refactor-")
    assert github_repo.head != "codesense/refactor-existing"
    assert local.remotes.origin.refs["codesense/refactor-existing"].commit == seed.commit(
        "codesense/refactor-existing"
    )
    assert local.remotes.origin.refs[github_repo.head].commit == local.head.commit
