"""Tests for persistent Jev daily call counter."""
from jev_indstocks_trader.jev_counter import JevDailyCounter


class TestJevDailyCounter:
    def test_increment_and_get(self, tmp_path):
        path = tmp_path / "counter.json"
        counter = JevDailyCounter(path)
        assert counter.get("2026-09-28") == 0
        counter.increment("2026-09-28")
        assert counter.get("2026-09-28") == 1
        counter.increment("2026-09-28")
        assert counter.get("2026-09-28") == 2

    def test_persists_across_instances(self, tmp_path):
        path = tmp_path / "counter.json"
        c1 = JevDailyCounter(path)
        c1.increment("2026-09-28")
        c1.increment("2026-09-28")

        c2 = JevDailyCounter(path)
        assert c2.get("2026-09-28") == 2

    def test_reset_on_new_day(self, tmp_path):
        path = tmp_path / "counter.json"
        counter = JevDailyCounter(path)
        counter.increment("2026-09-28")
        counter.increment("2026-09-28")
        assert counter.get("2026-09-28") == 2

        counter.reset_if_new_day("2026-09-29")
        assert counter.get("2026-09-29") == 0

    def test_get_wrong_day_returns_zero(self, tmp_path):
        path = tmp_path / "counter.json"
        counter = JevDailyCounter(path)
        counter.increment("2026-09-28")
        assert counter.get("2026-09-29") == 0

    def test_missing_file_starts_at_zero(self, tmp_path):
        counter = JevDailyCounter(tmp_path / "nonexistent.json")
        assert counter.get("2026-09-28") == 0

    def test_corrupt_file_starts_at_zero(self, tmp_path):
        path = tmp_path / "counter.json"
        path.write_text("not json")
        counter = JevDailyCounter(path)
        assert counter.get("2026-09-28") == 0
