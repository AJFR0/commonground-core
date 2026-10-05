import json

import yaml
from conftest import BASE

from commonground_core.docsync import cli, fetch


def test_cli_end_to_end(tmp_path, site, monkeypatch, capsys):
    monkeypatch.setattr(fetch, "requests_transport", site)
    cfg = tmp_path / "src.yaml"
    cfg.write_text(yaml.safe_dump({
        "name": "clidocs", "sitemaps": [BASE + "/sitemap_index.xml"],
        "fetch": {"delay_seconds": 0, "workers": 1},
        "sections": {"/guides/": "fundamentals"},
        "prepare": {"profiles": {"markdown": {"format": "markdown"}}},
    }))
    root = str(tmp_path / "data")
    common = ["-c", str(cfg), "--root", root]

    assert cli.main(["sync", *common]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["counts"] == {"added": 2, "blocked": 1}

    assert cli.main(["status", *common, "--sections"]) == 0
    st = json.loads(capsys.readouterr().out)
    assert st["docs"] == {"active": 2, "blocked": 1} and st["sections"] == {"fundamentals": 2}

    assert cli.main(["changes", *common]) == 0
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    assert {x["event"] for x in lines} == {"added"}

    assert cli.main(["prepare", *common, "--profile", "markdown"]) == 0
    assert json.loads(capsys.readouterr().out)["written"] == 2

    assert cli.main(["export", *common, "--what", "prepared/markdown", "--to", str(tmp_path / "vol")]) == 0
    assert json.loads(capsys.readouterr().out)["copied"] == 2
