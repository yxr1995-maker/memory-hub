#!/usr/bin/env python3
"""P1 review-pool API tests: GET /api/pending + POST /api/review over a live loopback server."""
import importlib.util
import json
import os
import pathlib
import shutil
import sys
import threading
import time
import unittest
import urllib.request
import urllib.error

ROOT = pathlib.Path(__file__).resolve().parents[1]
TMP = None
_SAVED_ENV = {}


def setUpModule():
    global TMP, _SAVED_ENV
    import tempfile
    TMP = pathlib.Path(tempfile.mkdtemp(prefix="review-api-"))
    hub_scripts = TMP / "hub" / "scripts"
    hub_scripts.mkdir(parents=True)
    for name in ("publish.sh", "lib.sh", "secure_replace.py"):
        shutil.copy2(ROOT / "scripts" / name, hub_scripts / name)
    (TMP / "staging" / "pages").mkdir(parents=True)
    (TMP / "data").mkdir(parents=True)
    wiki = TMP / "wiki"
    (wiki / "drafts" / "memoryhub").mkdir(parents=True)
    (wiki / "notes").mkdir(parents=True)
    (wiki / "index.md").write_text("", encoding="utf-8")
    (wiki / "log.md").write_text("", encoding="utf-8")
    for _k in ("WIKI_PATH", "MEMORY_HUB_STAGING", "MEMORY_HUB_DATA",
               "MEMORY_HUB_EXPERIENCE_DB"):
        _SAVED_ENV[_k] = os.environ.get(_k)
    os.environ["WIKI_PATH"] = str(wiki)
    os.environ["MEMORY_HUB_STAGING"] = str(TMP / "staging")
    os.environ["MEMORY_HUB_DATA"] = str(TMP / "data")
    os.environ["MEMORY_HUB_EXPERIENCE_DB"] = str(TMP / "data" / "experience.sqlite3")
    sys.path.insert(0, str(ROOT))


def tearDownModule():
    import shutil as _sh
    _sh.rmtree(str(TMP), ignore_errors=True)
    for _k, _v in _SAVED_ENV.items():
        if _v is None:
            os.environ.pop(_k, None)
        else:
            os.environ[_k] = _v
    try:
        sys.path.remove(str(ROOT))
    except ValueError:
        pass


CANDIDATE_BODY = """---
title: 'review candidate SLUG'
type: concept
created: '2026-09-17'
tags:
  - memoryhub
  - test
sources:
  - codex://sessions
abstract: 'candidate abstract SLUG'
status: candidate
conflict_target: 'TARGET'
---

# candidate SLUG
"""


def make_candidate(slug, target):
    text = CANDIDATE_BODY.replace("SLUG", slug).replace("TARGET", target)
    p = pathlib.Path(os.environ["MEMORY_HUB_STAGING"]) / "pages" / slug
    p.write_text(text, encoding="utf-8")
    return p


def make_payload(goal):
    return {
        "schema_version": 1,
        "collection_id": "fixture",
        "event_kind": "episode",
        "synthetic": True,
        "goal": goal,
        "narrative": "narrative for " + goal,
        "source_kind": "synthetic_fixture",
        "conditions": {"task_kind": "creative"},
        "outcome": {"status": "observed", "verification": "unknown"},
        "artifacts": [],
        "source_refs": [{"kind": "fixture", "ref": "dev/card-01"}],
    }


def load_server():
    path = ROOT / "scripts" / "server.py"
    spec = importlib.util.spec_from_file_location("memory_hub_review_server", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class ReviewApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = load_server()
        from scripts.automation_core.experience import AccessContext, initialize
        from scripts.automation_core.experience.store import record, connect
        cls.db = pathlib.Path(os.environ["MEMORY_HUB_EXPERIENCE_DB"])
        initialize(cls.db)
        cls.owner = AccessContext(subject_id="review-owner",
                                  allowed_collections=("fixture",),
                                  role="owner",
                                  artifact_roots=(str(TMP),))
        cls.ep_approve = record(cls.db, cls.owner, make_payload("approve me goal alpha"),
                                idempotency_key="k-approve")["event_id"]
        cls.ep_reject = record(cls.db, cls.owner, make_payload("reject me goal beta"),
                               idempotency_key="k-reject")["event_id"]
        with connect(cls.db, write=True) as c:
            c.execute("UPDATE experience_versions SET review_required=1")
        wiki = pathlib.Path(os.environ["WIKI_PATH"])
        (wiki / "drafts" / "memoryhub" / "cand-approve.md").write_text("old target\n", encoding="utf-8")
        make_candidate("cand-approve.md", "drafts/memoryhub/cand-approve.md")
        make_candidate("cand-reject.md", "drafts/memoryhub/cand-reject.md")
        (wiki / "notes" / "toreject.md").write_text(
            "---\ntitle: 'to reject'\ntype: note\nstatus: candidate\n---\n\nbody\n",
            encoding="utf-8")
        family, sockaddr = cls.server.resolve_loopback_bind_target("127.0.0.1", 0)
        cls.httpd = cls.server.create_loopback_server(family, sockaddr)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        time.sleep(0.2)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def api(self, path, payload=None):
        url = "http://127.0.0.1:" + str(self.port) + path
        data = json.dumps(payload).encode() if payload is not None else None
        method = "POST" if payload is not None else "GET"
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            try:
                return e.code, json.loads(body)
            except ValueError:
                return e.code, {"error": body}

    def test_01_pending_lists_all_sources(self):
        code, d = self.api("/api/pending")
        self.assertEqual(code, 200)
        by_id = {(i["source"], i["id"]) for i in d["items"]}
        self.assertIn(("candidate", "cand-approve.md"), by_id)
        self.assertIn(("candidate", "cand-reject.md"), by_id)
        self.assertIn(("experience", self.ep_approve), by_id)
        self.assertIn(("experience", self.ep_reject), by_id)
        self.assertIn(("page", "notes/toreject.md"), by_id)
        code, ov = self.api("/api/overview")
        self.assertEqual(code, 200)
        self.assertIsInstance(ov["pending_candidates"], int)
        self.assertIn("pending_details", ov)

    def test_02_candidate_approve_publishes(self):
        code, d = self.api("/api/review", {"source": "candidate", "id": "cand-approve.md",
                                           "decision": "approve"})
        self.assertEqual(code, 200, d)
        self.assertTrue(d.get("ok"))
        wiki = pathlib.Path(os.environ["WIKI_PATH"])
        target = wiki / "drafts" / "memoryhub" / "cand-approve.md"
        self.assertIn("candidate cand-approve.md", target.read_text(encoding="utf-8"))

    def test_03_candidate_reject_moves_to_rejected(self):
        code, d = self.api("/api/review", {"source": "candidate", "id": "cand-reject.md",
                                           "decision": "reject"})
        self.assertEqual(code, 200, d)
        staging = pathlib.Path(os.environ["MEMORY_HUB_STAGING"])
        self.assertFalse((staging / "pages" / "cand-reject.md").exists())
        self.assertTrue((staging / "rejected" / "cand-reject.md").is_file())

    def test_04_experience_approve_clears_flag(self):
        code, d = self.api("/api/review", {"source": "experience", "id": self.ep_approve,
                                           "decision": "approve"})
        self.assertEqual(code, 200, d)
        import sqlite3
        with sqlite3.connect(self.db) as conn:
            n = conn.execute("SELECT count(*) FROM experience_versions WHERE event_id=? "
                             "AND review_required=1", (self.ep_approve,)).fetchone()[0]
        self.assertEqual(n, 0)

    def test_05_experience_reject_not_recalled(self):
        code, d = self.api("/api/review", {"source": "experience", "id": self.ep_reject,
                                           "decision": "reject"})
        self.assertEqual(code, 200, d)
        from scripts.automation_core.experience.retrieval import recall
        res = recall(self.db, self.owner, task="reject me goal beta")
        ids = {i["episode_id"] for i in res["items"]}
        self.assertNotIn(self.ep_reject, ids)

    def test_06_page_reject_hides_from_scan(self):
        code, d = self.api("/api/review", {"source": "page", "id": "notes/toreject.md",
                                           "decision": "reject"})
        self.assertEqual(code, 200, d)
        wiki = pathlib.Path(os.environ["WIKI_PATH"])
        self.assertIn("status: rejected", (wiki / "notes" / "toreject.md").read_text(encoding="utf-8"))
        paths = [p["path"] for p in self.server.scan_pages()]
        self.assertNotIn("notes/toreject.md", paths)
        code, d = self.api("/api/review", {"source": "page", "id": "notes/toreject.md",
                                           "decision": "approve"})
        self.assertEqual(code, 400)

    def test_07_bad_requests(self):
        self.assertEqual(self.api("/api/review", {"source": "bogus", "id": "x",
                                                  "decision": "approve"})[0], 400)
        self.assertEqual(self.api("/api/review", {"source": "candidate", "id": "../evil.md",
                                                  "decision": "reject"})[0], 400)
        self.assertEqual(self.api("/api/review", {"source": "candidate", "id": "missing.md",
                                                  "decision": "reject"})[0], 404)

    def test_08_single_approve_leaves_unrelated(self):
        # M6 评审 #2：单条 approve 只发布指定文件，无关 staging 页原样保留
        wiki = pathlib.Path(os.environ["WIKI_PATH"])
        (wiki / "drafts" / "memoryhub" / "solo-approve.md").write_text(
            "old solo\n", encoding="utf-8")
        make_candidate("solo-approve.md", "drafts/memoryhub/solo-approve.md")
        unrelated = pathlib.Path(os.environ["MEMORY_HUB_STAGING"]) / "pages" / "unrelated.md"
        unrelated.write_text(
            "---\ntitle: 'unrelated'\ntype: note\ncreated: '2026-09-17'\n"
            "tags:\n  - test\nsources:\n  - codex://sessions\n"
            "abstract: 'unrelated abstract'\nstatus: active\n---\n\n# unrelated\n",
            encoding="utf-8")
        before = unrelated.read_bytes()
        code, d = self.api("/api/review", {"source": "candidate", "id": "solo-approve.md",
                                           "decision": "approve"})
        self.assertEqual(code, 200, d)
        self.assertTrue(unrelated.is_file(), "unrelated staging 页不应被连带发布")
        self.assertEqual(unrelated.read_bytes(), before, "unrelated 内容必须字节不变")
        self.assertFalse((wiki / "queries" / "unrelated.md").exists())
        target = wiki / "drafts" / "memoryhub" / "solo-approve.md"
        self.assertIn("candidate solo-approve.md", target.read_text(encoding="utf-8"))

    def test_09_reject_archives_without_overwrite(self):
        # M6 评审 #3：rejected/ 下已有同名旧记录时用唯一名归档，两份都保留
        staging = pathlib.Path(os.environ["MEMORY_HUB_STAGING"])
        (staging / "rejected").mkdir(parents=True, exist_ok=True)
        old = staging / "rejected" / "dup-reject.md"
        old.write_text("OLD REJECTED CONTENT\n", encoding="utf-8")
        make_candidate("dup-reject.md", "drafts/memoryhub/dup-reject.md")
        new_text = (staging / "pages" / "dup-reject.md").read_text(encoding="utf-8")
        code, d = self.api("/api/review", {"source": "candidate", "id": "dup-reject.md",
                                           "decision": "reject"})
        self.assertEqual(code, 200, d)
        self.assertEqual(old.read_text(encoding="utf-8"), "OLD REJECTED CONTENT\n")
        moved = TMP / d["moved"]
        self.assertTrue(moved.is_file())
        self.assertNotEqual(moved, old)
        self.assertEqual(moved.read_text(encoding="utf-8"), new_text)
        code, pend = self.api("/api/pending")
        self.assertNotIn(("candidate", "dup-reject.md"),
                         {(i["source"], i["id"]) for i in pend["items"]})

    def test_10_experience_reject_leaves_pending(self):
        # M6 评审 #4：驳回后待审池不再含该条目；旧 revision 标记不污染当前视图
        from scripts.automation_core.experience import AccessContext
        from scripts.automation_core.experience.store import record, connect
        from scripts.automation_core.experience.revisions import revise_understanding
        db = pathlib.Path(os.environ["MEMORY_HUB_EXPERIENCE_DB"])
        owner = AccessContext(subject_id="review-owner",
                              allowed_collections=("fixture",),
                              role="owner",
                              artifact_roots=(str(TMP),))
        ep = record(db, owner, make_payload("leave pending goal gamma"),
                    idempotency_key="k-leave-gamma")["event_id"]
        with connect(db, write=True) as c:
            c.execute("UPDATE experience_versions SET review_required=1 WHERE event_id=?",
                      (ep,))
        code, pend = self.api("/api/pending")
        self.assertIn(("experience", ep),
                      {(i["source"], i["id"]) for i in pend["items"]})
        code, d = self.api("/api/review", {"source": "experience", "id": ep,
                                           "decision": "reject"})
        self.assertEqual(code, 200, d)
        code, pend = self.api("/api/pending")
        self.assertNotIn(("experience", ep),
                         {(i["source"], i["id"]) for i in pend["items"]})
        ep2 = record(db, owner, make_payload("old revision goal delta"),
                     idempotency_key="k-oldrev-delta")["event_id"]
        revise_understanding(db, owner, target_id=ep2, base_revision=1,
                             patch={"explicit_reason": "test revision"},
                             reason="test", evidence_ids=[ep2])
        with connect(db, write=True) as c:
            c.execute("UPDATE experience_versions SET review_required=1 "
                      "WHERE event_id=? AND revision=1", (ep2,))
        code, pend = self.api("/api/pending")
        self.assertNotIn(("experience", ep2),
                         {(i["source"], i["id"]) for i in pend["items"]})

    def test_11_page_approve_activates_and_leaves_pending(self):
        # M6 评审 #6：wiki candidate 页 approve 转 active 并离开待审池；
        # 非 candidate 页的 approve/reject 都应 400
        wiki = pathlib.Path(os.environ["WIKI_PATH"])
        (wiki / "notes" / "toapprove.md").write_text(
            "---\ntitle: 'to approve'\ntype: note\nstatus: candidate\n---\n\nbody\n",
            encoding="utf-8")
        code, d = self.api("/api/review", {"source": "page", "id": "notes/toapprove.md",
                                           "decision": "approve"})
        self.assertEqual(code, 200, d)
        self.assertIn("status: active",
                      (wiki / "notes" / "toapprove.md").read_text(encoding="utf-8"))
        code, pend = self.api("/api/pending")
        self.assertNotIn(("page", "notes/toapprove.md"),
                         {(i["source"], i["id"]) for i in pend["items"]})
        self.assertEqual(self.api("/api/review", {"source": "page", "id": "notes/toapprove.md",
                                                  "decision": "approve"})[0], 400)
        # page+reject 是在售页合法入口：active 页驳回返回 200 并标记 rejected
        code, d = self.api("/api/review", {"source": "page", "id": "notes/toapprove.md",
                                           "decision": "reject"})
        self.assertEqual(code, 200, d)
        self.assertIn("status: rejected",
                      (wiki / "notes" / "toapprove.md").read_text(encoding="utf-8"))

    def test_12_status_sh_zero_candidates_exit_zero(self):
        # M6 评审 #5：无 candidate 的常见状态下 status.sh 必须 exit 0 并继续输出
        import subprocess as _sp
        import tempfile as _tf
        tmp = pathlib.Path(_tf.mkdtemp(prefix="status-zero-"))
        try:
            stg = tmp / "staging"
            (stg / "pages").mkdir(parents=True)
            (stg / "pages" / "plain.md").write_text(
                "---\ntitle: plain\nstatus: active\n---\n\nhi\n", encoding="utf-8")
            wk = tmp / "wiki"
            wk.mkdir()
            (wk / "index.md").write_text("", encoding="utf-8")
            (wk / "log.md").write_text("", encoding="utf-8")
            (wk / "plain.md").write_text(
                "---\ntitle: plain\n---\n\nhi\n", encoding="utf-8")
            env = dict(os.environ, MEMORY_HUB_STAGING=str(stg), WIKI_PATH=str(wk),
                       MEMORY_HUB_DATA=str(tmp / "data"),
                       MEMORY_HUB_EXPERIENCE_DB=str(tmp / "data" / "no-such-db.sqlite3"),
                       CODEX_SESSIONS_DIR=str(tmp / "sessions"))
            r = _sp.run(["bash", str(ROOT / "scripts" / "status.sh")],
                        capture_output=True, text=True, timeout=120, env=env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("待审候选(staging/pages): 0", r.stdout)
        finally:
            import shutil as _sh2
            _sh2.rmtree(str(tmp), ignore_errors=True)

    def test_13_overview_pending_unified(self):
        # M6 评审 #7：overview 待审数 = /api/pending 总数，details 按来源细分
        code, pend = self.api("/api/pending")
        self.assertEqual(code, 200)
        code, ov = self.api("/api/overview")
        self.assertEqual(code, 200)
        self.assertIsInstance(ov["pending_candidates"], int)
        self.assertEqual(ov["pending_candidates"], pend["total"])
        det = ov["pending_details"]
        self.assertEqual(det["candidates"] + det["experience_review"] + det.get("pages", 0),
                         pend["total"])

    def test_14_page_reject_active_wiki_page(self):
        # 收口 #1：普通 active 在售页 reject → 200，frontmatter 变 rejected，
        # scan_pages 与 /api/pending 即时消失
        wiki = pathlib.Path(os.environ["WIKI_PATH"])
        (wiki / "notes" / "sellpage.md").write_text(
            "---\ntitle: 'sell page'\ntype: note\nstatus: active\n---\n\nbody\n",
            encoding="utf-8")
        code, d = self.api("/api/review", {"source": "page", "id": "notes/sellpage.md",
                                           "decision": "reject"})
        self.assertEqual(code, 200, d)
        self.assertIn("status: rejected",
                      (wiki / "notes" / "sellpage.md").read_text(encoding="utf-8"))
        self.assertNotIn("notes/sellpage.md",
                         [p["path"] for p in self.server.scan_pages()])
        code, pend = self.api("/api/pending")
        self.assertNotIn(("page", "notes/sellpage.md"),
                         {(i["source"], i["id"]) for i in pend["items"]})

    def test_15_candidate_approve_leaves_pending_entirely(self):
        # 收口 #2：一次 candidate approve 后，两个身份都查不到它
        wiki = pathlib.Path(os.environ["WIKI_PATH"])
        (wiki / "drafts" / "memoryhub" / "solo2-approve.md").write_text(
            "old solo2\n", encoding="utf-8")
        make_candidate("solo2-approve.md", "drafts/memoryhub/solo2-approve.md")
        code, d = self.api("/api/review", {"source": "candidate",
                                           "id": "solo2-approve.md",
                                           "decision": "approve"})
        self.assertEqual(code, 200, d)
        self.assertIn("status: active",
                      (wiki / "drafts" / "memoryhub" / "solo2-approve.md").read_text(
                          encoding="utf-8"))
        code, pend = self.api("/api/pending")
        ids = {(i["source"], i["id"]) for i in pend["items"]}
        self.assertNotIn(("candidate", "solo2-approve.md"), ids)
        self.assertNotIn(("page", "drafts/memoryhub/solo2-approve.md"), ids)


if __name__ == "__main__":
    unittest.main()
