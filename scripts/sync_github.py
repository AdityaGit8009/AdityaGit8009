#!/usr/bin/env python3
"""Refresh public repo links, language bars and the REAL GitHub contribution grid.
Uses only the Python standard library and the GitHub API. No fake activity data.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from html import escape
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
USER = "AdityaGit8009"
API = "https://api.github.com"
TOKEN = os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN")
BG = "#0d1117"
PANEL = "#0d1117"
SUBTLE = "#161b22"
BORDER = "#30363d"
TEXT = "#e6edf3"
MUTED = "#8b949e"
GREEN = ["#161b22", "#0e4429", "#006d32", "#26a641", "#39d353"]


def request_json(url: str, payload: dict | None = None):
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "AdityaGit8009-README-updater",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        detail = exc.read(500).decode("utf-8", "replace")
        raise RuntimeError(f"GitHub API returned HTTP {exc.code}: {detail}") from exc


def fetch_repositories() -> list[dict]:
    repos: list[dict] = []
    for page in range(1, 7):
        page_data = request_json(f"{API}/users/{USER}/repos?per_page=100&page={page}&type=owner&sort=pushed")
        if not isinstance(page_data, list):
            raise ValueError("Unexpected GitHub repos response")
        repos.extend(r for r in page_data if isinstance(r, dict) and not r.get("private") and not r.get("fork") and not r.get("archived") and r.get("name") != USER)
        if len(page_data) < 100:
            break
    repos.sort(key=lambda r: r.get("pushed_at") or "", reverse=True)
    return repos


def fetch_contributions() -> tuple[list[list[dict]], int]:
    # The same contribution calendar source GitHub exposes in its GraphQL API.
    query = """query ($login: String!) {
      user(login: $login) {
        contributionsCollection {
          contributionCalendar {
            totalContributions
            weeks { contributionDays { date contributionCount contributionLevel } }
          }
        }
      }
    }"""
    obj = request_json(f"{API}/graphql", {"query": query, "variables": {"login": USER}})
    if obj.get("errors"):
        raise RuntimeError("GraphQL errors: " + str(obj["errors"])[:450])
    calendar = obj["data"]["user"]["contributionsCollection"]["contributionCalendar"]
    weeks = [w["contributionDays"] for w in calendar["weeks"]]
    if not weeks or not any(weeks):
        raise ValueError("No contribution weeks returned; refusing to create invented graph")
    for week in weeks:
        for day in week:
            if not isinstance(day["contributionCount"], int) or day["contributionCount"] < 0:
                raise ValueError("Bad contribution day count")
    return weeks, int(calendar["totalContributions"])


def svg_text(x, y, value, size=15, color=TEXT, weight=400, anchor="start"):
    return (f'<text x="{x}" y="{y}" fill="{color}" font-family="Segoe UI, Arial, sans-serif" '
            f'font-size="{size}" font-weight="{weight}" text-anchor="{anchor}">{escape(str(value))}</text>')


def svg_rect(x, y, width, height, color, radius=0, stroke=None):
    strokestr = f' stroke="{stroke}" stroke-width="1"' if stroke else ""
    return (f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="{radius}" '
            f'fill="{color}"{strokestr}/>' )


def document(title, w, h, shapes):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" '
            f'aria-label="{escape(title, quote=True)}">'
            f'<title>{escape(title)}</title>' + ''.join(shapes) + '</svg>\n')


def contribution_svg(weeks: list[list[dict]], total: int) -> str:
    w, h = 1170, 276
    shapes = [svg_rect(1, 1, w-2, h-2, BG, 11, BORDER),
              svg_text(26, 40, f"{total:,} contributions in the last year", 23, TEXT, 650),
              svg_text(w-27, 40, "REAL GITHUB ACTIVITY", 12, "#7ee787", 650, "end"),
              svg_text(26, 63, "Public contribution history • refreshed daily", 12, MUTED)]
    x0, y0, step, cell = 78, 105, 16.5, 12
    month_last = None
    colors = {"NONE": GREEN[0], "FIRST_QUARTILE": GREEN[1], "SECOND_QUARTILE": GREEN[2], "THIRD_QUARTILE": GREEN[3], "FOURTH_QUARTILE": GREEN[4]}
    for col, days in enumerate(weeks):
        if days:
            first = datetime.strptime(days[0]["date"], "%Y-%m-%d").date()
            if first.month != month_last and first.day < 24:
                shapes.append(svg_text(round(x0+col*step,1), 89, first.strftime("%b"), 12, "#c9d1d9"))
                month_last = first.month
        for d in days:
            dt = datetime.strptime(d["date"], "%Y-%m-%d").date()
            row = (dt.weekday()+1) % 7  # Sunday=0
            fill = colors.get(d["contributionLevel"], GREEN[0])
            x, y = round(x0+col*step,1), round(y0+row*step,1)
            shapes.append(svg_rect(x,y,cell,cell,fill,2))
    for r, label in [(1,"Mon"), (3,"Wed"), (5,"Fri")]:
        shapes.append(svg_text(27,round(y0+r*step+11,1),label,12,MUTED))
    shapes += [svg_text(78, 250, "Less", 12, MUTED)]
    for i, c in enumerate(GREEN):
        shapes.append(svg_rect(112+i*18, 239, 12, 12, c, 2))
    shapes += [svg_text(214, 250, "More", 12, MUTED),
               svg_text(w-28, 250, "Source: GitHub GraphQL contribution calendar", 12, MUTED, anchor="end")]
    return document("Actual GitHub contribution activity for AdityaGit8009", w, h, shapes)


def fetch_language_totals(repos: list[dict]) -> tuple[dict[str,int], int]:
    totals: dict[str,int] = defaultdict(int)
    selected = [r for r in repos if (r.get("size") or 0)>0][:50]
    for repo in selected:
        full = repo.get("full_name", "")
        if not full.startswith(USER + "/"):
            continue
        langs = request_json(f"{API}/repos/{full}/languages")
        if isinstance(langs, dict):
            for lang, size in langs.items():
                if isinstance(size, int) and size > 0:
                    totals[lang] += size
    return dict(totals), len(selected)


def language_svg(totals: dict[str,int], analyzed: int) -> str:
    pairs = sorted(totals.items(), key=lambda x:x[1], reverse=True)[:8]
    w, h = 1170, 110 + max(len(pairs),1)*49 + 43
    shapes = [svg_rect(1,1,w-2,h-2,BG,11,BORDER),
              svg_text(28,42,"Languages in My Public Repositories",23,TEXT,650),
              svg_text(28,66, f"Actual GitHub language bytes • {analyzed} recently updated public repositories checked", 13,MUTED)]
    if not pairs:
        shapes.append(svg_text(28,135,"No detectable public code languages yet.",17,MUTED))
    else:
        denom = sum(totals.values())
        for i,(name,raw) in enumerate(pairs):
            y = 100 + i*49
            pct = 100*raw/denom if denom else 0
            shapes.append(svg_text(28,y+14,name[:23],15,TEXT,600))
            shapes.append(svg_rect(235,y,825,17,SUBTLE,5))
            shapes.append(svg_rect(235,y,max(2,round(825*pct/100)),17,["#39d353","#2ea043","#26a641","#238636","#006d32","#0e4429","#4ac26b","#7ee787"][i],5))
            shapes.append(svg_text(1136,y+14,f"{pct:.1f}%",14,"#7ee787",650,"end"))
    shapes.append(svg_text(28,h-22,"Repository language mix, not a proficiency or expertise score.",12,MUTED))
    return document("Live GitHub public repository language progress bars",w,h,shapes)


def repo_markup(repos: list[dict]) -> str:
    if not repos:
        return "No non-fork public repositories found yet. [Browse my repositories](https://github.com/AdityaGit8009?tab=repositories)."
    lines = ["| Repository | Description | Primary language |", "| :--- | :--- | :--- |"]
    for r in repos[:10]:
        name = r["name"]
        url = r.get("html_url", "")
        if not url.startswith(f"https://github.com/{USER}/"):
            continue
        description = (r.get("description") or "Open-source project repository").replace("|", "\\|").replace("\n", " ").replace("\r", " ")
        description = description[:95] + ("…" if len(description)>95 else "")
        language = (r.get("language") or "—").replace("|", "")
        # GitHub controls HTML/Markdown input. Escape description and avoid unexpected HTML in README.
        lines.append(f"| [{escape(name)}]({url}) | {escape(description)} | {escape(language)} |")
    lines.append("\n[Browse all my public repositories](https://github.com/AdityaGit8009?tab=repositories)")
    return "\n".join(lines)


def replace_region(contents: str, start: str, end: str, replacement: str) -> str:
    if contents.count(start)!=1 or contents.count(end)!=1:
        raise ValueError("README marker missing or duplicated; refusing to overwrite manual content")
    a=contents.index(start)+len(start)
    b=contents.index(end)
    if b<=a:
        raise ValueError("README markers out of order")
    return contents[:a]+"\n"+replacement.strip()+"\n"+contents[b:]


def main():
    if not TOKEN:
        raise RuntimeError("Set GITHUB_TOKEN or GH_TOKEN to access GitHub GraphQL. GitHub Actions supplies GITHUB_TOKEN automatically.")
    print("Fetching verified public repositories…")
    repos=fetch_repositories()
    print(f"Public non-fork repositories: {len(repos)}")
    print("Fetching actual contribution history…")
    weeks,total=fetch_contributions()
    print(f"Actual contributions: {total}")
    print("Fetching public repository language distribution…")
    totals,analyzed=fetch_language_totals(repos)
    README=ROOT/"README.md"
    old=README.read_text(encoding="utf-8")
    new=replace_region(old, "<!-- PUBLIC_REPOS_START -->", "<!-- PUBLIC_REPOS_END -->",repo_markup(repos))
    # Generate output only after successful API fetches.
    ASSETS.mkdir(exist_ok=True)
    (ASSETS/"contributions.svg").write_text(contribution_svg(weeks,total),encoding="utf-8")
    (ASSETS/"languages.svg").write_text(language_svg(totals,analyzed),encoding="utf-8")
    README.write_text(new,encoding="utf-8")
    print("Updated contributions.svg, languages.svg and README.md public repo links.")


if __name__=="__main__":
    try:
        main()
    except Exception as exc:
        print(f"Update failed without fabricating values: {exc}", file=sys.stderr)
        sys.exit(1)
