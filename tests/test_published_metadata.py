import re
import tomllib
from pathlib import Path


PROJECT_ROOT = Path(__file__).parents[1]
REPOSITORY_URL = "https://github.com/Dumdart/TopicGate"
RAW_REPOSITORY_URL = "https://raw.githubusercontent.com/Dumdart/TopicGate/master"


def test_published_urls_use_the_repository_default_branch() -> None:
    project = tomllib.loads(
        (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]

    assert project["urls"]["Documentation"] == (
        f"{REPOSITORY_URL}/tree/master/docs"
    )


def test_readme_resources_are_absolute_for_pypi() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    markdown_targets = re.findall(r"!?\[[^]]*\]\(([^)]+)\)", readme)
    html_image_targets = re.findall(r'<img\s+[^>]*src="([^"]+)"', readme)

    relative_targets = [
        target
        for target in markdown_targets + html_image_targets
        if not target.startswith(("https://", "#", "mailto:"))
    ]

    assert relative_targets == []
    assert f"{RAW_REPOSITORY_URL}/docs/images/desktop-observer.png" in readme
    assert f"{REPOSITORY_URL}/blob/master/docs/install/OS_INSTALL.md" in readme
