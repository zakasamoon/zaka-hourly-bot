ZAKA HOURLY BOT  (fast test of the strategy on a small timeframe)
==================================================================
Rule: the high/low of every hour = two lines. In the NEXT hour only two signals exist:
  S3 BUY  = a candle closes above the top line;  S4 SELL = a candle closes below the bottom line.
(rejection / reversal / range signals are removed.)  Every other rule is the same as the Night algo
(50% of the candle beyond the line, clean entry, 9 targets 1:4..1:55, 40% + 7.5% x 8, stop to entry / TP1).

ONLINE (GitHub, new repository e.g. "zaka-hourly-bot"):
 1. Create a new PUBLIC repository.  Upload ALL of these files, keeping the folder .github/workflows/ :
      zaka_hourly_bot.py, zaka_hourly_config.json, index.html, README.txt, .github/workflows/zaka-hourly-bot.yml
 2. Settings > Actions > General > Workflow permissions > "Read and write permissions" > Save.
 3. Actions tab > "Zaka hourly bot" > Run workflow.  It then runs by itself every ~5 minutes.
 4. Settings > Pages > Deploy from branch > main / root: the results page is  https://<name>.github.io/<repo>/
LOCAL: double-click start_hourly_bot.bat, open http://127.0.0.1:8767
Change pairs/timeframe in zaka_hourly_config.json (timeframe 1m, 5m, 15m, 30m; profile_hours / entry_hours).
Note: on small timeframes the stop distance is tiny, so the fee (fee_percent) eats a lot of every trade - the results show that honestly.
