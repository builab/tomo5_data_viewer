#!/usr/bin/env python3
"""Smoke test for the Tomo5 Data Viewer: opens a session in a headless browser and clicks through
every task, tab, button, drop-down, right-click menu item, table row and keyboard shortcut,
reporting JavaScript errors, failed requests and clicks that could not be made.

    pip install playwright && python3 -m playwright install chromium
    python3 tests/smoke_test.py --session /data/tomo5/Session_X [--tilt /data/tomo5/TiltSeries_X]

It starts its own read-only viewer (tomo5_viewer.py --read-only, on a free port) and stops it at
the end. Exit code 0 = no errors. Nothing is written into the data folders.
"""
import argparse
import asyncio
import os
import socket
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
VIEWER = os.path.join(HERE, "..", "tomo5_viewer.py")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_viewer(dirs, port):
    """Share the parent folders of the session and tilt-series folder; returns (process, api path of each dir)."""
    parents, api = [], []
    for d in dirs:
        parent = os.path.dirname(os.path.abspath(d))
        if parent not in parents:
            parents.append(parent)
    names = {}
    for p in parents:                     # the viewer names roots by their folder name (_2, _3 for repeats)
        base, n, i = os.path.basename(p) or p, os.path.basename(p) or p, 2
        while n in names.values():
            n, i = f"{base}_{i}", i + 1
        names[p] = n
    for d in dirs:
        full = os.path.abspath(d)
        api.append(f"{names[os.path.dirname(full)]}/{os.path.basename(full)}")
    proc = subprocess.Popen([sys.executable, VIEWER, *parents, "--port", str(port), "--port-retries", "0", "--read-only"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    for _ in range(100):
        try:
            urllib.request.urlopen(f"http://localhost:{port}/api/info", timeout=1)
            return proc, api
        except Exception:
            if proc.poll() is not None:
                sys.exit("The viewer did not start:\n" + proc.stderr.read().decode())
            time.sleep(0.1)
    proc.kill()
    sys.exit("The viewer did not answer.")


async def run(url, slow):
    from playwright.async_api import async_playwright
    errors, notes = [], []
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await (await browser.new_context(viewport={"width": 1500, "height": 950})).new_page()
        page.on("pageerror", lambda e: errors.append(f"JavaScript error: {e}"))
        page.on("console", lambda m: m.type == "error" and "Failed to load resource" not in m.text and errors.append(f"console: {m.text}"))
        page.on("response", lambda r: r.status >= 500 and errors.append(f"HTTP {r.status} {r.url}"))
        page.on("dialog", lambda d: asyncio.ensure_future(d.dismiss()))
        wait = lambda ms: page.wait_for_timeout(ms * slow)

        async def attempt(label, action):
            try:
                await action()
                await wait(700)
            except Exception as e:
                notes.append(f"could not {label}: {str(e).splitlines()[0][:100]}")

        await page.goto(url)
        await wait(6000)
        if not await page.evaluate("!!SESSION"):
            errors.append("the session did not open: " + await page.inner_text("#status"))
            await browser.close()
            return errors, notes
        tasks = await page.eval_on_selector_all(".task[data-task]", "e => e.map(x => x.dataset.task)")
        for task in tasks:
            for tab in ["atlas", "overview", "searchmap", "search", "exposure"]:
                await page.goto("about:blank")
                await page.goto(f"{url}&task={task}&tab={tab}")
                await wait(5000)
                if await page.evaluate(f"STATE.task !== '{task}' || STATE.tab !== '{tab}'"):
                    continue                          # this tab is not available for this task / data
                where = f"{task} › {tab}"
                print(f"  {where}", flush=True)
                for bid in await page.eval_on_selector_all(".center button:not([disabled]), .subbar button:not([disabled])",
                                                           "e => e.filter(x => x.offsetParent && x.id && x.id !== 'plExport').map(x => x.id)"):
                    await attempt(f"click #{bid} ({where})", lambda bid=bid: page.click(f"#{bid}", timeout=2500))
                    if bid == "plPlay":
                        await wait(1500)
                        await attempt(f"pause ({where})", lambda: page.click("#plPlay", timeout=2500))
                for sid in await page.eval_on_selector_all(".center select, .subbar select", "e => e.filter(x => x.offsetParent && !x.disabled).map(x => x.id)"):
                    opts = await page.eval_on_selector_all(f"#{sid} option:not([disabled])", "e => e.map(o => o.value)")
                    if len(opts) > 1:
                        await attempt(f"change #{sid} ({where})", lambda sid=sid, v=opts[-1]: page.select_option(f"#{sid}", v))
                box = await page.eval_on_selector("#view", "e => { const r = e.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; }")
                await page.mouse.click(*box, button="right")
                await wait(300)
                items = await page.eval_on_selector_all("#ctxMenu .it", "e => e.map((x, i) => [i, x.textContent.trim(), x.disabled])")
                await page.keyboard.press("Escape")
                for i, label, disabled in items:
                    if disabled or "movie" in label.lower():
                        continue
                    await page.goto("about:blank")
                    await page.goto(f"{url}&task={task}&tab={tab}")
                    await wait(4000)
                    await page.mouse.click(*box, button="right")
                    await wait(300)
                    # find the item again by its label (the menu can differ a little after a reload)
                    now = await page.eval_on_selector_all("#ctxMenu .it", "e => e.map(x => x.textContent.trim())")
                    j = next((n for n, t in enumerate(now) if t[:14] == label[:14]), None)
                    if j is None:
                        continue
                    await attempt(f"choose '{label}' ({where})", lambda j=j: page.click(f"#ctxMenu .it >> nth={j}", timeout=2500))
                for row in (2, 1):
                    if await page.locator(f"#mapTable tbody tr:nth-child({row})").count():
                        await attempt(f"click table row {row} ({where})", lambda row=row: page.click(f"#mapTable tbody tr:nth-child({row})", timeout=2500))
                if await page.locator("#sortRating").count():
                    await attempt(f"sort ({where})", lambda: page.click("#sortRating", timeout=2500))
                for key in ["ArrowDown", "ArrowUp", "+", "-", "f", " ", "?", "Escape"]:
                    await attempt(f"press {key!r} ({where})", lambda key=key: page.keyboard.press(key))
                await attempt(f"double-click ({where})", lambda: page.mouse.dblclick(*box))
        await browser.close()
    return errors, notes


def main():
    ap = argparse.ArgumentParser(description="Click through the Tomo5 Data Viewer on a real session and report errors.")
    ap.add_argument("--session", required=True, help="a Tomo5 session folder (with Atlas/, SearchMaps/, Batch/)")
    ap.add_argument("--tilt", help="the tilt-series folder (optional)")
    ap.add_argument("--slow", type=float, default=1.0, help="multiply all waits (e.g. 2 on a slow network share)")
    a = ap.parse_args()
    try:
        import playwright  # noqa: F401
    except ImportError:
        sys.exit("Needs Playwright:  pip install playwright && python3 -m playwright install chromium")
    dirs = [a.session] + ([a.tilt] if a.tilt else [])
    for d in dirs:
        if not os.path.isdir(d):
            sys.exit(f"Not a folder: {d}")
    port = free_port()
    proc, api = start_viewer(dirs, port)
    url = f"http://localhost:{port}/#session={api[0]}" + (f"&tilt={api[1]}" if a.tilt else "")
    print(f"Viewer on port {port}; clicking through {api[0]}…", flush=True)
    t0 = time.time()
    try:
        errors, notes = asyncio.run(run(url, a.slow))
    finally:
        proc.terminate()
    print(f"\nDone in {time.time() - t0:.0f} s.")
    for n in notes:
        print("  note:", n)
    for e in errors:
        print("  ERROR:", e)
    print("OK: no errors." if not errors else f"{len(errors)} error(s).")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
