import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
POOL = {
    "name": "kata",
    "count": 3,
    "vmSize": "Standard_D4s_v5",
    "osSKU": "AzureLinux",
    "workloadRuntime": "KataVmIsolation",
    "mode": "System",
    "provisioningState": "Succeeded",
}

FAKE_COMMAND = r"""
import json
import os
import sys
from pathlib import Path

tool = Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["CALL_LOG"], "a") as stream:
    stream.write(json.dumps([tool, *args]) + "\n")

def emit(value):
    print(json.dumps(value) if not isinstance(value, str) else value)

if tool == "az":
    if args[:2] == ["account", "set"]:
        pass
    elif args[:2] == ["account", "show"]:
        emit("Enabled")
    elif args[:2] == ["group", "show"]:
        emit(os.environ.get("GROUP_LOCATION", "swedencentral"))
    elif args[:2] == ["provider", "register"]:
        pass
    elif args[:2] == ["feature", "show"]:
        if os.environ.get("FEATURE_ERROR"):
            sys.exit("Feature query unauthorized")
        emit(os.environ.get("FEATURE_STATE", "Pending"))
    elif args[:2] == ["acr", "show"]:
        emit("example.azurecr.io")
    elif args[:3] == ["deployment", "group", "validate"]:
        filename = args[args.index("--parameters") + 1].removeprefix("@")
        params = json.loads(Path(filename).read_text())["parameters"]
        assert params["deployWorkloads"]["value"] is True
        if os.environ.get("VALIDATION_ERROR"):
            sys.exit("Azure rejected the deployment")
        emit({"properties": {"provisioningState": "Succeeded"}})
    elif args[:3] == ["deployment", "group", "what-if"]:
        emit({"status": "Succeeded", "changes": []})
    elif args[:2] == ["aks", "show"]:
        emit("/subscriptions/example/resourceGroups/example/providers/"
             "Microsoft.ContainerService/managedClusters/example")
    elif args[:2] == ["aks", "get-credentials"]:
        pass
    elif args[:1] == ["rest"]:
        assert args[args.index("--url") + 1].endswith("?api-version=2026-07-01")
        emit(json.loads(os.environ["CLUSTER"]))
    else:
        sys.exit("Unexpected Azure operation: " + " ".join(args[:3]))
elif tool == "kubectl":
    if args[:2] == ["get", "runtimeclass"]:
        if os.environ.get("MISSING_RUNTIME"):
            sys.exit("Kata runtime class missing")
    elif args[:2] == ["get", "nodes"]:
        emit("\n".join("node/test-" + str(i) for i in range(int(os.environ.get("NODE_COUNT", "3")))))
    elif args[:2] == ["get", "daemonset"]:
        emit(os.environ.get("NODE_COUNT", "3"))
    elif args[:2] == ["create", "namespace"]:
        emit("apiVersion: v1\nkind: Namespace\nmetadata:\n  name: opensandbox")
    elif args[:1] == ["apply"]:
        manifest = sys.stdin.read()
        if os.environ.get("MANIFEST_LOG"):
            Path(os.environ["MANIFEST_LOG"]).write_text(manifest)
    elif args[:2] == ["rollout", "status"]:
        if os.environ.get("WAIT_ERROR"):
            sys.exit("Image warmup timed out")
    elif args[:1] == ["wait"]:
        if os.environ.get("WAIT_ERROR"):
            sys.exit("Kata readiness timed out")
    elif args[:1] == ["run"]:
        override = next(arg.split("=", 1)[1] for arg in args if arg.startswith("--overrides="))
        spec = json.loads(override)["spec"]
        assert spec["runtimeClassName"] == "kata-vm-isolation"
        assert spec["nodeSelector"]["kubernetes.azure.com/kata-vm-isolation"] == "true"
        assert not spec.get("tolerations")
    elif args[:1] == ["logs"]:
        emit(os.environ.get("KERNEL", "6.6.0-mshv"))
    elif args[:1] == ["delete"]:
        if os.environ.get("DELETE_ERROR"):
            sys.exit("Cleanup failed")
    else:
        sys.exit("Unexpected kubectl operation: " + " ".join(args[:2]))
else:
    sys.exit("Unexpected tool")
"""


@pytest.fixture
def commands(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("az", "kubectl"):
        executable = bin_dir / tool
        executable.write_text(f"#!{sys.executable}\n{FAKE_COMMAND}")
        executable.chmod(0o700)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "CALL_LOG": str(tmp_path / "calls.jsonl"),
        "AZURE_SUBSCRIPTION": "example",
        "AZURE_RESOURCE_GROUP": "example",
        "AZURE_LOCATION": "swedencentral",
        "RESOURCE_PREFIX": "example",
        "AKS_NAME": "example",
        "ACR_NAME": "example",
        "LOG_ANALYTICS_WORKSPACE_NAME": "example",
        "BACKEND_APP_NAME": "example-api",
        "FRONTEND_APP_NAME": "example-web",
        "IMAGE_TAG": "test",
        "GITHUB_TOKEN": "test-only",
        "MCD_MCP_TOKEN": "test-only",
        "OPENSANDBOX_API_KEY": "test-only",
        "OPENSANDBOX_SECURE_ACCESS_KEY": "test-only",
        "AKS_AUTHORIZED_IP_RANGES": "192.0.2.1/32",
        "CLUSTER": json.dumps(
            {"provisioningState": "Succeeded", "agentPoolProfiles": [POOL]}
        ),
    }
    return env


def run_script(script, env, *args):
    return subprocess.run(
        ["bash", str(ROOT / "scripts" / script), *args],
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )


def calls(env):
    return [json.loads(line) for line in Path(env["CALL_LOG"]).read_text().splitlines()]


@pytest.mark.parametrize(
    "state", ["Pending", "Registering", "Registered", "NotRegistered", "Unregistered"]
)
def test_feature_state_does_not_block_arm_validation(commands, state):
    commands["FEATURE_STATE"] = state
    result = run_script("prepare-azure.sh", commands)
    assert result.returncode == 0, result.stderr
    assert not any(c[1:3] == ["feature", "register"] for c in calls(commands))
    if state != "Registered":
        assert f"is {state}" in result.stderr
        assert "ARM validation" in result.stderr


@pytest.mark.parametrize(
    "changes",
    [{"FEATURE_STATE": "Failed"}, {"FEATURE_STATE": ""}, {"FEATURE_ERROR": "1"}],
)
def test_feature_query_errors_are_not_ignored(commands, changes):
    commands.update(changes)
    result = run_script("prepare-azure.sh", commands)
    assert result.returncode != 0
    assert result.stderr.strip()


def test_region_mismatch_stops_before_provider_registration(commands):
    commands["GROUP_LOCATION"] = "eastus"
    result = run_script("prepare-azure.sh", commands)
    assert result.returncode != 0
    assert not any(c[1:3] == ["provider", "register"] for c in calls(commands))


def test_validate_only_checks_workloads_and_never_creates_resources(commands):
    result = run_script("deploy-infrastructure.sh", commands, "--validate-only")
    assert result.returncode == 0, result.stderr
    operations = [c[1:4] for c in calls(commands) if c[0] == "az"]
    assert ["deployment", "group", "validate"] in operations
    assert ["deployment", "group", "what-if"] in operations
    assert ["deployment", "group", "create"] not in operations
    for call in calls(commands):
        if "--parameters" in call:
            filename = call[call.index("--parameters") + 1].removeprefix("@")
            assert not Path(filename).exists()


@pytest.mark.parametrize("args", [(), ("--validate-only",)])
def test_azure_validation_failure_prevents_creation(commands, args):
    commands["VALIDATION_ERROR"] = "1"
    result = run_script("deploy-infrastructure.sh", commands, *args)
    assert result.returncode != 0
    assert "Azure rejected" in result.stderr
    assert not any(
        c[1:4]
        in (["deployment", "group", "create"], ["deployment", "group", "what-if"])
        for c in calls(commands)
    )


def test_verified_kata_configuration_and_kernel(commands):
    result = run_script("verify-kata.sh", commands)
    assert result.returncode == 0, result.stderr
    assert "6.6.0-mshv" in result.stdout
    assert any(c[:2] == ["kubectl", "run"] for c in calls(commands))
    assert any(c[:2] == ["kubectl", "delete"] for c in calls(commands))


def test_image_warmup_uses_kata_and_cleans_up(commands, tmp_path):
    manifest = tmp_path / "warmup.yaml"
    commands["MANIFEST_LOG"] = str(manifest)
    result = run_script(
        "warm-sandbox-image.sh", commands, "example.azurecr.io/sandbox:test"
    )
    assert result.returncode == 0, result.stderr
    content = manifest.read_text()
    assert "runtimeClassName: kata-vm-isolation" in content
    assert 'kubernetes.azure.com/kata-vm-isolation: "true"' in content
    assert "image: example.azurecr.io/sandbox:test" in content
    operations = calls(commands)
    assert operations[-1][:3] == ["kubectl", "delete", "daemonset"]
    assert "--timeout=600s" in next(
        c for c in operations if c[1:3] == ["rollout", "status"]
    )


@pytest.mark.parametrize(
    "failure", [{"NODE_COUNT": "2"}, {"WAIT_ERROR": "1"}, {"DELETE_ERROR": "1"}]
)
def test_image_warmup_failure_is_reported_and_cleaned_up(commands, failure):
    commands.update(failure)
    result = run_script(
        "warm-sandbox-image.sh", commands, "example.azurecr.io/sandbox:test"
    )
    assert result.returncode != 0
    assert result.stderr.strip()
    assert calls(commands)[-1][:3] == ["kubectl", "delete", "daemonset"]


@pytest.mark.parametrize(
    "image", ["", "image:test\ninjected: value", "image with spaces"]
)
def test_image_warmup_rejects_invalid_references(commands, image):
    result = run_script("warm-sandbox-image.sh", commands, image)
    assert result.returncode != 0
    assert not Path(commands["CALL_LOG"]).exists()


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("count", 1),
        ("vmSize", "Standard_D2s_v5"),
        ("osSKU", "Ubuntu"),
        ("workloadRuntime", "OCIContainer"),
        ("mode", "User"),
        ("provisioningState", "Updating"),
        ("nodeTaints", ["kata=true:NoSchedule"]),
    ],
)
def test_pool_drift_blocks_kubernetes_operations(commands, key, value):
    cluster = json.loads(commands["CLUSTER"])
    cluster["agentPoolProfiles"][0][key] = value
    commands["CLUSTER"] = json.dumps(cluster)
    result = run_script("verify-kata.sh", commands)
    assert result.returncode != 0
    assert not any(c[0] == "kubectl" for c in calls(commands))


@pytest.mark.parametrize("pool_count", [0, 2])
def test_unexpected_pool_count_fails(commands, pool_count):
    commands["CLUSTER"] = json.dumps(
        {"provisioningState": "Succeeded", "agentPoolProfiles": [POOL] * pool_count}
    )
    result = run_script("verify-kata.sh", commands)
    assert result.returncode != 0
    assert "exactly one node pool" in result.stderr


@pytest.mark.parametrize(
    "changes",
    [
        {"MISSING_RUNTIME": "1"},
        {"NODE_COUNT": "2"},
        {"WAIT_ERROR": "1"},
        {"KERNEL": "6.6.0-azure"},
    ],
)
def test_missing_runtime_nodes_or_isolation_fails(commands, changes):
    commands.update(changes)
    result = run_script("verify-kata.sh", commands)
    assert result.returncode != 0
    if "KERNEL" in changes:
        assert "does not prove Kata MSHV isolation" in result.stderr
        assert any(c[:2] == ["kubectl", "delete"] for c in calls(commands))


def test_compiled_template_has_exact_approved_pool():
    result = subprocess.run(
        ["az", "bicep", "build", "--file", str(ROOT / "infra/main.bicep"), "--stdout"],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    resources = json.loads(result.stdout)["resources"]
    aks = next(
        resource
        for resource in resources
        if resource["type"] == "Microsoft.ContainerService/managedClusters"
    )
    assert aks["apiVersion"] == "2026-07-01"
    pools = aks["properties"]["agentPoolProfiles"]
    assert len(pools) == 1
    for key, value in POOL.items():
        if key != "provisioningState":
            assert pools[0][key] == value
    assert not pools[0].get("nodeTaints")
    assert pools[0]["upgradeSettings"]["maxSurge"] == "1"


@pytest.mark.parametrize("validation_fails", [False, True])
def test_deploy_all_validates_before_building(tmp_path, commands, validation_fails):
    scripts = tmp_path / "project" / "scripts"
    scripts.mkdir(parents=True)
    for name in ("deploy-all.sh", "common.sh"):
        (scripts / name).write_text((ROOT / "scripts" / name).read_text())
    for name in (
        "prepare-azure.sh",
        "deploy-infrastructure.sh",
        "build-images.sh",
        "install-opensandbox.sh",
        "verify-deployment.sh",
    ):
        script = scripts / name
        script.write_text(
            f"#!{sys.executable}\n"
            "import json, os, sys\n"
            "from pathlib import Path\n"
            "name = Path(sys.argv[0]).name\n"
            "assert os.environ['OPENSANDBOX_SECURE_ACCESS_KEY'] == 'test-only'\n"
            "with open(os.environ['CALL_LOG'], 'a') as stream:\n"
            "    stream.write(json.dumps([name, *sys.argv[1:]]) + '\\n')\n"
            "if '--validate-only' in sys.argv and os.environ.get('VALIDATION_ERROR'):\n"
            "    sys.exit('Azure validation rejected')\n"
        )
        script.chmod(0o700)
    if validation_fails:
        commands["VALIDATION_ERROR"] = "1"
    result = subprocess.run(
        ["bash", str(scripts / "deploy-all.sh")],
        env=commands,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    expected = [
        ["prepare-azure.sh"],
        ["deploy-infrastructure.sh", "--validate-only"],
    ]
    if validation_fails:
        assert result.returncode != 0
    else:
        assert result.returncode == 0, result.stderr
        expected.extend(
            [
                ["build-images.sh"],
                ["deploy-infrastructure.sh"],
                ["install-opensandbox.sh"],
                ["verify-deployment.sh"],
            ]
        )
    assert calls(commands) == expected
