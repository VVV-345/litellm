"""本文件验证发布流水线将正式镜像和 Git tag 限定在构建成功之后。"""

from pathlib import Path
from typing import Final, cast

import yaml
from pydantic import JsonValue, TypeAdapter

JSON_MAP: Final = TypeAdapter(dict[str, JsonValue])
STRING_MAP: Final = TypeAdapter(dict[str, str])
STRING_LIST: Final = TypeAdapter(list[str])
STEP_LIST: Final = TypeAdapter(list[dict[str, JsonValue]])


def test_release_tags_wait_for_all_image_builds() -> None:
    workflow_path: Final = (
        Path(__file__).resolve().parents[2] / ".github" / "workflows" / "publish-deployment-images.yml"
    )
    raw_workflow: Final = cast(object, yaml.load(workflow_path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader))
    workflow: Final = JSON_MAP.validate_python(raw_workflow)
    jobs: Final = JSON_MAP.validate_python(workflow["jobs"])
    verify_job: Final = JSON_MAP.validate_python(jobs["verify-quota"])
    build_job: Final = JSON_MAP.validate_python(jobs["build-and-publish"])
    build_steps: Final = STEP_LIST.validate_python(build_job["steps"])
    build_step: Final = next(step for step in build_steps if step.get("id") == "build")
    build_with: Final = JSON_MAP.validate_python(build_step["with"])
    build_outputs: Final = TypeAdapter(str).validate_python(build_with["outputs"])
    assert "verify-quota" in STRING_LIST.validate_python(build_job["needs"])
    assert "push-by-digest=true" in build_outputs
    assert "push=true" in build_outputs
    assert "tags" not in build_with
    assert any(
        "actions/upload-artifact@" in TypeAdapter(str).validate_python(step.get("uses", "")) for step in build_steps
    )
    assert "candidate-" not in workflow_path.read_text(encoding="utf-8")

    manifest_job: Final = JSON_MAP.validate_python(jobs["publish-manifest"])
    manifest_steps: Final = STEP_LIST.validate_python(manifest_job["steps"])
    assert "build-and-publish" in STRING_LIST.validate_python(manifest_job["needs"])
    assert any(
        "actions/download-artifact@" in TypeAdapter(str).validate_python(step.get("uses", ""))
        for step in manifest_steps
    )
    manifest_env: Final = tuple(
        value for step in manifest_steps for value in STRING_MAP.validate_python(step.get("env", {})).values()
    )
    assert any("outputs.deploy_tag" in value for value in manifest_env)
    assert any("outputs.versioned_tag" in value for value in manifest_env)
    manifest_commands: Final = tuple(
        TypeAdapter(str).validate_python(step["run"]) for step in manifest_steps if "run" in step
    )
    assert any("docker buildx imagetools create" in command and "sha256:" in command for command in manifest_commands)

    tag_job: Final = JSON_MAP.validate_python(jobs["create-git-tag"])
    assert {"version", "publish-manifest"}.issubset(STRING_LIST.validate_python(tag_job["needs"]))
    permissions: Final = STRING_MAP.validate_python(tag_job["permissions"])
    assert permissions["contents"] == "write"
    tag_steps: Final = STEP_LIST.validate_python(tag_job["steps"])
    tag_env: Final = tuple(
        value for step in tag_steps for value in STRING_MAP.validate_python(step.get("env", {})).values()
    )
    assert any("outputs.versioned_tag" in value for value in tag_env)
    assert any("outputs.revision" in value for value in tag_env)

    verify_steps: Final = STEP_LIST.validate_python(verify_job["steps"])
    pytest_commands: Final = tuple(
        TypeAdapter(str).validate_python(step["run"]) for step in verify_steps if "run" in step
    )
    assert any("test_release_workflow.py" in command for command in pytest_commands)
    assert any("test_release_runtime.py" in command for command in pytest_commands)
    assert any("test_release_database.py" in command for command in pytest_commands)
    assert any("account_pool/test_upstream_sync.py" in command for command in pytest_commands)
    assert any("test_account_pool_endpoints.py" in command for command in pytest_commands)
    assert any(
        "AccountPoolUpstreamSyncPanel.test.tsx" in command and "AccountPoolSettingsOverview.test.tsx" in command
        for command in pytest_commands
    )
    assert any("AccountPoolOnboardingPanel.integration.test.tsx" in command for command in pytest_commands)
    assert any("AccountPoolManagementApi.test.ts" in command for command in pytest_commands)

    summary_job: Final = JSON_MAP.validate_python(jobs["summary"])
    assert "create-git-tag" in STRING_LIST.validate_python(summary_job["needs"])
