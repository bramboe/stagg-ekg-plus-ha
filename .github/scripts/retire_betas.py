"""Turn GitHub pre-releases into drafts once main has the same or a newer version.

Stable versions ship from main by commit. HACS offers published pre-releases to installs
with "Show beta versions" on, and skips drafts. So once main reaches a beta's version (or
passes it), drafting the beta makes those installs fall back to main and see it as an update.
Drafting rather than deleting keeps the release notes and can be undone.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys

from packaging.version import InvalidVersion, Version

MANIFEST = "custom_components/fellow_stagg/manifest.json"


def superseded(prerelease_tags: list[str], main_version: str) -> list[str]:
    """Return the pre-release tags whose version is not newer than main's."""
    main = Version(main_version)
    stale = []
    for tag in prerelease_tags:
        try:
            if Version(tag.removeprefix("v")) <= main:
                stale.append(tag)
        except InvalidVersion:
            print(f"skipping {tag}: not a version")
    return stale


def gh(*args: str) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def main() -> int:
    repo = os.environ["GITHUB_REPOSITORY"]
    content = json.loads(gh("api", f"repos/{repo}/contents/{MANIFEST}?ref=main"))["content"]
    main_version = json.loads(base64.b64decode(content))["version"]
    releases = json.loads(
        gh("release", "list", "--repo", repo, "--limit", "100", "--json", "tagName,isPrerelease,isDraft")
    )
    published = [r["tagName"] for r in releases if r["isPrerelease"] and not r["isDraft"]]
    print(f"main is {main_version}; published pre-releases: {', '.join(published) or 'none'}")
    for tag in superseded(published, main_version):
        print(f"{tag} is not newer than main {main_version}: making it a draft so HACS stops offering it")
        gh("release", "edit", tag, "--repo", repo, "--draft=true")
    return 0


if __name__ == "__main__":
    sys.exit(main())
