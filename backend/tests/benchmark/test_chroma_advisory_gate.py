from scripts.check_chroma_advisories import advisory_errors


def _source(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "store.py").write_text("import chromadb\nchromadb.PersistentClient(path='x')\n")
    return source


def test_reviewed_server_advisories_are_accepted_for_embedded_client(tmp_path) -> None:
    payload = {
        "dependencies": [
            {
                "name": "chromadb",
                "vulns": [
                    {"id": "CVE-2026-45830", "aliases": [], "fix_versions": []},
                    {
                        "id": "PYSEC-2026-311",
                        "aliases": ["CVE-2026-45829"],
                        "fix_versions": [],
                    },
                ],
            }
        ]
    }
    assert advisory_errors(payload, _source(tmp_path)) == []


def test_gate_rejects_new_fix_or_remote_client(tmp_path) -> None:
    source = _source(tmp_path)
    (source / "remote.py").write_text("import chromadb\nchromadb.HttpClient(host='x')\n")
    payload = {
        "dependencies": [
            {
                "name": "chromadb",
                "vulns": [
                    {
                        "id": "CVE-2026-45831",
                        "aliases": [],
                        "fix_versions": ["1.5.10"],
                    }
                ],
            }
        ]
    }
    errors = advisory_errors(payload, source)
    assert any("now has a fix" in error for error in errors)
    assert any("remote Chroma client" in error for error in errors)


def test_gate_rejects_unreviewed_dependency_advisory(tmp_path) -> None:
    payload = {
        "dependencies": [{"name": "other", "vulns": [{"id": "CVE-new", "fix_versions": []}]}]
    }
    assert any("unreviewed" in error for error in advisory_errors(payload, _source(tmp_path)))
