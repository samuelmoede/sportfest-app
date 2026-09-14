"""Offline tests for authorization and the untrusted agent-output boundary."""

import copy
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "codex_issue", Path(__file__).resolve().parents[1] / ".github/scripts/codex_issue.py")
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)


class CodexIssueWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.event = {"action": "created", "sender": {"login": "owner", "type": "User"},
                      "issue": {"state": "open", "number": 1, "body": "task"},
                      "comment": {"body": "@codex bitte umsetzen", "user": {"login": "owner"}}}

    def test_owner_comment_and_new_issue_are_accepted(self):
        self.assertTrue(guard.authorized(self.event, "owner", "owner"))
        del self.event["comment"]
        self.event["action"] = "opened"
        self.event["issue"].update(user={"login": "owner"}, title="@codex bitte umsetzen")
        self.assertTrue(guard.authorized(self.event, "owner", "owner"))

    def test_external_actor_pr_bot_closed_and_edits_are_rejected(self):
        cases = [({"action": "edited"}), ({"sender": {"login": "owner", "type": "Bot"}})]
        self.assertFalse(guard.authorized(self.event, "stranger", "owner"))
        for change in cases:
            event = copy.deepcopy(self.event)
            event.update(change)
            self.assertFalse(guard.authorized(event, "owner", "owner"))
        for change in ({"pull_request": {"url": "example"}}, {"state": "closed"}):
            event = copy.deepcopy(self.event)
            event["issue"].update(change)
            self.assertFalse(guard.authorized(event, "owner", "owner"))

    def test_issue_mention_does_not_retrigger_on_unrelated_comment(self):
        self.event["issue"]["body"] = "@codex bitte umsetzen"
        for body in ("thanks", "@codex-other", "email@codex", "@@codex"):
            self.event["comment"]["body"] = body
            self.assertFalse(guard.authorized(self.event, "owner", "owner"))

    def test_protected_paths_and_traversal_are_rejected(self):
        for path in (".github/workflows/ci.yml", "AGENTS.md", "CLAUDE.md", "data/sportfest.db",
                     "app/../data/x", "app//x", "app/AGENTS.md", "app/.env", "app/x.db",
                     "/app/x.py", "app\\x.py", "app/x\ny.py", "docker-compose.yml",
                     "app/sub/.git/config", "docs/secrets.key"):
            with self.subTest(path=path):
                self.assertFalse(guard.allowed_path(path))

    def test_text_changes_and_deletions_are_accepted(self):
        bundle = {"base": "a" * 40, "files": [
            {"path": "app/services/example.py", "mode": "100644", "content": "print('ok')"},
            {"path": "tests/old.py", "mode": "100644", "content": None}]}
        self.assertEqual(guard.validate(bundle), bundle)

    def test_symlinks_duplicates_binary_size_and_invalid_base_are_rejected(self):
        item = {"path": "app/test.py", "mode": "100644", "content": "ok"}
        for change in ({"mode": "120000"}, {"mode": "160000"}, {"content": "x\0y"},
                       {"content": "x" * (guard.MAX_BYTES + 1)}):
            with self.assertRaises(ValueError):
                guard.validate({"base": "a" * 40, "files": [{**item, **change}]})
        with self.assertRaises(ValueError):
            guard.validate({"base": "main", "files": []})
        with self.assertRaises(ValueError):
            guard.validate({"base": "a" * 40, "files": [item, item]})

    def test_publisher_rejects_non_develop_ancestry_before_writes(self):
        bundle = {"base": "a" * 40, "files": [
            {"path": "app/test.py", "mode": "100644", "content": "ok"}]}
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            filename = Path(folder) / "changes.json"
            filename.write_text(json.dumps(bundle), encoding="utf-8")
            with patch.dict(guard.os.environ, {"GITHUB_ACTOR": "owner", "REPO_OWNER": "owner"}), \
                    patch.object(guard, "api", return_value={"status": "diverged"}) as api:
                with self.assertRaises(ValueError):
                    guard.publish(filename, self.event)
                api.assert_called_once_with("GET", f"compare/{'a' * 40}...develop")

    def test_publisher_only_creates_feature_ref_and_develop_draft(self):
        import json
        import tempfile
        bundle = {"base": "a" * 40, "files": [
            {"path": "app/test.py", "mode": "100644", "content": "ok"}]}
        replies = [{"status": "ahead"}, {"tree": {"sha": "old-tree"}},
                   {"sha": "new-tree"}, {"sha": "new-commit"}, {},
                   {"base": {"ref": "develop"}, "html_url": "https://example.test/pr/1"}]
        with tempfile.TemporaryDirectory() as folder:
            filename = Path(folder) / "changes.json"
            filename.write_text(json.dumps(bundle), encoding="utf-8")
            env = {"GITHUB_ACTOR": "owner", "REPO_OWNER": "owner", "GITHUB_RUN_ID": "12",
                   "GITHUB_RUN_ATTEMPT": "1", "GITHUB_REPOSITORY": "owner/repo",
                   "GITHUB_STEP_SUMMARY": str(Path(folder) / "summary.md")}
            with patch.dict(guard.os.environ, env), patch.object(guard, "api", side_effect=replies) as api:
                guard.publish(filename, self.event)
                calls = api.call_args_list
                self.assertEqual([c.args[1] for c in calls], [f"compare/{'a' * 40}...develop",
                    "git/commits/" + "a" * 40, "git/trees", "git/commits", "git/refs", "pulls"])
                self.assertEqual(calls[4].args[2]["ref"], "refs/heads/codex/issue-1-run-12-1")
                self.assertEqual(calls[5].args[2]["base"], "develop")
                self.assertTrue(calls[5].args[2]["draft"])


if __name__ == "__main__":
    unittest.main()
