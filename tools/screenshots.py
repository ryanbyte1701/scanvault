#!/usr/bin/env python3
"""Regenerate the README screenshots from the self-contained demo (sample data
only, no server, no real patient data). Every shot uses the default dark theme.

    pip install playwright && playwright install chromium
    python3 tools/screenshots.py            # writes docs/screenshots/*.png

Set CHROME=/path/to/chrome to use an installed browser instead.
"""
import asyncio
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "screenshots"
DARK = {"theme": "dark", "dark_color": "#0d1416"}
DESKTOP = {"width": 1440, "height": 900}
MOBILE = {"width": 390, "height": 844}


async def open_vault(ctx, demo):
    pg = await ctx.new_page()
    await pg.goto(demo.as_uri())
    await pg.wait_for_timeout(600)
    await pg.add_style_tag(content=".demo-banner{display:none!important}")
    await pg.evaluate("t => SV_API.req('PUT', '/api/prefs', t)", DARK)
    return pg


async def later(pg):
    # the demo vault asks to save the recovery key first
    if await pg.get_by_text("Remind me later").count():
        await pg.get_by_text("Remind me later").click()
        await pg.wait_for_timeout(800)


async def nav(pg, label):
    await pg.locator("[data-go]:visible", has_text=label).first.click()
    await pg.wait_for_timeout(900)


async def click(pg, sel, text):
    await pg.locator(sel, has_text=text).first.click()
    await pg.wait_for_timeout(1000)


async def shot(pg, name):
    await pg.wait_for_timeout(500)
    path = OUT / f"{name}.png"
    await pg.screenshot(path=str(path))
    # 256-colour palette keeps the PNGs small; dither only the scan images
    im = Image.open(path).convert("RGB")
    dither = Image.Dither.FLOYDSTEINBERG if "viewer" in name or "compare" in name or name == "study" else Image.Dither.NONE
    im.quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=dither).save(path, optimize=True)
    print("wrote", path.relative_to(ROOT))


async def compare(pg, name):
    """Prior and current knee MRI side by side, linked, a few slices in."""
    await nav(pg, "Patients")
    await click(pg, "a.pcard", "Jane Doe")
    await click(pg, "a.study", "MRI left knee")
    await pg.click("#cmp")
    await pg.wait_for_timeout(600)
    await pg.locator(".cmpick").first.click()
    await pg.wait_for_timeout(1500)
    for _ in range(3):
        await pg.keyboard.press("ArrowDown")
        await pg.wait_for_timeout(300)
    await shot(pg, name)
    await pg.keyboard.press("Escape")
    await pg.wait_for_timeout(400)


async def capture(demo):
    async with async_playwright() as p:
        kw = {"executable_path": os.environ["CHROME"]} if os.environ.get("CHROME") else {}
        browser = await p.chromium.launch(args=["--no-sandbox"], **kw)

        async def context(viewport, mobile=False):
            ctx = await browser.new_context(viewport=viewport, device_scale_factor=2, color_scheme="dark",
                                            is_mobile=mobile, has_touch=mobile)
            await ctx.add_init_script(f"localStorage.setItem('sv-theme', '{__import__('json').dumps(DARK)}')")
            return ctx

        # First-run setup
        ctx = await context({"width": 1280, "height": 960})
        pg = await open_vault(ctx, demo)
        await pg.click("#new")
        await pg.fill("[name=u]", "ryan")
        await pg.fill("[name=p]", "sample-password-1")
        await pg.fill("[name=p2]", "sample-password-1")
        await pg.click("button.primary")
        await pg.wait_for_timeout(400)
        await pg.click("#gen")
        await shot(pg, "setup-encryption-key")
        await pg.click("#go")
        await pg.wait_for_timeout(1500)
        await shot(pg, "setup-recovery-key")
        await ctx.close()

        # Desktop
        ctx = await context(DESKTOP)
        pg = await open_vault(ctx, demo)
        await pg.click("#skip")
        await pg.wait_for_timeout(1200)
        await later(pg)
        await shot(pg, "home")
        await nav(pg, "Patients")
        await shot(pg, "patients")
        await click(pg, "a.pcard", "Jane Doe")
        await shot(pg, "patient-timeline")
        await click(pg, "a.study", "MRI left knee")
        await shot(pg, "study")
        await nav(pg, "Patients")
        await click(pg, "a.pcard", "Jane Doe")
        await click(pg, "a.study", "CT chest")
        await pg.locator(".tile").nth(4).click()
        await pg.wait_for_timeout(1500)
        await pg.locator("#pre").select_option(label="Lung -600/1500")
        await pg.wait_for_timeout(1500)
        await shot(pg, "viewer")
        await pg.keyboard.press("Escape")
        await pg.wait_for_timeout(400)
        await compare(pg, "compare")
        await nav(pg, "Backups")
        await shot(pg, "backups")
        await nav(pg, "Upload")
        await shot(pg, "upload")
        await nav(pg, "Settings")
        await shot(pg, "settings")
        await ctx.close()

        # Phone (PWA layout)
        ctx = await context(MOBILE, mobile=True)
        pg = await open_vault(ctx, demo)
        await pg.click("#skip")
        await pg.wait_for_timeout(1200)
        await later(pg)
        await shot(pg, "mobile-home")
        await nav(pg, "Patients")
        await click(pg, "a.pcard", "Jane Doe")
        await shot(pg, "mobile-patient")
        await click(pg, "a.study", "CT chest")
        await shot(pg, "mobile-study")
        await pg.locator(".tile").nth(2).click()
        await pg.wait_for_timeout(1500)
        await pg.locator("#pre").select_option(label="Lung -600/1500")
        await pg.wait_for_timeout(1500)
        await shot(pg, "mobile-viewer")
        await pg.keyboard.press("Escape")
        await pg.wait_for_timeout(400)
        await compare(pg, "mobile-compare")
        await nav(pg, "Backups")
        await shot(pg, "mobile-backups")
        await ctx.close()
        await browser.close()


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        demo = Path(tmp) / "demo.html"
        subprocess.run([sys.executable, str(ROOT / "tools" / "build_demo.py"), str(demo)], check=True)
        asyncio.run(capture(demo))


if __name__ == "__main__":
    main()
