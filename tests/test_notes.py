import json
import os
import socket
import tempfile
import threading
import time
import unittest
from unittest import mock

from relaylib import notes, sessions
from relaylib.errors import RelayError

NOW = 1_800_000_000.0


class FakeInbox:
    """A Unix socket server in a short temporary folder that records what each connection sent."""

    def __init__(self, folder):
        self.path = os.path.join(folder, "in.sock")
        self.got = []
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(self.path)
        self.server.listen(5)
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        while True:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            with conn:
                data = b""
                while chunk := conn.recv(65536):
                    data += chunk
                self.got.append(data.decode())

    def wait(self, count=1):
        until = time.monotonic() + 2
        while len(self.got) < count and time.monotonic() < until:
            time.sleep(0.01)
        return self.got

    def close(self):
        self.server.close()


class NotesTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir="/tmp")  # short: macOS caps socket paths near 104 bytes
        self.addCleanup(temp.cleanup)
        self.tmp = temp.name
        patch = mock.patch.dict(os.environ, {"RELAY_HOME": os.path.join(self.tmp, "h")})
        patch.start()
        self.addCleanup(patch.stop)

    def inbox(self):
        fake = FakeInbox(self.tmp)
        self.addCleanup(fake.close)
        return fake

    def log(self, provider="claude", sid="s1"):
        with open(notes.log_path(provider, sid)) as f:
            return json.load(f)

    def hold_lock(self):
        os.makedirs(notes.folder(), exist_ok=True)
        return sessions._lock(os.path.join(notes.folder(), ".lock"), 1)

    def test_queued_note_file_name_and_modes(self):
        note, message = notes.send("claude", "s1", "check the build", None, NOW)
        self.assertEqual((note["status"], note["text"], note["at"], message), ("queued", "check the build", NOW, None))
        self.assertEqual(os.stat(notes.folder()).st_mode & 0o777, 0o700)
        self.assertEqual(os.stat(notes.log_path("claude", "s1")).st_mode & 0o777, 0o600)
        odd = notes.log_path("claude", "../../x/..")
        self.assertEqual(os.path.dirname(odd), notes.folder())
        notes.send("claude", "../../x/..", "hi", None, NOW)
        self.assertTrue(os.path.exists(odd))
        self.assertEqual(sorted(os.listdir(notes.folder())), sorted([".lock", os.path.basename(odd),
                                                                     os.path.basename(notes.log_path("claude", "s1"))]))

    def test_text_rules(self):
        for bad in ("", "   \n", None, 5, "x" * 2001):
            with self.assertRaises(RelayError):
                notes.send("claude", "s1", bad, None, NOW)
        self.assertEqual(notes.send("claude", "s1", "x" * 2000, None, NOW)[0]["status"], "queued")

    def test_posting_to_the_inbox(self):
        fake = self.inbox()
        note, message = notes.send("claude", "s1", "stop after this task", fake.path, NOW)
        self.assertEqual((note["status"], message), ("posted", None))
        [sent] = fake.wait()
        self.assertEqual(json.loads(sent), {"type": "user", "message": {"role": "user", "content":
                                            notes.PREFIX + "\nstop after this task"}})
        self.assertTrue(sent.endswith("\n"))
        self.assertEqual([n["status"] for n in self.log()], ["posted"])
        self.assertEqual(notes.take("claude", "s1", NOW), [])                    # posted is never handed again

    def test_failed_posts_leave_the_note_queued(self):
        fake = self.inbox()
        plain = os.path.join(self.tmp, "plain")
        open(plain, "w").close()
        link = os.path.join(self.tmp, "link.sock")
        os.symlink(fake.path, link)
        dead = os.path.join(self.tmp, "dead.sock")
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(dead)
        s.close()                                                                 # nobody listens: refused
        for inbox in (os.path.join(self.tmp, "missing.sock"), plain, link, dead):
            self.assertEqual(notes.send("claude", "s1", "hi", inbox, NOW)[0]["status"], "queued", inbox)
        real = os.lstat
        other = lambda p: os.stat_result((*real(p)[:4], os.getuid() + 1, *real(p)[5:]))
        with mock.patch.object(notes.os, "lstat", side_effect=other):
            self.assertEqual(notes.send("claude", "s1", "hi", fake.path, NOW)[0]["status"], "queued")
        with mock.patch.object(notes.socket.socket, "connect", side_effect=socket.timeout("slow")):
            self.assertEqual(notes.send("claude", "s1", "hi", fake.path, NOW)[0]["status"], "queued")
        self.assertEqual(notes.send("codex", "s1", "hi", fake.path, NOW)[0]["status"], "queued")  # never Codex
        self.assertEqual(fake.got, [])

    def test_take_delivers_once_and_remove_only_queued(self):
        a, _ = notes.send("claude", "s1", "one", None, NOW)
        b, _ = notes.send("claude", "s1", "two", None, NOW + 1)
        notes.remove("claude", "s1", b["id"], NOW + 2)
        taken = notes.take("claude", "s1", NOW + 3)
        self.assertEqual([(n["text"], n["status"], n["status_at"]) for n in taken], [("one", "delivered", NOW + 3)])
        self.assertEqual(notes.take("claude", "s1", NOW + 4), [])
        self.assertEqual(notes.render(taken), [notes.PREFIX + "\none"])
        for missing in (a["id"], b["id"], "nope"):
            with self.assertRaises(notes.Conflict) as caught:
                notes.remove("claude", "s1", missing, NOW + 5)
            self.assertEqual([n["status"] for n in caught.exception.notes], ["delivered"])
        self.assertEqual(notes.take("codex", "other", NOW), [])

    def test_concurrent_sends_all_survive(self):
        threads = [threading.Thread(target=notes.send, args=("claude", "s1", f"n{i}", None, NOW)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(n["text"] for n in self.log()), sorted(f"n{i}" for i in range(20)))

    def test_a_busy_lock(self):
        notes.send("claude", "s1", "waiting", None, NOW)
        self.addCleanup(os.close, self.hold_lock())
        fake = self.inbox()
        started = time.monotonic()
        self.assertEqual(notes.take("claude", "s1", NOW), [])
        self.assertLess(time.monotonic() - started, 0.5)
        with mock.patch.object(notes, "SEND_WAIT_S", 0.2), self.assertRaises(RelayError):
            notes.send("claude", "s1", "hi", fake.path, NOW)
        self.assertEqual(fake.got, [])
        self.assertEqual([n["status"] for n in self.log()], ["queued"])

    def test_a_send_while_the_hook_holds_the_lock_waits_and_stays_queued(self):
        notes.send("claude", "s1", "first", None, NOW)
        fd = self.hold_lock()                                                     # the hook is taking notes
        result = {}
        sender = threading.Thread(target=lambda: result.update(sent=notes.send("claude", "s1", "second", None, NOW)))
        sender.start()
        time.sleep(0.3)
        self.assertTrue(sender.is_alive())                                        # waiting for the lock
        with open(notes.log_path("claude", "s1")) as f:
            logged = json.load(f)
        for n in logged:
            n.update(status="delivered", status_at=NOW)                           # what take writes, under the lock
        with open(notes.log_path("claude", "s1"), "w") as f:
            json.dump(logged, f)
        os.close(fd)
        sender.join(3)
        self.assertEqual(result["sent"][0]["status"], "queued")
        self.assertEqual([(n["text"], n["status"]) for n in self.log()], [("first", "delivered"), ("second", "queued")])

    def test_storage_failures_stop_the_send_before_any_post(self):
        fake = self.inbox()
        os.makedirs(notes.log_path("claude", "s1"))                             # a directory where the log goes
        with self.assertRaises(OSError):
            notes.send("claude", "s1", "hi", fake.path, NOW)
        os.rmdir(notes.log_path("claude", "s1"))
        with mock.patch.object(notes.json, "dump", side_effect=OSError("disk full")), self.assertRaises(OSError):
            notes.send("claude", "s1", "hi", fake.path, NOW)
        os.chmod(notes.folder(), 0o500)
        self.addCleanup(os.chmod, notes.folder(), 0o700)
        with self.assertRaises(OSError):
            notes.send("claude", "s1", "hi", fake.path, NOW)
        self.assertEqual(fake.got, [])

    def test_a_failed_record_after_a_post_is_left_out_of_the_log(self):
        fake = self.inbox()
        notes.send("claude", "s1", "earlier", None, NOW)
        real = os.replace
        with mock.patch.object(notes.os, "replace", side_effect=OSError("rename failed")):
            note, message = notes.send("claude", "s1", "now", fake.path, NOW)
        self.assertEqual((note["status"], message), ("posted", notes.NOT_RECORDED))
        self.assertEqual(len(fake.wait()), 1)
        self.assertEqual([n["text"] for n in self.log()], ["earlier"])
        self.assertEqual([n["text"] for n in notes.take("claude", "s1", NOW)], ["earlier"])
        self.assertEqual(sorted(os.listdir(notes.folder())), sorted([".lock", os.path.basename(
            notes.log_path("claude", "s1"))]))                                     # no temporary files left
        self.assertIs(os.replace, real)

    def test_a_note_can_carry_its_own_prefix(self):  # mobile-hub D5
        hub = "Note from the owner, relayed by claude session h1 from the relay hub:"
        fake = self.inbox()
        note, _ = notes.send("claude", "s1", "go ahead", fake.path, NOW, prefix=hub)
        self.assertEqual(note["status"], "posted")
        [sent] = fake.wait()
        self.assertEqual(json.loads(sent)["message"]["content"], hub + "\ngo ahead")
        notes.send("claude", "s2", "queued one", None, NOW, prefix=hub)
        notes.send("claude", "s2", "plain one", None, NOW)
        taken = notes.take("claude", "s2", NOW)
        self.assertEqual(notes.render(taken), [hub + "\nqueued one", notes.PREFIX + "\nplain one"])
        self.assertNotIn("prefix", self.log("claude", "s2")[1])

    def test_take_waits_out_a_send_that_is_posting(self):
        fake = self.inbox()
        posting, release = threading.Event(), threading.Event()
        real = notes.post

        def slow(inbox, text, prefix=None):
            posting.set()
            release.wait(2)
            return real(inbox, text, prefix)
        result = {}
        with mock.patch.object(notes, "post", side_effect=slow):
            sender = threading.Thread(target=lambda: result.update(sent=notes.send("claude", "s1", "hi", fake.path,
                                                                                    NOW)))
            sender.start()
            self.assertTrue(posting.wait(2))
            self.assertEqual(notes.take("claude", "s1", NOW), [])               # the send holds the lock
            release.set()
            sender.join()
        self.assertEqual(result["sent"][0]["status"], "posted")
        self.assertEqual(notes.take("claude", "s1", NOW), [])
        self.assertEqual([n["status"] for n in self.log()], ["posted"])

    def test_list_and_prune(self):
        notes.send("claude", "s1", "old", None, NOW - 8 * 86400)
        notes.send("claude", "s1", "new", None, NOW)
        notes.send("claude", "s2", "gone", None, NOW - 8 * 86400)
        self.assertEqual([n["text"] for n in notes.list_for("claude", "s1", NOW)], ["new"])
        notes.prune(NOW)
        self.assertEqual([n["text"] for n in self.log()], ["new"])
        self.assertFalse(os.path.exists(notes.log_path("claude", "s2")))
        self.assertEqual(notes.list_for("claude", "nobody", NOW), [])


if __name__ == "__main__":
    unittest.main()
