"""Tests for the Pulse pipeline. Run with: python -m unittest discover pipeline/tests"""

from __future__ import annotations

import json
import sys
import threading
import unittest
from datetime import date
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import card  # noqa: E402
import config  # noqa: E402
import evidence as ev  # noqa: E402
import output  # noqa: E402
import signals  # noqa: E402
import synthesize as syn  # noqa: E402

FIXTURES = HERE / "fixtures"
EVIDENCE = (FIXTURES / "mock_evidence.md").read_text(encoding="utf-8")
REDDIT_LINE = ('- "Honestly the eval is the product now \u2014 nobody trusts the leaderboards" '
               '\U0001F7E0 u/evalnerd on r/MachineLearning · 1,315 upvotes \u2014 '
               'https://www.reddit.com/r/MachineLearning/comments/abc/x/def \u2014 cluster 2 \u2014 top comment')


class EvidenceTests(unittest.TestCase):
    def test_parse_reddit_quote_line(self):
        q = ev.parse_quote_line(REDDIT_LINE)
        self.assertEqual(q["text"], "Honestly the eval is the product now \u2014 nobody trusts the leaderboards")
        self.assertEqual(q["who"], "u/evalnerd on r/MachineLearning")
        self.assertEqual(q["count"], "1,315 upvotes")
        self.assertEqual(q["url"], "https://www.reddit.com/r/MachineLearning/comments/abc/x/def")
        self.assertEqual(q["cluster"], 2)
        self.assertEqual(q["label"], "top comment")

    def test_parse_mock_comments(self):
        quotes = ev.community_comments(EVIDENCE)
        self.assertGreaterEqual(len(quotes), 2)
        x_post = next(q for q in quotes if q["url"].startswith("https://x.com/"))
        self.assertEqual(x_post["count"], "200 likes")
        self.assertTrue(x_post["who"].startswith("@example"))

    def test_footer_and_range(self):
        rows = ev.footer_sources(EVIDENCE)
        labels = [r["label"] for r in rows]
        self.assertIn("Reddit", labels)
        self.assertEqual(ev.date_range(EVIDENCE)[1] > ev.date_range(EVIDENCE)[0], True)

    def test_synthesis_input_respects_budget_and_keeps_quotes(self):
        text = ev.synthesis_input(EVIDENCE, 2500)
        self.assertLessEqual(len(text), 2600)
        self.assertIn("Top Community Comments", text)
        self.assertNotIn("Before you write the report", text)
        self.assertNotIn("DEGRADED RUN WARNING", text)

    def test_quote_matching_ignores_punctuation(self):
        self.assertTrue(ev.quote_in_evidence("Third one we've bought.  They last", EVIDENCE))
        self.assertFalse(ev.quote_in_evidence("This sentence was never in the evidence at all", EVIDENCE))


class ProseTests(unittest.TestCase):
    def test_clean_prose_removes_dashes(self):
        self.assertEqual(syn.clean_prose("Evals matter \u2014 a lot"), "Evals matter, a lot")
        self.assertEqual(syn.clean_prose("Scores rose 3\u20135 points"), "Scores rose 3 to 5 points")
        self.assertNotIn("\u2013", syn.clean_prose("a \u2013 b"))

    def test_clean_prose_truncates_on_word(self):
        out = syn.clean_prose("word " * 100, 40)
        self.assertLessEqual(len(out), 40)
        self.assertTrue(out.endswith("…"))


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.agent = None
        self.raw = {
            "summary": "Shoppers \u2014 mostly happy.",
            "stories": [
                {"headline": "Classic model keeps selling.", "body": "Owners say it lasts \u2014 a third purchase for some.",
                 "quote": {"text": "Third one we've bought. They last.", "who": "Comment on Amazon",
                           "count": "12 votes", "url": "https://www.amazon.com/dp/B0MOCK00X2"},
                 "odds": None,
                 "sources": ["https://www.amazon.com/dp/B0MOCK00X2", "https://evil.example/not-in-evidence"]},
                {"headline": "Made-up story", "body": "No sources.", "quote": {"text": "I invented this quote entirely myself", "who": "x", "count": "", "url": ""},
                 "odds": None, "sources": ["https://evil.example/2"]},
            ],
            "patterns": ["People like durable things \u2013 obviously."],
            "watch": None,
        }

    def test_untraceable_links_and_quotes_are_dropped(self):
        brief = syn.validate_brief(self.raw, EVIDENCE, self.agent)
        self.assertEqual(len(brief["stories"]), 1)
        story = brief["stories"][0]
        self.assertEqual(story["sources"], ["https://www.amazon.com/dp/B0MOCK00X2"])
        self.assertIsNotNone(story["quote"])
        self.assertEqual(story["headline"], "Classic model keeps selling")
        for text in (brief["summary"], story["body"], *brief["patterns"]):
            self.assertNotIn("\u2014", text)
            self.assertNotIn("\u2013", text)

    def test_duplicate_quotes_removed(self):
        self.raw["stories"][1] = dict(self.raw["stories"][0])
        brief = syn.validate_brief(self.raw, EVIDENCE, self.agent)
        quotes = [s["quote"] for s in brief["stories"] if s["quote"]]
        self.assertEqual(len(quotes), 1)


class SignalTests(unittest.TestCase):
    results = [
        {"url": "https://reddit.com/a", "source": "reddit", "engagement": {"score": 2000}, "published_at": "2026-11-20"},
        {"url": "https://reddit.com/b", "source": "reddit", "engagement": {"score": 20}, "published_at": "2026-11-20"},
        {"url": "https://youtube.com/c", "source": "youtube", "engagement": {"views": 900000}, "published_at": "2026-11-25"},
        {"url": "https://youtube.com/d", "source": "youtube", "engagement": {"views": 1000}, "published_at": "2026-10-01"},
    ]

    def test_heat_is_within_source(self):
        heat = signals.result_heat(self.results)
        self.assertGreater(heat["https://reddit.com/a"], heat["https://reddit.com/b"])
        self.assertEqual(heat["https://reddit.com/a"], heat["https://youtube.com/c"])

    def test_pulse_window_and_peak(self):
        heat = signals.result_heat(self.results)
        line, peak = signals.pulse(self.results, heat, date(2026, 11, 26), 30)
        self.assertEqual(len(line), 30)
        self.assertEqual(max(line), 1.0)
        self.assertEqual(peak, "2026-11-20")  # two posts that day beat one big video

    def test_count_labels(self):
        self.assertEqual(signals.count_label("reddit", {"score": 1315}), "1.3k upvotes")
        self.assertEqual(signals.count_label("youtube", {"views": 48213}), "48k views")
        self.assertEqual(signals.count_label("polymarket", {"volume": 66000}), "")
        self.assertEqual(signals.count_label("hackernews", {"points": 1}), "1 point")


class ConfigTests(unittest.TestCase):
    def test_repo_config_loads(self):
        cfg = config.load(HERE.parents[1] / "pulse.toml")
        self.assertGreaterEqual(len(cfg.topics), 1)
        self.assertEqual(len({t.id for t in cfg.topics}), len(cfg.topics))

    def test_bad_depth_rejected(self):
        path = FIXTURES / "_bad.toml"
        path.write_text('[engine]\ndepth = "huge"\n[[topic]]\nname = "x"\n', encoding="utf-8")
        try:
            with self.assertRaises(config.ConfigError):
                config.load(path)
        finally:
            path.unlink()


class FakeClaude(BaseHTTPRequestHandler):
    replies: list = []
    requests: list = []

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        FakeClaude.requests.append({"body": body, "headers": {k.lower(): v for k, v in self.headers.items()}})
        status, payload = FakeClaude.replies.pop(0)
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        if status == 529:
            self.send_header("retry-after", "0.01")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class ClaudeCallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), FakeClaude)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.old_url = syn.API_URL
        syn.API_URL = f"http://127.0.0.1:{cls.server.server_port}/v1/messages"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        syn.API_URL = cls.old_url

    def setUp(self):
        FakeClaude.replies, FakeClaude.requests = [], []

    def _ok(self, obj):
        return 200, {"content": [{"type": "thinking", "thinking": "..."}, {"type": "text", "text": json.dumps(obj)}],
                     "stop_reason": "end_turn"}

    def test_request_shape_and_retry(self):
        FakeClaude.replies = [(529, {"type": "error"}), self._ok({"headline": "H", "bullets": ["a"]})]
        out = syn.call_claude("sk-test", "claude-sonnet-5-5", "low", "sys", "user", syn.OVERVIEW_SCHEMA, 100)
        self.assertEqual(out["headline"], "H")
        self.assertEqual(len(FakeClaude.requests), 2)
        req = FakeClaude.requests[-1]
        self.assertEqual(req["headers"]["x-api-key"], "sk-test")
        self.assertIn("anthropic-version", req["headers"])
        self.assertEqual(req["body"]["output_config"]["format"]["type"], "json_schema")
        self.assertEqual(req["body"]["output_config"]["effort"], "low")

    def test_schema_refusal_falls_back_to_plain_json(self):
        FakeClaude.replies = [(400, {"error": {"message": "output_config.format not supported"}}),
                              self._ok({"headline": "H", "bullets": []})]
        syn.call_claude("k", "m", "low", "sys", "u", syn.OVERVIEW_SCHEMA, 100)
        self.assertNotIn("format", FakeClaude.requests[-1]["body"]["output_config"])

    def test_full_topic_brief_through_fake_api(self):
        reply = {
            "summary": "A short summary.",
            "stories": [{"headline": "Durable classic", "body": "People keep rebuying it.",
                         "quote": {"text": "Third one we've bought. They last.", "who": "Comment on Amazon",
                                   "count": "12 votes", "url": "https://www.amazon.com/dp/B0MOCK00X2"},
                         "odds": None, "sources": ["https://www.amazon.com/dp/B0MOCK00X2"]}],
            "patterns": [], "watch": None,
        }
        FakeClaude.replies = [self._ok(reply)]
        cfg = config.Config()
        brief = syn.claude_brief("Test", "test", "2026-11-26", 30, EVIDENCE, None, cfg, "k")
        self.assertEqual(len(brief["stories"]), 1)
        sent = FakeClaude.requests[0]["body"]["messages"][0]["content"]
        self.assertIn("<evidence>", sent)
        self.assertIn("Top Community Comments", sent)


class OutputTests(unittest.TestCase):
    def test_write_day_and_index(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            for d in ("2025-01-01", "2026-11-25"):
                (out / "days").mkdir(exist_ok=True)
                (out / "days" / f"{d}.json").write_text(json.dumps({"date": d, "overview": {"headline": d}, "topics": []}))
            day = {"date": "2026-11-26", "title": "Pulse", "overview": {"headline": "Today"}, "topics": []}
            output.write_day(out, day, keep_days=365)
            index = json.loads((out / "index.json").read_text())
            self.assertEqual(index["latest"], "2026-11-26")
            self.assertEqual([d["date"] for d in index["days"]], ["2026-11-26", "2026-11-25"])
            self.assertFalse((out / "days" / "2025-01-01.json").exists())


class CardTests(unittest.TestCase):
    def test_combined_pulse_normalised(self):
        day = {"topics": [{"pulse": [0, 1, 0.5]}, {"pulse": [0, 0.5, 1]}, {"pulse": [0, 0, 0]}]}
        line = card.combined_pulse(day)
        self.assertEqual(len(line), 3)
        self.assertAlmostEqual(max(line), 1.0)

    def test_render_card(self):
        try:
            import PIL  # noqa: F401
        except ImportError:
            self.skipTest("Pillow not installed")
        import tempfile
        from PIL import Image
        day = {"date": "2026-11-26", "title": "Pulse", "overview": {"headline": "A headline " * 12},
               "topics": [{"pulse": [i / 29 for i in range(30)]}]}
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "og.png"
            self.assertTrue(card.render(day, out, "news.example.com"))
            self.assertEqual(Image.open(out).size, (1200, 630))


if __name__ == "__main__":
    unittest.main()
