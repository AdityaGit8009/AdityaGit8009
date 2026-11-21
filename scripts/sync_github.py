#!/usr/bin/env python3
"""Refresh Aditya's profile from public GitHub projects and the contribution calendar."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from html import escape
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
USER = "AdityaGit8009"
API = "https://api.github.com"
PROFILE = f"https://github.com/{USER}"
TOKEN = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
BG = "#0c1727"
BORDER = "#293b52"
TEXT = "#f1f5f9"
MUTED = "#91a5bf"
GREEN = ["#17263b", "#164e4a", "#21786e", "#2fb7a5", "#5eead4"]


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


def graphql(query: str, **variables) -> dict:
    result = request_json(f"{API}/graphql", {"query": query, "variables": variables})
    if result.get("errors"):
        raise RuntimeError(f"GitHub GraphQL error: {result['errors'][0]['message']}")
    return result["data"]["user"]


PROJECTS_QUERY = """query ($login: String!, $cursor: String) {
  user(login: $login) {
    repositories(first: 100, after: $cursor, privacy: PUBLIC, isFork: false,
                 ownerAffiliations: [OWNER], orderBy: {field: PUSHED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        name owner { login } visibility isFork isArchived pushedAt
        primaryLanguage { name }
        languages(first: 100) { edges { size node { name } } pageInfo { hasNextPage } }
        defaultBranchRef { target { ... on Commit { oid committedDate messageHeadline } } }
        latestRelease { tagName publishedAt isDraft isPrerelease }
      }
    }
  }
}"""


def fetch_repositories() -> list[dict]:
    repos = {}
    cursors = set()
    cursor = None
    while True:
        page = graphql(PROJECTS_QUERY, login=USER, cursor=cursor)["repositories"]
        for repo in page["nodes"]:
            name = repo["name"]
            if (repo["visibility"] == "PUBLIC" and not repo["isFork"] and not repo["isArchived"]
                    and repo["owner"]["login"].casefold() == USER.casefold()
                    and name.casefold() != USER.casefold()):
                repos[name] = repo
        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]
        if not cursor or cursor in cursors:
            raise ValueError("GitHub returned an invalid repository page cursor")
        cursors.add(cursor)
    # Sorting by name first also gives ties a stable order.
    ordered = sorted(repos.values(), key=lambda repo: repo["name"].casefold())
    return sorted(ordered, key=lambda repo: repo["pushedAt"] or "", reverse=True)


class PublicCalendar(HTMLParser):
    """Read the day cells and accessible count labels in GitHub's signed-out calendar."""

    def __init__(self):
        super().__init__()
        self.cells = {}
        self.counts = {}
        self.tooltip = None
        self.total = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "td" and "data-date" in attrs:
            self.cells[attrs["id"]] = (date.fromisoformat(attrs["data-date"]), int(attrs["data-level"]))
        if tag == "tool-tip":
            self.tooltip = attrs.get("for")

    def handle_endtag(self, tag):
        if tag == "tool-tip":
            self.tooltip = None

    def handle_data(self, data):
        text = " ".join(data.split())
        count = re.fullmatch(r"(No|[\d,]+) contributions? in the last year", text, re.I)
        if count:
            self.total = self.parse_count(count[1])
        if self.tooltip:
            count = re.match(r"(No|[\d,]+) contributions? on ", text, re.I)
            if count:
                self.counts[self.tooltip] = self.parse_count(count[1])

    @staticmethod
    def parse_count(text):
        return 0 if text.lower() == "no" else int(text.replace(",", ""))

    def weeks_and_total(self):
        if not self.cells or self.cells.keys() != self.counts.keys():
            raise ValueError("GitHub's public calendar is missing daily labels")
        weeks = []
        previous = None
        for key, (day, level) in sorted(self.cells.items(), key=lambda item: item[1][0]):
            if level not in range(len(GREEN)) or (previous and day != previous + timedelta(days=1)):
                raise ValueError("Invalid or missing day in GitHub's public calendar")
            if not weeks or day.weekday() == 6:
                weeks.append([])
            weeks[-1].append({"date": day.isoformat(), "contributionCount": self.counts[key],
                              "contributionLevel": level})
            previous = day
        if sum(self.counts.values()) != self.total:
            raise ValueError("Visible day counts do not match GitHub's public calendar total")
        return weeks, self.total


def fetch_contributions() -> tuple[list[list[dict]], int]:
    request = urllib.request.Request(
        f"https://github.com/users/{USER}/contributions",
        headers={"User-Agent": "AdityaGit8009-README-updater", "Accept-Language": "en-US"},
    )
    parser = PublicCalendar()
    with urllib.request.urlopen(request, timeout=30) as response:
        parser.feed(response.read().decode("utf-8"))
    parser.close()
    return parser.weeks_and_total()


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
    days = [day for week in weeks for day in week]
    counts = [day["contributionCount"] for day in days]
    period = f"{days[0]['date']} to {days[-1]['date']}"
    shapes = [svg_rect(1, 1, w-2, h-2, BG, 11, BORDER),
              svg_text(26, 40, f"{total:,} GitHub contributions", 23, TEXT, 650),
              svg_text(26, 63, period, 13, MUTED),
              svg_text(985, 128, str(sum(count > 0 for count in counts)), 25, "#5eead4", 650),
              svg_text(985, 148, "Days with contributions", 13, MUTED),
              svg_text(985, 188, str(max(counts)), 25, TEXT, 650),
              svg_text(985, 208, "Most in one day", 13, MUTED)]
    x0, y0, step, cell = 78, 105, 16.5, 12
    month_last = None
    for col, days in enumerate(weeks):
        if days:
            first = datetime.strptime(days[0]["date"], "%Y-%m-%d").date()
            if first.month != month_last and first.day < 24:
                shapes.append(svg_text(round(x0+col*step,1), 89, first.strftime("%b"), 12, "#c9d1d9"))
                month_last = first.month
        for d in days:
            dt = datetime.strptime(d["date"], "%Y-%m-%d").date()
            row = (dt.weekday()+1) % 7  # Sunday=0
            fill = GREEN[d["contributionLevel"]]
            x, y = round(x0+col*step,1), round(y0+row*step,1)
            label = f'{dt.isoformat()} · {d["contributionCount"]} contributions'
            shapes.append(f'<g><title>{label}</title>{svg_rect(x,y,cell,cell,fill,2)}</g>')
    for r, label in [(1,"Mon"), (3,"Wed"), (5,"Fri")]:
        shapes.append(svg_text(27,round(y0+r*step+11,1),label,12,MUTED))
    shapes += [svg_text(78, 250, "Less", 12, MUTED)]
    for i, c in enumerate(GREEN):
        shapes.append(svg_rect(112+i*18, 239, 12, 12, c, 2))
    shapes += [svg_text(214, 250, "More", 12, MUTED),
               svg_text(w-28, 250, "GitHub contribution calendar", 12, MUTED, anchor="end")]
    return document("Actual GitHub contribution activity for AdityaGit8009", w, h, shapes)


def fetch_language_totals(repos: list[dict]) -> tuple[dict[str,int], int]:
    totals: dict[str,int] = defaultdict(int)
    selected = repos[:50]
    for repo in selected:
        languages = repo["languages"]
        if languages["pageInfo"]["hasNextPage"]:
            # REST returns the full map if a project contains over 100 languages.
            name = urllib.parse.quote(repo["name"], safe="")
            sizes = request_json(f"{API}/repos/{USER}/{name}/languages")
        else:
            sizes = {edge["node"]["name"]: edge["size"] for edge in languages["edges"]}
        for language, size in sizes.items():
            if type(size) is not int or size < 0:
                raise ValueError("Invalid language byte count from GitHub")
            if size:
                totals[language] += size
    return dict(totals), len(selected)


def language_svg(totals: dict[str,int], analyzed: int) -> str:
    pairs = sorted(totals.items(), key=lambda item: (-item[1], item[0]))[:8]
    w, h = 1170, 285
    colors = ["#5eead4", "#93c5fd", "#fbbf24", "#c4b5fd", "#fb7185", "#67e8f9", "#a3e635", "#fdba74"]
    other = sum(totals.values()) - sum(raw for _, raw in pairs)
    if other:
        pairs.append(("Other", other))
        colors.append("#91a5bf")
    shapes = [svg_rect(1,1,w-2,h-2,BG,11,BORDER),
              svg_text(28,42,"Languages in my public projects",23,TEXT,650),
              svg_text(28,66, f"Language mix · {analyzed} public repositories checked", 13,MUTED)]
    if not pairs:
        shapes.append(svg_text(28,135,"No detectable public code languages yet.",17,MUTED))
    else:
        denom = sum(totals.values())
        x = 28.0
        for i,(name,raw) in enumerate(pairs):
            pct = 100*raw/denom if denom else 0
            width = 1114 * pct / 100
            shapes.append(svg_rect(round(x,3),94,round(width,3),20,colors[i]))
            x += width
            col, row = i % 3, i // 3
            lx, ly = 28 + col * 373, 146 + row * 35
            shapes.append(svg_rect(lx,ly-10,10,10,colors[i],3))
            shapes.append(svg_text(lx+21,ly,name[:23],15,TEXT,600))
            shapes.append(svg_text(lx+337,ly,f"{pct:.1f}%",14,MUTED,500,"end"))
    shapes.append(svg_text(28,h-22,"Percentages show each language’s share of the code bytes GitHub detected.",12,MUTED))
    return document("Code bytes by language across public projects",w,h,shapes)


def cell(value: str, limit: int = 110) -> str:
    """Keep repository text on one Markdown table row and render punctuation literally."""
    value = " ".join(value.split())
    if len(value) > limit:
        value = value[:limit - 1].rstrip() + "…"
    escaped = escape(value, quote=False)
    return re.sub(r"[\\|\[\]*_`~@]", lambda match: f"&#{ord(match[0])};", escaped)


def repo_link(repo: dict) -> str:
    name = repo["name"]
    return f"[{cell(name)}]({PROFILE}/{urllib.parse.quote(name, safe='')})"


def repo_markup(repos: list[dict]) -> str:
    if not repos:
        return "No public projects to list yet."
    lines = ["| Repository | Primary language | Last push (UTC) |", "| --- | --- | --- |"]
    for repo in repos[:10]:
        lang = repo["primaryLanguage"]
        pushed = (repo["pushedAt"] or "")[:10] or "—"
        lines.append(f"| {repo_link(repo)} | {cell(lang['name']) if lang else '—'} | {pushed} |")
    return "\n".join(lines)


def work_markup(repos: list[dict]) -> str:
    commits = []
    releases = []
    for repo in repos:
        branch = repo["defaultBranchRef"]
        if branch and branch["target"].get("oid"):
            commits.append((repo, branch["target"]))
        release = repo["latestRelease"]
        if release and release["publishedAt"] and not release["isDraft"] and not release["isPrerelease"]:
            releases.append((repo, release))

    commits.sort(key=lambda item: (item[1]["committedDate"], item[0]["name"]), reverse=True)
    lines = []
    if commits:
        lines.extend(["| Date (UTC) | Project | Latest default-branch commit |", "| --- | --- | --- |"])
    else:
        lines.append("Public project commits will appear here when a default branch has a commit.")
    for repo, commit in commits[:5]:
        oid = commit["oid"]
        if not re.fullmatch(r"[0-9a-f]{40,64}", oid):
            raise ValueError("Invalid commit ID from GitHub")
        name = urllib.parse.quote(repo["name"], safe="")
        url = f"{PROFILE}/{name}/commit/{oid}"
        title = cell(commit["messageHeadline"] or oid[:7])
        lines.append(f"| {commit['committedDate'][:10]} | {repo_link(repo)} | [{title}]({url}) |")

    if releases:
        lines.extend(["", "**Latest stable releases**", ""])
        releases.sort(key=lambda item: (item[1]["publishedAt"], item[0]["name"]), reverse=True)
        for repo, release in releases[:3]:
            tag = release["tagName"]
            name = urllib.parse.quote(repo["name"], safe="")
            url = f"{PROFILE}/{name}/releases/tag/{urllib.parse.quote(tag, safe='')}"
            lines.append(f"- {repo_link(repo)} · [{cell(tag)}]({url}) · {release['publishedAt'][:10]}")
    return "\n".join(lines)


def replace_region(contents: str, start: str, end: str, replacement: str) -> str:
    if contents.count(start)!=1 or contents.count(end)!=1:
        raise ValueError("README marker missing or duplicated; refusing to overwrite manual content")
    a=contents.index(start)+len(start)
    b=contents.index(end)
    if b<a:
        raise ValueError("README markers out of order")
    return contents[:a]+"\n"+replacement.strip()+"\n"+contents[b:]


def render_outputs(readme, repos, weeks, total, totals, analyzed) -> dict[str, str]:
    for key, markup in (("PUBLIC_REPOS", repo_markup(repos)), ("RECENT_WORK", work_markup(repos))):
        readme = replace_region(readme, f"<!-- {key}_START -->", f"<!-- {key}_END -->", markup)
    return {
        "README.md": readme,
        "assets/contributions.svg": contribution_svg(weeks, total),
        "assets/languages.svg": language_svg(totals, analyzed),
    }


def main():
    if not TOKEN:
        raise RuntimeError("GitHub GraphQL requires a token. The refresh workflow supplies its repository token.")
    repos = fetch_repositories()
    weeks, total = fetch_contributions()
    totals, analyzed = fetch_language_totals(repos)
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    outputs = render_outputs(readme, repos, weeks, total, totals, analyzed)
    changed = []
    for name, content in outputs.items():
        path = ROOT / name
        if not path.exists() or path.read_text(encoding="utf-8") != content:
            path.write_text(content, encoding="utf-8", newline="\n")
            changed.append(name)
    print(f"Checked {len(repos)} public projects and {len(weeks)} contribution weeks.")
    print("Updated " + ", ".join(changed) if changed else "Profile is up to date.")


if __name__=="__main__":
    try:
        main()
    except Exception as exc:
        print(f"Update failed without fabricating values: {exc}", file=sys.stderr)
        sys.exit(1)
