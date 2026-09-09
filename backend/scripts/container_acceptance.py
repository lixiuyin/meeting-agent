"""Exercise built production containers on an owned network and empty data volume."""

import argparse
import base64
import json
import os
import secrets
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]


def docker(*args: str, check: bool = True) -> str:
    result = subprocess.run(["docker", *args], check=check, capture_output=True, text=True)
    return (result.stdout + result.stderr).strip() if args[0] == "logs" else result.stdout.strip()


def run(backend_image: str, frontend_image: str) -> dict:
    suffix = secrets.token_hex(5)
    network, backend, frontend = (f"meeting-qa-{kind}-{suffix}" for kind in ("net", "be", "fe"))
    checks = {}
    with tempfile.TemporaryDirectory(prefix="meeting-container-qa-") as temporary:
        owned = Path(temporary)
        data = owned / "data"
        data.mkdir()
        key, password = secrets.token_hex(24), secrets.token_hex(16)
        hashed = subprocess.run(
            ["openssl", "passwd", "-apr1", "-stdin"],
            input=password,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        values = {
            **dotenv_values(ROOT / ".env"),
            **dotenv_values(ROOT / "backend" / ".env"),
            **os.environ,
        }
        model_keys = ("LLM_", "EMBEDDING_", "RERANKER_", "QUERY_REWRITE_")
        environment = {
            name: str(value)
            for name, value in values.items()
            if name.startswith(model_keys) and value is not None and "\n" not in str(value)
        }
        environment.update(
            ENVIRONMENT="production",
            API_KEY=key,
            PRINCIPAL_ID="container_acceptance",
            PRINCIPAL_PEPPER=secrets.token_hex(32),
            TRUSTED_HOSTS="127.0.0.1,localhost,backend",
            CORS_ORIGINS="http://127.0.0.1",
            DATA_DIR="/app/data",
            DB_PATH="/app/data/meetings.db",
            UPLOAD_DIR="/app/data/uploads",
            VECTOR_DB_DIR="/app/data/vectordb",
            LOG_DIR="/app/data/logs",
            RAGANYTHING_ENABLED="false",
            MEETING_AUTO_SUMMARIZE_FILES="false",
            COMBINED_EXTRACTION_ENABLED="false",
            SESSION_SUMMARY_ENABLED="false",
            FRONTEND_AUTH_USER="reviewer",
            FRONTEND_AUTH_PASSWORD_HASH=hashed,
        )
        for name in ("http_proxy", "https_proxy"):
            value = os.environ.get(name.upper())
            if value:
                environment[name] = value.replace("127.0.0.1", "host.lima.internal")
        environment["no_proxy"] = "127.0.0.1,localhost,backend"
        envfile = owned / "runtime.env"
        envfile.write_text(
            "\n".join(f"{name}={value}" for name, value in environment.items()) + "\n"
        )
        envfile.chmod(0o600)
        auth = "Basic " + base64.b64encode(f"reviewer:{password}".encode()).decode()

        def request(url, *, headers=None, payload=None):
            request_headers = dict(headers or {})
            if payload is not None:
                request_headers["Content-Type"] = "application/json"
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode() if payload is not None else None,
                headers=request_headers,
            )
            try:
                with urllib.request.urlopen(req, timeout=120) as response:
                    return response.status, response.read(), dict(response.headers)
            except urllib.error.HTTPError as error:
                return error.code, error.read(), dict(error.headers)

        def published_port(container: str, port: int) -> str:
            # Some remote daemons publish ports shortly after run returns.
            for _ in range(30):
                state = json.loads(docker("inspect", container))[0]
                bindings = state["NetworkSettings"].get("Ports", {}).get(f"{port}/tcp")
                if bindings:
                    return bindings[0]["HostPort"]
                if state["State"]["Status"] in {"exited", "dead"}:
                    raise RuntimeError(
                        f"{container} exited before publishing its port: {state['State']}"
                    )
                time.sleep(0.5)
            raise RuntimeError(f"{container} did not publish port {port}")

        try:
            docker("network", "create", network)
            docker(
                "run",
                "-d",
                "--name",
                backend,
                "--network",
                network,
                "--network-alias",
                "backend",
                "--env-file",
                str(envfile),
                "--read-only",
                "--tmpfs",
                "/tmp",
                "--security-opt",
                "no-new-privileges",
                "--cap-drop",
                "ALL",
                "--cap-add",
                "CHOWN",
                "--cap-add",
                "SETUID",
                "--cap-add",
                "SETGID",
                "-p",
                "127.0.0.1::8000",
                "-v",
                f"{data}:/app/data",
                backend_image,
            )
            be_port = published_port(backend, 8000)
            be_url = f"http://127.0.0.1:{be_port}"
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                try:
                    if request(be_url + "/api/v1/health/ready")[0] == 200:
                        break
                except OSError:
                    pass
                time.sleep(1)
            else:
                raise RuntimeError("backend readiness timeout")
            checks["backend_ready"] = True
            docker(
                "run",
                "-d",
                "--name",
                frontend,
                "--network",
                network,
                "--env-file",
                str(envfile),
                "--security-opt",
                "no-new-privileges",
                "--cap-drop",
                "ALL",
                "-p",
                "127.0.0.1::8080",
                frontend_image,
            )
            fe_port = published_port(frontend, 8080)
            fe_url = f"http://127.0.0.1:{fe_port}"
            for _ in range(30):
                try:
                    if request(fe_url + "/healthz")[0] == 200:
                        break
                except OSError:
                    pass
                time.sleep(1)
            headers = {"Authorization": auth}
            checks["frontend_requires_login"] = request(fe_url + "/")[0] == 401
            status, body, response_headers = request(fe_url + "/", headers=headers)
            checks["authenticated_spa"] = status == 200 and b'<div id="root">' in body
            checks["security_headers"] = response_headers.get("X-Content-Type-Options") == "nosniff"
            checks["nginx_ready_proxy"] = (
                request(fe_url + "/api/v1/health/ready", headers=headers)[0] == 200
            )
            checks["direct_api_requires_key"] = request(be_url + "/api/v1/meetings")[0] in {
                401,
                403,
            }
            checks["nginx_injects_api_key"] = (
                request(fe_url + "/api/v1/meetings", headers=headers)[0] == 200
            )
            code, body, _ = request(
                fe_url + "/api/v1/chat/stream",
                headers=headers,
                payload={
                    "question": "列出已记录的任务",
                    "memory_mode": "balanced",
                    "web_search_mode": "off",
                },
            )
            events = [
                json.loads(line[6:])
                for line in body.decode().splitlines()
                if line.startswith("data: ")
            ]
            checks["stream_proxy_completes"] = (
                code == 200
                and any(e.get("type") == "done" for e in events)
                and not any(e.get("type") == "error" for e in events)
            )
            metric_code, metrics, _ = request(be_url + "/metrics", headers={"X-API-Key": key})
            metric_sample = [
                line
                for line in metrics.decode().splitlines()
                if line.startswith("chat_completion_total{")
            ]
            checks["completion_metrics_exported"] = (
                metric_code == 200
                and b'chat_completion_total{endpoint="/api/v1/chat/stream",outcome="success"} 1.0'
                in metrics
            )
            code, structured_body, _ = request(
                fe_url + "/api/v1/chat",
                headers=headers,
                payload={
                    "question": (
                        "What can be concluded without uploaded evidence? "
                        'Return only JSON with a single "answer" field.'
                    ),
                    "memory_mode": "off",
                    "web_search_mode": "off",
                },
            )
            try:
                structured_answer = json.loads(json.loads(structured_body)["answer"])
            except (ValueError, KeyError, TypeError):
                structured_answer = None
            checks["explicit_json_output_contract"] = (
                code == 200
                and isinstance(structured_answer, dict)
                and set(structured_answer) == {"answer"}
                and isinstance(structured_answer["answer"], str)
                and bool(structured_answer["answer"])
            )
            checks["runtime_starlette_patched"] = docker(
                "exec",
                backend,
                "/app/.venv/bin/python",
                "-c",
                "import starlette; print(starlette.__version__)",
            ).startswith("1.")
            return {
                "completed": True,
                "checks": checks,
                "completion_metric_http_status": metric_code,
                "completion_metric_samples": metric_sample,
                "passed": all(checks.values()),
                "backend_image_id": docker(
                    "image", "inspect", backend_image, "--format", "{{.Id}}"
                ),
                "frontend_image_id": docker(
                    "image", "inspect", frontend_image, "--format", "{{.Id}}"
                ),
                "release_ready": False,
                "scope": (
                    "isolated production authentication, Nginx, SSE, "
                    "and runtime checks; no deployment"
                ),
            }
        except Exception as error:
            return {
                "completed": False,
                "checks": checks,
                "passed": False,
                "error": f"{type(error).__name__}: {error}",
                "backend_log": docker("logs", "--tail", "40", backend, check=False),
                "frontend_log": docker("logs", "--tail", "20", frontend, check=False),
                "release_ready": False,
            }
        finally:
            docker("rm", "-f", frontend, backend, check=False)
            docker("network", "rm", network, check=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend-image", required=True)
    parser.add_argument("--frontend-image", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.backend_image, args.frontend_image)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if not k.endswith("_log")}, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
