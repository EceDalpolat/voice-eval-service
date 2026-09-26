import base64
import json
from datetime import timedelta
from pathlib import Path

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from evaluations.models import Evaluation

from . import audio_factory as af

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "audio"
URL = "/api/evaluations/"


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def payload(**overrides):
    body = {
        "call_id": "call-1",
        "bot_id": "support-bot",
        "turn_id": "t1",
        "language": "tr",
        "stt": {"provider": "demo_stt", "transcript": "siparişim nerede acaba",
                "confidence": 0.93, "latency_ms": 420},
        "tts": {"provider": "demo_tts", "text": "Siparişiniz yarın teslim edilecek.",
                "duration_ms": 2600, "latency_ms": 650},
    }
    body.update(overrides)
    return body


class EvaluationApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def post(self, body):
        return self.client.post(URL, body, format="json")

    def test_accept_happy_path(self):
        res = self.post(payload(audio_base64=b64(af.clean_speech())))
        self.assertEqual(res.status_code, 201, res.content)
        body = res.json()
        self.assertEqual(body["decision"]["action"], "accept")
        self.assertEqual(body["scores"], {"audio": 1.0, "stt": 1.0, "tts": 1.0})
        self.assertEqual(body["quality_score"], 1.0)
        # raw audio is never stored, only a reference
        self.assertNotIn("audio_base64", json.dumps(body["request_payload"]))
        self.assertEqual(body["request_payload"]["audio"]["size_bytes"], len(af.clean_speech()))

    def test_bad_audio_asks_repeat(self):
        res = self.post(payload(audio_base64=b64(af.silence())))
        self.assertEqual(res.json()["decision"]["action"], "ask_repeat")

    def test_low_confidence_retries_stt(self):
        body = payload()
        body["stt"]["confidence"] = 0.3
        decision = self.post(body).json()["decision"]
        self.assertEqual(decision["action"], "retry_stt")
        self.assertEqual(decision["triggered_by"][0]["rule"], "stt.low_confidence")
        self.assertIn("0.3", decision["explanation"])

    def test_tts_audio_via_multipart(self):
        body = payload()
        body["tts"]["duration_ms"] = None
        res = self.client.post(URL, {
            "data": json.dumps(body),
            "audio": open(FIXTURES / "clean_speech.wav", "rb"),
            "tts_audio": open(FIXTURES / "clipped.wav", "rb"),
        }, format="multipart")
        self.assertEqual(res.status_code, 201, res.content)
        decision = res.json()["decision"]
        self.assertEqual(decision["action"], "retry_tts")
        self.assertIn("tts.audio.clipping", [f["rule"] for f in decision["triggered_by"]])

    def test_prior_failures_in_same_call_trigger_fallback(self):
        bad = payload(audio_base64=b64(af.silence()))
        for _ in range(3):
            self.post(bad)
        res = self.post(payload())  # a perfectly good turn, but the call is already failing
        self.assertEqual(res.json()["decision"]["policy"], "repeated_failures_in_call")
        # a different call is unaffected
        self.assertEqual(self.post(payload(call_id="call-2")).json()["decision"]["action"], "accept")

    def test_validation_errors(self):
        self.assertEqual(self.post({"call_id": "x", "bot_id": "y"}).status_code, 400)
        self.assertEqual(self.post(payload(audio_base64="%%%not-base64")).status_code, 400)
        body = payload()
        body["stt"]["confidence"] = 1.7
        self.assertEqual(self.post(body).status_code, 400)

    def test_retrieve_by_id(self):
        created = self.post(payload()).json()
        res = self.client.get(f"{URL}{created['id']}/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["id"], created["id"])
        self.assertIn("trace", res.json()["decision"])
        self.assertEqual(self.client.get(f"{URL}00000000-0000-0000-0000-000000000000/").status_code, 404)

    def test_list_filters(self):
        self.post(payload())
        self.post(payload(call_id="call-2", bot_id="sales-bot"))
        low = payload(call_id="call-3")
        low["stt"]["confidence"] = 0.2
        low["stt"]["provider"] = "other_stt"
        self.post(low)

        def ids(query):
            res = self.client.get(URL, query)
            self.assertEqual(res.status_code, 200, res.content)
            return {r["call_id"] for r in res.json()["results"]}

        self.assertEqual(ids({"bot_id": "sales-bot"}), {"call-2"})
        self.assertEqual(ids({"call_id": "call-1"}), {"call-1"})
        self.assertEqual(ids({"action": "retry_stt"}), {"call-3"})
        self.assertEqual(ids({"provider": "other_stt"}), {"call-3"})
        self.assertEqual(ids({"provider": "demo_tts"}), {"call-1", "call-2", "call-3"})

    def test_date_range_filter(self):
        old = Evaluation.objects.get(id=self.post(payload(call_id="old")).json()["id"])
        old.created_at = timezone.now() - timedelta(days=10)
        old.save(update_fields=["created_at"])
        self.post(payload(call_id="new"))
        today = timezone.now().date().isoformat()
        res = self.client.get(URL, {"date_from": today, "date_to": today}).json()
        self.assertEqual({r["call_id"] for r in res["results"]}, {"new"})
        self.assertEqual(self.client.get(URL, {"date_from": "yesterday"}).status_code, 400)
        self.assertEqual(self.client.get(URL, {"action": "explode"}).status_code, 400)

    def test_stats(self):
        self.post(payload())
        low = payload(call_id="call-2")
        low["stt"]["confidence"] = 0.2
        self.post(low)
        slow = payload(call_id="call-3")
        slow["tts"]["duration_ms"] = 0
        slow["tts"]["latency_ms"] = 1650
        self.post(slow)

        stats = self.client.get(f"{URL}stats/").json()
        self.assertEqual(stats["total_evaluations"], 3)
        self.assertAlmostEqual(stats["failure_rate"], round(2 / 3, 4))
        self.assertAlmostEqual(stats["retry_rate"], round(2 / 3, 4))
        self.assertEqual({a["action"]: a["count"] for a in stats["actions"]},
                         {"accept": 1, "retry_stt": 1, "retry_tts": 1})
        tts = stats["tts_providers"][0]
        self.assertEqual(tts["provider"], "demo_tts")
        self.assertAlmostEqual(tts["avg_latency_ms"], round((650 + 650 + 1650) / 3, 1))
        # provider failure rate counts only turns where that provider's component failed
        self.assertAlmostEqual(tts["failure_rate"], round(1 / 3, 4))
        self.assertAlmostEqual(stats["stt_providers"][0]["failure_rate"], round(1 / 3, 4))
        self.assertEqual(self.client.get(f"{URL}stats/", {"bot_id": "nobody"}).json()["total_evaluations"], 0)

    def test_health_docs_and_report(self):
        self.assertEqual(self.client.get("/api/health/").json()["status"], "ok")
        self.assertEqual(self.client.get("/api/schema/").status_code, 200)
        self.post(payload())
        res = self.client.get("/report/")
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "support-bot")
        self.assertEqual(self.client.get("/report/", {"date_from": "nope"}).status_code, 400)
