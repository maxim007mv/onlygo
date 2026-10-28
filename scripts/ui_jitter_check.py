"""Optional UI check: runs the 1-click demo in headless Chromium and measures page scroll + layout shift.

Needs `pip install playwright && playwright install chromium` (not part of the runtime image).
Usage: python scripts/ui_jitter_check.py [--url http://localhost:8080] [--video DIR]
Fails if the page itself scrolls more than one screen or cumulative layout shift exceeds 0.1.
"""

import argparse
import asyncio
import sys

from playwright.async_api import async_playwright

PROBE = """() => { window.__ys = []; window.__cls = 0;
  (function f() { window.__ys.push(scrollY); requestAnimationFrame(f); })();
  new PerformanceObserver((l) => { for (const e of l.getEntries()) if (!e.hadRecentInput) window.__cls += e.value; })
    .observe({ type: 'layout-shift', buffered: true }); }"""


async def main(url: str, video: str | None) -> int:
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        opts = {"viewport": {"width": 1280, "height": 720}}
        if video:
            opts.update(record_video_dir=video, record_video_size={"width": 1280, "height": 720})
        ctx = await browser.new_context(**opts)
        page = await ctx.new_page()
        await page.goto(url)
        await page.evaluate(PROBE)
        await page.click("#demoBtn")
        await page.wait_for_function("!document.querySelector('#demoBtn').disabled", timeout=120_000)
        ys = await page.evaluate("window.__ys")
        cls = await page.evaluate("window.__cls")
        travel = sum(abs(b - a) for a, b in zip(ys, ys[1:], strict=False))
        await ctx.close()
        await browser.close()
    print(f"page scroll travel: {travel}px, CLS: {cls:.3f}")
    return 0 if travel <= 720 and cls <= 0.1 else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8080/")
    ap.add_argument("--video")
    a = ap.parse_args()
    sys.exit(asyncio.run(main(a.url, a.video)))
