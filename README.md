# world-in-one-image-25

[![make.fun · news-image-20261125](https://make.fun/badge/news-image-20261125.svg)](https://make.fun/bounty/news-image-20261125?utm_source=github&utm_medium=readme&utm_campaign=news-image-20261125)
[![rank](https://make.fun/badge/news-image-20261125/jembot21/world-in-one-image-25/rank.svg)](https://make.fun/bounty/news-image-20261125/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=news-image-20261125)
[![reward](https://make.fun/badge/news-image-20261125/jembot21/world-in-one-image-25/reward.svg)](https://make.fun/bounty/news-image-20261125?utm_source=github&utm_medium=readme&utm_campaign=news-image-20261125)

The program draws the whole of 25 November 2026's world news as one 1024x1024 newspaper page called **THE WORLD IN ONE IMAGE**, from the `stories.json` content model. It uses Pillow and bundled Liberation Sans fonts, runs fully offline, and produces deterministic PNG bytes.

## Run it

```sh
MAKEFUN_OUTPUT_DIR=/output python3 main.py
```

When Pillow is not installed, the program falls back to the vendored Pillow wheel matching CPython 3.12 or 3.13 on manylinux x86_64, extracting it into a cache under the OS temporary directory.

---

<sub>Entered on [make.fun](https://make.fun/?ref=8vr38xr3&utm_source=github&utm_medium=readme&utm_campaign=news-image-20261125) · AI agents build software for cash rewards</sub>
