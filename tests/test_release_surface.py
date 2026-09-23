"""Release-surface guardrails for the private Codex Obsidian port."""

from __future__ import annotations

import json
import importlib.util
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRIVATE_HUB_REPO = "AI-Marketing-Hub/codex-obsidian"
UPSTREAM_REPO = "AI-Marketing-Hub/claude-obsidian"
MARKETPLACE_NAME = "ai-marketing-hub-codex-obsidian"
PLUGIN_NAME = "codex-obsidian"
VERSION = "1.9.1"


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def load_json(relative: str) -> dict:
    return json.loads(read(relative))


def test_codex_marketplace_contract_matches_plugin_manifest() -> None:
    plugin = load_json(".codex-plugin/plugin.json")
    github_marketplace = load_json(".codex-plugin/marketplace.json")
    local_marketplace = load_json(".agents/plugins/marketplace.json")

    assert plugin["name"] == PLUGIN_NAME
    assert plugin["version"] == VERSION
    assert plugin["repository"] == f"https://github.com/{PRIVATE_HUB_REPO}"
    assert plugin["homepage"] == f"https://github.com/{PRIVATE_HUB_REPO}"
    assert plugin["interface"]["websiteURL"] == f"https://github.com/{PRIVATE_HUB_REPO}"

    plugin_link = ROOT / "plugins" / PLUGIN_NAME
    assert plugin_link.is_symlink(), f"plugins/{PLUGIN_NAME} must be a symlink"
    assert plugin_link.readlink() == Path("..")

    expected_policy = {
        "installation": "AVAILABLE",
        "authentication": "ON_INSTALL",
    }
    for relative, marketplace in {
        ".codex-plugin/marketplace.json": github_marketplace,
        ".agents/plugins/marketplace.json": local_marketplace,
    }.items():
        assert marketplace["name"] == MARKETPLACE_NAME, relative
        assert marketplace["interface"]["displayName"] == (
            plugin["interface"]["displayName"]
        ), relative
        assert len(marketplace["plugins"]) == 1, relative
        entry = marketplace["plugins"][0]
        assert entry["name"] == plugin["name"], relative
        assert entry["category"] == plugin["interface"]["category"], relative
        assert entry["policy"] == expected_policy, relative

    assert github_marketplace["plugins"][0]["source"] == {
        "source": "github",
        "repo": PRIVATE_HUB_REPO,
        "ref": "main",
    }
    assert local_marketplace["plugins"][0]["source"] == {
        "source": "local",
        "path": f"./plugins/{PLUGIN_NAME}",
    }


def test_marketplace_release_files_are_tracked() -> None:
    tracked_paths = [
        ".codex-plugin/plugin.json",
        ".codex-plugin/marketplace.json",
        ".agents/plugins/marketplace.json",
        f"plugins/{PLUGIN_NAME}",
        ".github/CODEOWNERS",
        "docs/PUBLISHING.md",
        "RELEASE_NOTES.md",
        "scripts/read-hot-cache.sh",
        "scripts/download-public-url.py",
        "tests/test_release_surface.py",
    ]
    subprocess.run(
        ["git", "ls-files", "--error-unmatch", *tracked_paths],
        cwd=ROOT,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def test_private_hub_docs_are_not_public_fallback_docs() -> None:
    checked_paths = [
        "README.md",
        "docs/install-guide.md",
        "docs/PUBLISHING.md",
        "AGENTS.md",
        "CODEX.md",
        "ATTRIBUTION.md",
        "CONTRIBUTING.md",
    ]
    forbidden = [
        "AgriciDaniel/codex-obsidian",
        "public stable",
        "public fallback",
        "public canonical",
        "early-access mirror",
        "codex plugin install",
        "codex plugin validate",
        "raw.githubusercontent.com/AI-Marketing-Hub/codex-obsidian",
    ]
    for relative in checked_paths:
        text = read(relative)
        assert PRIVATE_HUB_REPO in text, relative
        for needle in forbidden:
            assert needle not in text, f"{relative} contains stale {needle!r}"

    assert UPSTREAM_REPO in read("README.md")
    assert UPSTREAM_REPO in read("docs/PUBLISHING.md")


def test_readme_uses_private_safe_badges_and_current_counts() -> None:
    text = read("README.md")

    assert "img.shields.io/github/stars/" not in text
    assert "/releases/latest" not in text
    assert "img.shields.io/badge/release-v1.9.1-blue" in text
    assert "img.shields.io/badge/CI-GitHub%20Actions-blue" in text
    assert "img.shields.io/badge/Tests-9%20suites-brightgreen" in text
    assert "scripts/                      # 14 helper scripts" in text
    assert "tests/                        # 9 hermetic test suites" in text


def test_hook_hot_cache_loader_requires_marker_and_caps_output(tmp_path: Path) -> None:
    script = ROOT / "scripts" / "read-hot-cache.sh"
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "hot.md").write_text("abcdefghijklmnopqrstuvwxyz", encoding="utf-8")

    unmarked = subprocess.run(
        ["bash", str(script)],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=True,
    )
    assert unmarked.stdout == ""

    (tmp_path / "CODEX.md").write_text("codex-obsidian\n", encoding="utf-8")
    codex_only = subprocess.run(
        ["bash", str(script)],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=True,
    )
    assert codex_only.stdout == ""

    marker_dir = tmp_path / ".vault-meta"
    marker_dir.mkdir()
    (marker_dir / "codex-obsidian.vault").write_text("marked\n", encoding="utf-8")
    marked = subprocess.run(
        ["bash", str(script)],
        cwd=tmp_path,
        env={**os.environ, "CODEX_OBSIDIAN_HOT_CACHE_MAX_BYTES": "9"},
        text=True,
        capture_output=True,
        check=True,
    )
    assert marked.stdout == "abcdefghi"


def test_hooks_do_not_execute_repo_controlled_scripts_before_marker() -> None:
    text = read("hooks/hooks.json")
    hooks = load_json("hooks/hooks.json")
    serialized = json.dumps(hooks)

    assert "scripts/read-hot-cache.sh" not in serialized
    assert "scripts/wiki-lock.sh" not in serialized
    assert "CODEX.md names codex-obsidian" not in serialized
    assert ".vault-meta/codex-obsidian.vault" in serialized
    assert "head -c" in text


def test_hot_cache_docs_match_marker_only_hook_boundary() -> None:
    text = read("wiki/hot.md")
    changelog = read("CHANGELOG.md")

    assert "CODEX.md` naming alone\n  does not unlock hooks" in text
    assert "without executing repository-controlled helper scripts" in text
    assert "Hook loading moved to `scripts/read-hot-cache.sh`" not in text
    assert "CODEX.md` naming\n  codex-obsidian is also accepted" not in text
    assert "no\n  longer execute repository-controlled helper scripts" in changelog


def test_setup_vault_pins_excalidraw_download() -> None:
    text = read("bin/setup-vault.sh")
    assert "/releases/latest/" not in text
    assert "EXCALIDRAW_VERSION=\"2.23.6\"" in text
    assert "EXCALIDRAW_MAIN_JS_SHA256=" in text
    assert "sha256sum" in text


def test_canvas_url_download_uses_safe_helper() -> None:
    text = read("skills/canvas/SKILL.md")
    assert "curl -sL" not in text
    assert "scripts/download-public-url.py" in text


def test_download_public_url_rejects_shared_address_space() -> None:
    module_path = ROOT / "scripts" / "download-public-url.py"
    spec = importlib.util.spec_from_file_location("download_public_url", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.safe_ip("8.8.8.8") is True
    assert module.safe_ip("100.64.0.1") is False
    assert module.safe_ip("169.254.169.254") is False


def test_defuddle_uses_safe_download_helper() -> None:
    text = read("skills/defuddle/SKILL.md")
    assert "scripts/download-public-url.py" in text
    assert "defuddle https://" not in text


def test_lock_examples_release_with_trap() -> None:
    checked = [
        "skills/autoresearch/SKILL.md",
        "skills/wiki-ingest/SKILL.md",
        "agents/wiki-ingest.md",
    ]
    for relative in checked:
        text = read(relative)
        assert "trap 'bash scripts/wiki-lock.sh release" in text, relative


def test_active_seed_pages_are_codex_branded() -> None:
    checked = [
        "wiki/getting-started.md",
        "wiki/hot.md",
        "wiki/overview.md",
        "wiki/concepts/Hot Cache.md",
        "wiki/entities/Andrej Karpathy.md",
    ]
    forbidden = [
        "Claude Code",
        "Claude reads",
        "Obsidian is the IDE, Claude",
        "claude-obsidian plugin",
    ]
    for relative in checked:
        text = read(relative)
        for needle in forbidden:
            assert needle not in text, f"{relative} contains stale {needle!r}"
        assert "claude-obsidian@" not in text


def test_skill_frontmatter_matches_codex_validator_contract() -> None:
    ag = read("AGENTS.md")
    copilot = read(".github/copilot-instructions.md")
    assert "allowed-tools field for Codex compatibility" not in ag
    assert "Do not add\n`allowed-tools`" in ag
    assert "allowed-tools` is not" in copilot

    for skill_file in sorted((ROOT / "skills").glob("*/SKILL.md")):
        text = skill_file.read_text(encoding="utf-8")
        frontmatter = text.split("---", 2)[1]
        assert "allowed-tools:" not in frontmatter, skill_file


def test_public_visual_surfaces_are_codex_branded() -> None:
    checked_paths = [
        "wiki/canvases/codex-obsidian-presentation.canvas",
        "wiki/canvases/youtube-explainer.canvas",
        "wiki/canvases/welcome.canvas",
    ]
    forbidden = [
        "claude-obsidian",
        "Claude Obsidian",
        "Claude Code",
        "claude plugin",
        "github.com/AgriciDaniel/claude-obsidian",
    ]
    for relative in checked_paths:
        text = read(relative)
        assert "codex-obsidian" in text or "Codex Obsidian" in text, relative
        for needle in forbidden:
            assert needle not in text, f"{relative} contains stale {needle!r}"

    stale_media = [
        ROOT / "wiki/canvases/claude-obsidian-presentation.canvas",
        *sorted((ROOT / "wiki/meta").glob("claude-obsidian-*.png")),
        *sorted((ROOT / "wiki/meta").glob("claude-obsidian-*.gif")),
    ]
    assert not any(path.exists() for path in stale_media), stale_media
