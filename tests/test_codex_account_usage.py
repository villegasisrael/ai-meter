import json
import sys
import tempfile
import time
import unittest
from datetime import date
from pathlib import Path
from threading import Thread
from unittest.mock import patch

from rich.text import Text

from ai_meter.collectors.codex_account_usage import (
    AccountUsage, AccountUsageState, CodexAccountUsageCollector, UsageUnavailable,
)
from ai_meter.config import AppConfig, ConfigManager
from ai_meter.tui.codex import render_account_usage


def result():
    return {
        "summary": {"lifetimeTokens": 19_106_297_990},
        "dailyUsageBuckets": [
            {"startDate": "2026-10-12", "tokens": 649_312_958},
            {"startDate": "2026-10-13", "tokens": 36_392_267},
        ],
    }


class AccountUsageTests(unittest.TestCase):
    def test_missing_day_is_unknown_and_explicit_zero_is_zero(self):
        usage = AccountUsage.from_result({"summary": None, "dailyUsageBuckets": [
            {"startDate": "2026-10-12", "tokens": 0},
            {"startDate": "2026-10-13", "tokens": -1},
            {"startDate": "bad", "tokens": 123},
        ]})
        self.assertIsNone(usage.lifetime_tokens)
        self.assertEqual(usage.daily_tokens.get(date(2026, 10, 12)), 0)
        self.assertIsNone(usage.daily_tokens.get(date(2026, 10, 13)))
        self.assertEqual(len(usage.daily_tokens), 1)
        self.assertEqual(dict(AccountUsage.from_result({}).daily_tokens), {})

    def test_conflicting_duplicate_is_not_added(self):
        payload = result()
        payload["dailyUsageBuckets"].append({"startDate": "2026-10-12", "tokens": 42})
        with self.assertRaises(UsageUnavailable):
            AccountUsage.from_result(payload)

    def test_account_cache_and_failure_preserve_last_known_data_without_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            collector = CodexAccountUsageCollector(root, command=[sys.executable])
            usage = AccountUsage.from_result(result())
            with patch.object(collector, "_query_usage", return_value=usage) as query:
                first = collector.fetch_if_due()
                self.assertEqual(first.status, "connected")
                collector.fetch_if_due()
                self.assertEqual(query.call_count, 1)
            collector.request_refresh()
            with patch.object(collector, "_query_usage", side_effect=UsageUnavailable("inicia sesión en Codex")):
                failed = collector.fetch_if_due()
            self.assertEqual(failed.status, "stale")
            self.assertIs(failed.usage, usage)
            self.assertEqual(list(root.iterdir()), [])
            collector.close()
            with patch.object(collector, "_query_usage") as query:
                collector.fetch_if_due()
                query.assert_not_called()

    def test_config_migrates_existing_codex_section_and_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text('[providers.codex]\nenabled = true\n', encoding="utf-8")
            manager = ConfigManager(path)
            config = manager.load()
            self.assertTrue(config.providers.codex.usage_api_enabled)
            self.assertEqual(config.providers.codex.usage_api_interval_s, 900)
            config.providers.codex.usage_api_enabled = False
            config.providers.codex.app_server_path = "C:/tools/codex.exe"
            manager.save(config)
            loaded = manager.load()
            self.assertFalse(loaded.providers.codex.usage_api_enabled)
            self.assertEqual(loaded.providers.codex.app_server_path, "C:/tools/codex.exe")
            self.assertFalse(loaded.app.persist_history)

    def test_render_preserves_date_labels_and_unknown_at_day_rollover(self):
        state = AccountUsageState(AccountUsage.from_result(result()), "connected")
        for width in (34, 44, 86):
            lines = [Text.from_markup(line).plain for line in render_account_usage(state, width, date(2026, 10, 13))]
            plain = "\n".join(lines)
            self.assertIn("36.39 M", plain)
            self.assertIn("649.31 M", plain)
            self.assertIn("19.11 B", plain)
            self.assertTrue(all(len(line) <= width for line in lines), lines)
        plain = "\n".join(render_account_usage(state, 86, date(2026, 10, 14)))
        self.assertIn("unknown", plain)
        self.assertIn("36.39 M", plain)
        self.assertNotIn("649.31 M", plain)


class AccountProtocolTests(unittest.TestCase):
    def test_rpc_uses_only_usage_read_and_existing_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log = root / "methods.jsonl"
            script = root / "server.py"
            script.write_text(
                "import json, os, sys\n"
                f"log = open({str(log)!r}, 'w')\n"
                "for line in sys.stdin:\n"
                "    request = json.loads(line)\n"
                "    log.write(json.dumps({'method': request['method'], 'home': os.environ['CODEX_HOME']})+'\\n'); log.flush()\n"
                "    if 'id' in request:\n"
                f"        data = {result()!r} if request['method']=='account/usage/read' else {{}}\n"
                "        print(json.dumps({'id':request['id'],'result':data}), flush=True)\n",
                encoding="utf-8",
            )
            collector = CodexAccountUsageCollector(root, command=[sys.executable, "-u", str(script)])
            state = collector.fetch_if_due()
            self.assertEqual(state.status, "connected")
            self.assertEqual(state.usage.lifetime_tokens, 19_106_297_990)
            calls = [json.loads(line) for line in log.read_text().splitlines()]
            self.assertEqual([c["method"] for c in calls], ["initialize", "initialized", "account/usage/read"])
            self.assertTrue(all(c["home"] == str(root) for c in calls))
            self.assertIsNone(collector._process)

    def test_rpc_auth_errors_do_not_expose_server_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "server.py"
            script.write_text(
                "import json, sys\n"
                "for line in sys.stdin:\n"
                "    r=json.loads(line)\n"
                "    if r['method']=='initialize':\n"
                "        print(json.dumps({'id':r['id'],'result':{}}),flush=True)\n"
                "    elif r['method']=='account/usage/read':\n"
                "        print(json.dumps({'id':r['id'],'error':{'message':'authentication required secret-do-not-show'}}),flush=True)\n",
                encoding="utf-8",
            )
            state = CodexAccountUsageCollector(root, command=[sys.executable, "-u", str(script)]).fetch_if_due()
            self.assertEqual(state.status, "unavailable")
            self.assertEqual(state.error, "inicia sesión en Codex")
            self.assertIsNone(state.usage)

    def test_shutdown_cancels_blocked_server_and_cleans_process(self):
        collector = CodexAccountUsageCollector(
            Path(tempfile.gettempdir()),
            command=[sys.executable, "-c", "import time; time.sleep(30)"],
        )
        thread = Thread(target=collector.fetch_if_due)
        thread.start()
        deadline = time.monotonic() + 3
        while collector._process is None and time.monotonic() < deadline:
            time.sleep(0.01)
        process = collector._process
        collector.close()
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertIsNone(collector._process)
        self.assertIsNotNone(process)
        self.assertIsNotNone(process.poll())

    def test_timeout_is_bounded_and_does_not_leave_a_process(self):
        collector = CodexAccountUsageCollector(
            Path(tempfile.gettempdir()),
            command=[sys.executable, "-c", "import time; time.sleep(30)"],
            timeout_seconds=0.2,
        )
        started = time.monotonic()
        state = collector.fetch_if_due()
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(state.status, "unavailable")
        self.assertIn("agotada", state.error)
        self.assertIsNone(collector._process)


class AccountTuiTests(unittest.IsolatedAsyncioTestCase):
    async def test_account_panel_refresh_is_separate_from_local_samples(self):
        from ai_meter.app import MonitorEngine
        from ai_meter.paths import AppPaths
        from ai_meter.runtime_store import MemoryOffsetStore
        from ai_meter.tui.app import AiMeterTui

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = AppPaths(root, root, root / "config", root / "db", root, root, root)
            config = AppConfig()
            config.app.start_paused = True
            config.providers.claude.usage_api_enabled = False
            config.providers.hardware.enabled = False
            engine = MonitorEngine(config, paths, MemoryOffsetStore())
            app = AiMeterTui(engine)
            with patch.object(engine.codex_account, "_query_usage", return_value=AccountUsage.from_result(result())) as query:
                engine.run_codex_account_collection()
                async with app.run_test(size=(150, 46)) as pilot:
                    await pilot.pause()
                    svg = app.export_screenshot()
                    self.assertIn("TOKENS", svg)
                    self.assertIn("19.11", svg)
                    self.assertEqual(engine.snapshot().codex_usage, [])
                    await pilot.press("r")
                    await pilot.pause()
                    self.assertEqual(query.call_count, 2)
                    await pilot.resize_terminal(120, 40)
                    await pilot.press("3")
                    await pilot.pause()
                    self.assertGreaterEqual(app.query_one("#claude_panel").content_size.height, 10)
                    self.assertGreater(app.query_one("#ai_panels").scroll_y, 0)
            self.assertTrue(engine.codex_account._closed.is_set())


if __name__ == "__main__":
    unittest.main()
