from pathlib import Path


WORKFLOWS = Path(".github/workflows")


def test_workflows_only_use_repository_owned_actions() -> None:
    external_actions: list[str] = []

    for workflow in WORKFLOWS.glob("*.y*ml"):
        for line_number, line in enumerate(workflow.read_text().splitlines(), start=1):
            value = line.strip().removeprefix("- ").strip()
            if value.startswith("uses:"):
                action = value.removeprefix("uses:").strip()
                if not action.startswith(("./", "Dumdart/")):
                    external_actions.append(f"{workflow}:{line_number}: {action}")

    assert external_actions == []


def test_distribution_smoketests_install_linux_gui_dependencies() -> None:
    workflow = (WORKFLOWS / "smoketests.yml").read_text(encoding="utf-8")

    assert "if: runner.os == 'Linux'" in workflow
    assert "sudo apt-get install --yes libegl1" in workflow
