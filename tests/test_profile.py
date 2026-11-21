import re
import unittest
from unittest.mock import patch
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree

from scripts import sync_github as profile


def project(name, **fields):
    repo = {
        "name": name,
        "owner": {"login": profile.USER},
        "visibility": "PUBLIC",
        "isFork": False,
        "isArchived": False,
        "pushedAt": "2026-01-10T10:00:00Z",
        "primaryLanguage": None,
        "defaultBranchRef": None,
        "latestRelease": None,
        "languages": {"edges": [], "pageInfo": {"hasNextPage": False}},
    }
    return repo | fields


def project_page(repos, cursor=None):
    return {"repositories": {
        "nodes": repos,
        "pageInfo": {"hasNextPage": bool(cursor), "endCursor": cursor},
    }}


class GeneratorTests(unittest.TestCase):
    def test_queries_only_owned_public_projects_and_handles_pagination(self):
        pages = [
            project_page([
                project("zeta"), project("private", visibility="PRIVATE"),
                project("internal", visibility="INTERNAL"), project("fork", isFork=True),
                project("archived", isArchived=True), project(profile.USER),
                project("another-owner", owner={"login": "another-owner"}),
            ], "page-2"),
            project_page([project("alpha"), project("zeta")]),
        ]
        with patch.object(profile, "graphql", side_effect=pages) as query:
            repos = profile.fetch_repositories()
        self.assertEqual([repo["name"] for repo in repos], ["alpha", "zeta"])
        self.assertEqual(query.call_args_list[1].kwargs["cursor"], "page-2")
        self.assertIn("privacy: PUBLIC", query.call_args_list[0].args[0])

    def test_signed_out_calendar_checks_dates_labels_and_total(self):
        fragment = """
          <h2>1 contribution
            in the last year</h2>
          <td id="sun" data-date="2026-01-11" data-level="0"></td>
          <tool-tip for="sun">No contributions on January 11th.</tool-tip>
          <td id="sat" data-date="2026-01-10" data-level="1"></td>
          <tool-tip for="sat">1 contribution on January 10th.</tool-tip>
        """
        parser = profile.PublicCalendar()
        parser.feed(fragment)
        weeks, total = parser.weeks_and_total()
        self.assertEqual((total, len(weeks)), (1, 2))
        self.assertEqual(weeks[1][0]["contributionCount"], 0)
        for missing in ("in the last year", "No contributions on January 11th."):
            parser = profile.PublicCalendar()
            parser.feed(fragment.replace(missing, ""))
            with self.assertRaises(ValueError):
                parser.weeks_and_total()

    def test_language_totals_use_complete_data_and_limit(self):
        langs = {"edges": [{"size": 40, "node": {"name": "Java"}}],
                 "pageInfo": {"hasNextPage": False}}
        truncated = {"edges": [], "pageInfo": {"hasNextPage": True}}
        repos = [project("small", languages=langs), project("large", languages=truncated)]
        repos.extend(project(str(n)) for n in range(48))
        repos.append(project("outside-window", languages=langs))
        with patch.object(profile, "request_json", return_value={"Java": 10, "PHP": 20}) as rest:
            totals, count = profile.fetch_language_totals(repos)
        self.assertEqual((totals, count), ({"Java": 50, "PHP": 20}, 50))
        rest.assert_called_once_with(f"{profile.API}/repos/{profile.USER}/large/languages")

    def test_commit_order_escaping_and_eligible_releases(self):
        latest = {"committedDate": "2026-01-15T10:00:00Z", "oid": "a" * 40,
                  "messageHeadline": "Update [manual](example) | form\n<script>"}
        earlier = latest | {"committedDate": "2026-01-14T10:00:00Z", "messageHeadline": "Earlier"}
        release = {"publishedAt": "2026-01-10T10:00:00Z", "tagName": "series/v2",
                   "isDraft": False, "isPrerelease": False}
        repos = [
            project("earlier", defaultBranchRef={"target": earlier}),
            project("latest", defaultBranchRef={"target": latest}, latestRelease=release),
            project("draft", latestRelease=release | {"isDraft": True}),
            project("preview", latestRelease=release | {"isPrerelease": True}),
            project("empty"),
        ]
        markup = profile.work_markup(repos)
        self.assertLess(markup.index("[latest]"), markup.index("[earlier]"))
        self.assertIn("/commit/" + "a" * 40, markup)
        self.assertIn("/releases/tag/series%2Fv2", markup)
        self.assertIn("&#124; form &lt;script&gt;", markup)
        self.assertNotIn("[manual]", markup)
        self.assertNotIn("draft", markup)
        self.assertNotIn("preview", markup)
        self.assertNotIn("stable releases", profile.work_markup([]))

    def test_render_is_idempotent_and_preserves_authored_sections(self):
        template = ("Intro\n<!-- RECENT_WORK_START --><!-- RECENT_WORK_END -->\n"
                    "My bio\n<!-- PUBLIC_REPOS_START --><!-- PUBLIC_REPOS_END -->\nFooter")
        weeks = [[{"date": "2026-01-10", "contributionCount": 2,
                   "contributionLevel": 2}]]
        args = ([project("sample")], weeks, 2, {"C++": 10}, 1)
        first = profile.render_outputs(template, *args)
        self.assertEqual(first, profile.render_outputs(first["README.md"], *args))
        self.assertIn("My bio", first["README.md"])
        self.assertTrue(first["README.md"].endswith("Footer"))
        for name in ("assets/contributions.svg", "assets/languages.svg"):
            ElementTree.fromstring(first[name])
        self.assertIn("2 GitHub contributions", first["assets/contributions.svg"])
        self.assertIn("100.0%", first["assets/languages.svg"])

    def test_invalid_region_stops_generation(self):
        for invalid in ("", "start start end", "end start"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                profile.replace_region(invalid, "start", "end", "replacement")


class PublishedProfileTests(unittest.TestCase):
    def test_readme_links_and_assets(self):
        readme = (profile.ROOT / "README.md").read_text(encoding="utf-8")
        targets = re.findall(r'\]\(([^)\s]+)\)|(?:href|src)="([^"]+)"', readme)
        headings = re.findall(r"^#{1,6} (.+)$", readme, re.MULTILINE)
        anchors = {re.sub(r"[^\w -]", "", title.lower()).replace(" ", "-") for title in headings}
        for md_url, html_url in targets:
            url = md_url or html_url
            with self.subTest(url=url):
                parts = urlsplit(url)
                if parts.scheme or parts.netloc:
                    continue
                if parts.path:
                    path = (profile.ROOT / unquote(parts.path)).resolve()
                    self.assertTrue(path.is_relative_to(profile.ROOT) and path.is_file())
                elif parts.fragment:
                    self.assertIn(unquote(parts.fragment), anchors)
        for tag in re.findall(r"<img\b[^>]+>", readme):
            self.assertRegex(tag, r'\balt="[^"]+"')
        for key in ("PUBLIC_REPOS", "RECENT_WORK"):
            profile.replace_region(readme, f"<!-- {key}_START -->", f"<!-- {key}_END -->", "check")

    def test_svg_assets_parse(self):
        for path in (profile.ROOT / "assets").glob("*.svg"):
            with self.subTest(svg=path.name):
                root = ElementTree.parse(path).getroot()
                self.assertEqual(root.tag, "{http://www.w3.org/2000/svg}svg")
                self.assertIn("viewBox", root.attrib)


if __name__ == "__main__":
    unittest.main()
