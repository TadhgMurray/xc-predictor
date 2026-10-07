#!/usr/bin/env python3
"""
diag_chrome_launch.py -- why the anet scraper's Chrome will not start.
READ-ONLY: launches Chrome a few times, scrapes nothing, writes nothing.

    cd /srv/xc-predictor && xvfb-run -a /srv/venv/bin/python scripts/diag_chrome_launch.py

★ WHY (owner, 2026-10-07): every launcher session failed "BrowserType.launch:
  Target page, context or browser has been closed" -- Chrome exited as it
  started -- and the launcher's tail shows only Playwright's cleanup lines,
  not Chrome's own error. This prints the machine's state (Chrome version,
  memory, /dev/shm, /tmp, leftover Chrome processes) and then launches Chrome
  with the launcher's exact flags (launcher.CHROME_ARGS), then without
  --single-process, then with no flags at all, printing each one's FULL error.
  Whichever succeeds names the cause.

  If all three succeed, the failure needs MANY Chromes at once, as the
  launcher runs them: the last test launches NUM_SESSIONS (default 10) in one
  Playwright, at the open-file limit the shell gave, then again after raising
  it as the launcher now does. about:blank only -- no site is contacted.
"""
import asyncio
import os
import shutil
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))


def _sh(cmd):
    try:
        out = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=30)
        return (out.stdout + out.stderr).strip()
    except Exception as e:                                       # noqa: BLE001
        return f"({e})"


def machine(chrome):
    print("== machine")
    print(f"  chrome:    {chrome}")
    print(f"  version:   {_sh(f'{chrome!r} --version')}")
    print(f"  DISPLAY:   {os.environ.get('DISPLAY')!r}  (empty = not under xvfb-run)")
    n_chrome = _sh("ps -eo comm | grep -c '^chrome'")
    print(f"  chrome processes running: {n_chrome}")
    print(f"  zombie/defunct:           {_sh('ps -eo stat | grep -c ^Z')}")
    for path in ("/dev/shm", "/tmp", "/"):
        u = shutil.disk_usage(path)
        print(f"  {path:9} free {u.free / 2**30:6.1f} GB of {u.total / 2**30:6.1f} GB")
    print("  " + _sh("free -g").replace("\n", "\n  "))
    print(f"  pids: {_sh('cat /proc/sys/kernel/pid_max')} max, "
          f"{_sh('ls /proc | grep -c ^[0-9]')} in use; ulimit -u {_sh('ulimit -u')}")
    print(f"  open files limit: {_sh('ulimit -n')}")
    print(f"  /tmp playwright dirs left over: {_sh('ls -d /tmp/playwright* 2>/dev/null | wc -l')}")


async def tryLaunch(pw, chrome, name, args):
    print(f"\n== launch: {name}")
    try:
        browser = await pw.chromium.launch(headless=False, executable_path=chrome,
                                           args=args, timeout=60000)
    except Exception as e:                                       # noqa: BLE001
        print(f"  FAILED:\n  " + str(e).replace("\n", "\n  "))
        return False
    try:
        page = await (await browser.new_context()).new_page()
        await page.goto("about:blank")
        print("  OK: launched and opened a page")
        return True
    except Exception as e:                                       # noqa: BLE001
        print(f"  LAUNCHED, BUT A PAGE FAILED:\n  " + str(e).replace("\n", "\n  "))
        return False
    finally:
        await browser.close()


async def manyAtOnce(pw, chrome, args, n, tag=""):
    print(f"\n== launch: {n} at once, the launcher's flags{tag}")
    got = await asyncio.gather(*[pw.chromium.launch(headless=False, executable_path=chrome,
                                                    args=args, timeout=60000)
                                 for _ in range(n)], return_exceptions=True)
    bad = [g for g in got if isinstance(g, BaseException)]
    ok = [g for g in got if not isinstance(g, BaseException)]
    pages = 0
    for b in ok:
        try:
            await (await (await b.new_context()).new_page()).goto("about:blank")
            pages += 1
        except Exception as e:                                   # noqa: BLE001
            bad.append(e)
    print(f"  {len(ok)}/{n} launched, {pages} opened a page")
    if bad:
        import launcher
        print("  first failure:\n  " + str(bad[0]).splitlines()[0])
        for line in launcher.chromeErrorLines(bad[0]):
            print(f"    {line}")
    for b in ok:
        try:
            await b.close()
        except Exception:                                        # noqa: BLE001
            pass
    return not bad


async def main():
    from chrome_path import chromePath
    from playwright.async_api import async_playwright
    import launcher
    chrome = chromePath()
    machine(chrome)
    flags = list(launcher.CHROME_ARGS)
    no_single = [a for a in flags if a != "--single-process"]
    got = {}
    async with async_playwright() as pw:
        got["launcher flags"] = await tryLaunch(pw, chrome, "the launcher's flags", flags)
        got["without --single-process"] = await tryLaunch(
            pw, chrome, "the launcher's flags without --single-process", no_single)
        got["no flags"] = await tryLaunch(pw, chrome, "no flags but --no-sandbox", ["--no-sandbox"])
        n = int(os.environ.get("NUM_SESSIONS", 10))
        import resource
        print(f"\n  open-file limit now: {resource.getrlimit(resource.RLIMIT_NOFILE)[0]}")
        got[f"{n} at once"] = await manyAtOnce(pw, chrome, flags, n)
    launcher.raiseOpenFileLimit()
    async with async_playwright() as pw:
        got[f"{n} at once, limit raised"] = await manyAtOnce(pw, chrome, flags, n, ", open-file limit raised")
    print("\n== verdict")
    for k, v in got.items():
        print(f"  {k:30} {'OK' if v else 'FAILED'}")
    if not got["launcher flags"] and got["without --single-process"]:
        print("  -> --single-process is the cause on this Chrome.")
    elif got["no flags"] and not got[f"{n} at once"] and got[f"{n} at once, limit raised"]:
        print("  -> the open-file limit is the cause; the launcher now raises it.")
    elif not any(got.values()):
        print("  -> Chrome itself will not start: read the first FAILED block "
              "(and the machine section: memory, /dev/shm, leftover processes).")


if __name__ == "__main__":
    asyncio.run(main())
