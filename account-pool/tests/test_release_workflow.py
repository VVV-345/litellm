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
    manifest_commands: Final = tuple(
        TypeAdapter(str).validate_python(step["run"]) for step in manifest_steps if "run" in step
    )
    assert any("docker buildx imagetools create" in command and "sha256:" in command for command in manifest_commands)

    tag_job: Final = JSON_MAP.validate_python(jobs["create-git-tag"])
    assert {"version", "publish-manifest", "publish-version-alias", "summary"}.issubset(
        STRING_LIST.validate_python(tag_job["needs"])
    )
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
    assert any("test_proxy_catalog_schema.py" in command for command in pytest_commands)
    assert any("test_isp_profiles.py" in command for command in pytest_commands)
    assert any("test_proxy_gateways.py" in command for command in pytest_commands)
    assert any("test_proxy_configuration.py" in command for command in pytest_commands)
    assert any("account_pool/test_upstream_sync.py" in command for command in pytest_commands)
    assert any("test_account_pool_endpoints.py" in command for command in pytest_commands)
    assert any(
        "AccountPoolUpstreamSyncPanel.test.tsx" in command and "AccountPoolSettingsOverview.test.tsx" in command
        for command in pytest_commands
    )
    assert any("AccountPoolOnboardingPanel.integration.test.tsx" in command for command in pytest_commands)
    assert any("ProxyManagerPanel.integration.test.tsx" in command for command in pytest_commands)
    assert any("AccountPoolManagementApi.test.ts" in command for command in pytest_commands)

    summary_job: Final = JSON_MAP.validate_python(jobs["summary"])
    assert "publish-version-alias" in STRING_LIST.validate_python(summary_job["needs"])


def test_success_marker_waits_for_all_version_aliases() -> None:
    path: Final = Path(__file__).resolve().parents[2] / ".github/workflows/publish-deployment-images.yml"
    workflow: Final = JSON_MAP.validate_python(yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader))
    jobs: Final = JSON_MAP.validate_python(workflow["jobs"])
    alias: Final = JSON_MAP.validate_python(jobs["publish-version-alias"])
    assert {"version", "release-manifest"}.issubset(STRING_LIST.validate_python(alias["needs"]))
    concurrency: Final = JSON_MAP.validate_python(workflow["concurrency"])
    assert concurrency["group"] == "publish-deployment-images"
    assert concurrency["cancel-in-progress"] == "false"


def test_formal_release_keeps_a_durable_non_overwritten_manifest_asset() -> None:
    path: Final = Path(__file__).resolve().parents[2] / ".github/workflows/publish-deployment-images.yml"
    workflow: Final = JSON_MAP.validate_python(yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader))
    jobs: Final = JSON_MAP.validate_python(workflow["jobs"])
    final: Final = JSON_MAP.validate_python(jobs["create-git-tag"])
    steps: Final = STEP_LIST.validate_python(final["steps"])
    script: Final = next(STRING_MAP.validate_python(step["with"])["script"] for step in steps if "github-script@" in str(step.get("uses")))
    assert "github.rest.repos.uploadReleaseAsset" in script
    assert "Buffer.from(existing.data).equals(manifest)" in script
    assert "release-manifest.json" in script
    assert "deleteReleaseAsset" not in script
    assert "updateRef" not in script


def test_unified_package_builds_and_probes_all_four_components_on_selected_platforms() -> None:
    path: Final = Path(__file__).resolve().parents[2] / ".github/workflows/publish-deployment-images.yml"
    workflow: Final = JSON_MAP.validate_python(yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader))
    jobs: Final = JSON_MAP.validate_python(workflow["jobs"])
    build: Final = JSON_MAP.validate_python(jobs["build-and-publish"])
    strategy: Final = JSON_MAP.validate_python(build["strategy"])
    matrix: Final = JSON_MAP.validate_python(strategy["matrix"])
    entries: Final = STEP_LIST.validate_python(matrix["include"])
    assert matrix["arch"] == "${{ fromJSON(needs.version.outputs.architectures) }}"
    assert set(STRING_LIST.validate_python(matrix["image"])) == {
        "litellm", "manager", "cliproxyapi"
    }
    assert all("arch" not in entry for entry in entries)
    assert build["runs-on"] == "${{ matrix.arch == 'arm64' && 'ubuntu-24.04-arm' || 'ubuntu-latest' }}"
    assert {entry["image"] for entry in entries if "image" in entry} == set(matrix["image"])
    steps: Final = STEP_LIST.validate_python(build["steps"])
    image_build: Final = next(step for step in steps if step.get("id") == "build")
    options: Final = STRING_MAP.validate_python(image_build["with"])
    assert options["platforms"] == "linux/${{ matrix.arch }}"
    assert "${{ env.IMAGE_REPOSITORY }}" in options["outputs"]
    assert STRING_MAP.validate_python(workflow["env"])["IMAGE_REPOSITORY"] == "ghcr.io/vvv-345/litellm-cliproxyapi"
    assert "ai.zlia.component=${{ matrix.image }}" in options["labels"]
    assert "org.opencontainers.image.revision=${{ needs.version.outputs.revision }}" in options["labels"]
    probe: Final = next(step for step in steps if step.get("id") == "probe")
    commands: Final = TypeAdapter(str).validate_python(probe["run"])
    assert "probe_managed_identity.py --image" in commands
    assert "${DIGEST}" in commands
    assert steps.index(probe) < next(index for index, step in enumerate(steps) if step.get("name") == "Record the built image digest")
    assert "clean-package-versions" not in jobs
    release: Final = JSON_MAP.validate_python(jobs["release-manifest"])
    assert "publish-manifest" in STRING_LIST.validate_python(release["needs"])


def test_manager_expected_companion_pins_come_from_selected_product_source_metadata():
    path = Path(__file__).resolve().parents[2] / ".github/workflows/publish-deployment-images.yml"
    workflow = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    step = next(item for item in workflow["jobs"]["build-and-publish"]["steps"] if item.get("id") == "build")
    labels = step["with"]["labels"]
    assert "ai.zlia.cliproxy-source-revision=${{ needs.version.outputs.cliproxy_commit }}" in labels
    assert "ai.zlia.cliproxy-patch-sha256=${{ needs.version.outputs.patch_sha256 }}" in labels


def test_sha_manifest_uses_selected_architectures_and_preserves_immutable_tags() -> None:
    path: Final = Path(__file__).resolve().parents[2] / ".github/workflows/publish-deployment-images.yml"
    workflow: Final = JSON_MAP.validate_python(yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader))
    jobs: Final = JSON_MAP.validate_python(workflow["jobs"])
    manifest: Final = JSON_MAP.validate_python(jobs["publish-manifest"])
    steps: Final = STEP_LIST.validate_python(manifest["steps"])
    commands: Final = "\n".join(TypeAdapter(str).validate_python(step["run"]) for step in steps if "run" in step)
    assert '${IMAGE}-${arch}.json' in commands
    assert 'jq -r ".[]" <<< "$ARCHITECTURES"' in commands
    assert '.platforms == $platforms' in commands
    assert 'create --dry-run "${sources[@]}"' in commands
    assert 'create --tag "$ref" "${sources[@]}"' in commands
    assert "diff <(jq -S '.manifests | sort_by(.digest)' /tmp/proposed.json)" in commands
    assert '/tmp/existing.json)' in commands
    assert '/tmp/published.json)' in commands


def test_workflow_exposes_architecture_selection_and_passes_it_to_all_gates() -> None:
    path = Path(__file__).resolve().parents[2] / ".github/workflows/publish-deployment-images.yml"
    workflow = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert workflow["on"]["workflow_dispatch"]["inputs"]["architectures"]["options"] == [
        "arm64", "amd64", "amd64,arm64"
    ]
    assert workflow["on"]["workflow_call"]["inputs"]["architectures"]["type"] == "string"
    jobs = workflow["jobs"]
    step = next(step for step in jobs["version"]["steps"] if step.get("id") == "platforms")
    assert "inputs.architectures" in step["env"]["ARCHITECTURES"]
    assert "vars.DEPLOY_ARCHITECTURES" in step["env"]["ARCHITECTURES"]
    assert "'arm64'" in step["env"]["ARCHITECTURES"]
    assert 'python deploy/release_platforms.py "$ARCHITECTURES"' in step["run"]
    for job in ("publish-manifest", "release-manifest"):
        step = next(step for step in jobs[job]["steps"] if "ARCHITECTURES" in step.get("env", {}))
        assert step["env"]["ARCHITECTURES"] == "${{ needs.version.outputs.architectures }}"
    release = next(step for step in jobs["release-manifest"]["steps"] if "run" in step)
    assert '--architectures "$architectures"' in release["run"]
    wrapper = yaml.load((path.parent / "publish-cliproxy-adapted-image.yml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert wrapper["jobs"]["publish"]["with"]["architectures"] == "${{ inputs.architectures }}"
