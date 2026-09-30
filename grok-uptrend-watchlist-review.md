# Uptrend watchlist review

Local uncommitted changes, reviewed after the scanner edits went quiet. Stop, target, Jev cutoff, and paper mode were not part of this change.

## Summary

The uptrend rules in `scripts/scan_uptrend.py` match the requested screen (close above a rising 50/200 stack, 20-day range, liquidity versus a ₹50,000 position, 63-day strength versus the Nifty, cap 10, no padding), and the new tests cover a pass, a trend miss, a range miss, and a short list. The in-process reload is also careful: torn or unreadable files keep the last good list, and the scanner replace is atomic. The live path does not use that screen. The 08:35 job still builds a padded 200-day-cross list into `watchlist_200ma.json`, which is the file the bot now reloads, while the new scanner writes `watchlist_uptrend.json` and nothing in this tree calls it. Stop, target, conviction cutoff, and `PAPER_TRADING` are untouched.

## Issues

### Issue 1 -- Severity: bug
- File: scripts/scan_uptrend.py:272
- Description: The new screen never becomes the list the running bot trades. `main()` defaults `--output` to `~/.indstocks/watchlist_uptrend.json`. The bot reloads `cfg.watchlist_file` (`src/jev_indstocks_trader/main.py:199`), which on the VM is `~/.indstocks/watchlist_200ma.json`. That file is still produced by `scripts/scan_premarket.py`, the weekday morning job. That script still selects fresh 200-day crossovers and, at `scripts/scan_premarket.py:56`, pads to 10 from the RS ranking. The new reload makes a process that was already up before 08:35 pick up that padded crossover list during the session. A shorter uptrend result is never written to the file the loop reads, and a fresh 200-day cross is still a way onto the traded list.
- Suggestion: Have the morning job run the uptrend screen and atomically write the same path `WATCHLIST_FILE` already points at (`watchlist_200ma.json`). Keep an empty or short result short. Do not leave `scan_premarket.py` as the writer of that file.
- Status: open

### Issue 2 -- Severity: suggestion
- File: scripts/scan_uptrend.py:149
- Description: The 20-day range and traded-value checks are not the same-day averages the filter describes. High and low are `dropna()`'d separately and then subtracted as `.values` (`scripts/scan_uptrend.py:153`), so one missing high or low shifts the pairing and the range is no longer that session's high minus low. Traded value at `scripts/scan_uptrend.py:169` is the latest close times the mean of the last 20 volumes, not the mean of each session's price times volume, so a name near the cutoff can pass or fail on the last print rather than on 20-day turnover. The 63-day stock and Nifty returns (`scripts/scan_uptrend.py:114` and `:185`) have the same problem: each series uses its own `iloc[-63]` after `dropna()`, so a missing session makes the relative-strength window a different calendar span.
- Suggestion: Join high, low, close, and volume on the close index, take the last 20 rows together, average `(high-low)/close`, and average `close*volume`. Compute the 63-session return on that shared index for both the stock and `^NSEI`.
- Status: partial. Range and traded value now share the stock's sessions. The 63-day Nifty window is still its own series. See the recheck.

### Issue 3 -- Severity: suggestion
- File: src/jev_indstocks_trader/watchlist.py:70
- Description: A truncated or corrupt watchlist correctly keeps the previous symbols, but it also keeps `last_mtime`. The loop calls this every tick (`src/jev_indstocks_trader/main.py:200`, default poll 1s), so one bad file logs a warning or a full exception on every iteration until the file changes again. The atomic replace avoids this for the scanner's own writes; any other empty or broken write will spam the session log.
- Suggestion: Remember the mtime that failed to parse and stay quiet until `st_mtime` changes, while still keeping the last good symbol list.
- Status: fixed. A bad read now returns the failed mtime, and the loop stores it.

### Issue 4 -- Severity: nit
- File: src/jev_indstocks_trader/watchlist.py:17
- Description: `import os` was added with the reload helpers and is unused. `path.stat()` does not need it.
- Suggestion: Drop the import.
- Status: fixed. `watchlist.py` no longer imports `os`.

## Recheck

Claude's follow-up fixed the per-tick log spam and the unused import. The 20-day range and traded value now use one aligned frame. Stop, target, the Jev cutoff, and paper mode are still unchanged. The morning job was not switched.

### Issue 1 -- Severity: bug
- File: scripts/scan_premarket.py:56
- Description: The list the bot reloads is still the padded 200-day crossover file. `scan_premarket.py` pads to 10 and writes `~/.indstocks/watchlist_200ma.json`. `scan_uptrend.py` still defaults to `watchlist_uptrend.json`, and nothing in this tree runs it.
- Suggestion: Point the weekday morning job at the uptrend screen and write `watchlist_200ma.json`. Leave a short list short.
- Status: open

### Issue 2 -- Severity: suggestion
- File: scripts/scan_uptrend.py:116
- Description: Nifty's 63-day return is `close["^NSEI"].dropna()` then `iloc[-1]` versus `iloc[-63]`, computed once for every name. Each stock uses its own last 63 aligned rows. A missing session makes those windows different calendar spans.
- Suggestion: Join `^NSEI` onto the stock's aligned index and take both returns from that shared index.
- Status: open

### Issue 3 -- Severity: nit
- File: src/jev_indstocks_trader/watchlist.py:55
- Description: The reload docstring still says a bad file keeps `last_mtime` so the next tick retries. The code returns the failed mtime and stays quiet until the file changes.
- Suggestion: Describe the quiet behavior the code now has.
- Status: open
