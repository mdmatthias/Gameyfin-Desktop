"""Tests for the Proton version manager (service, drop-down and settings section)."""
import hashlib
import io
import os
import tarfile
from unittest.mock import MagicMock

import pytest

from gameyfin_frontend.services import proton_manager
from gameyfin_frontend.services.proton_manager import (
    InstallCancelled, ProtonRelease, ProtonSource,
)


@pytest.fixture()
def compat_dirs(tmp_path, monkeypatch):
    """Point every compatibility tools directory into tmp_path."""
    native = tmp_path / "steam" / "compatibilitytools.d"
    flatpak = tmp_path / "flatpak-steam" / "compatibilitytools.d"
    native.mkdir(parents=True)
    monkeypatch.setattr(proton_manager, "INSTALL_DIR", str(native))
    monkeypatch.setattr(proton_manager, "COMPAT_TOOL_DIRS", [str(native), str(flatpak)])
    return native, flatpak


def _make_build(directory, name):
    path = directory / name
    path.mkdir(parents=True)
    (path / "proton").write_text("#!/usr/bin/env python3\n")
    return path


def _tarball(top_dir: str, with_proton: bool = True) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        files = {f"{top_dir}/files/bin/wine": b"wine"}
        if with_proton:
            files[f"{top_dir}/proton"] = b"#!/usr/bin/env python3\n"
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        link = tarfile.TarInfo(f"{top_dir}/files/bin/wine64")
        link.type = tarfile.SYMTYPE
        link.linkname = "wine"
        tar.addfile(link)
    return buffer.getvalue()


def _response(content: bytes = b"", text: str = "", json_data=None):
    response = MagicMock()
    response.content = content
    response.text = text
    response.headers = {"content-length": str(len(content))}
    response.json.return_value = json_data
    response.iter_content.side_effect = lambda size: (
        content[i:i + size] for i in range(0, len(content), size))
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


def _fake_get(routes):
    def get(url, **_kwargs):
        return routes[url]
    return get


def _release(name="GE-Proton11-7-x86_64", checksum=True):
    return ProtonRelease(
        tag="GE-Proton11-7", name=name,
        url=f"https://example.invalid/{name}.tar.gz", size=0,
        checksum_url=f"https://example.invalid/{name}.sha512sum" if checksum else None,
        stem=name,
    )


class TestListAndRemove:
    def test_lists_builds_with_proton_script_only(self, compat_dirs):
        native, flatpak = compat_dirs
        _make_build(native, "GE-Proton10-9")
        _make_build(native, "GE-Proton10-10")
        _make_build(flatpak, "proton-cachyos-11.0")
        (native / "not-a-proton").mkdir()
        (native / ".gameyfin-GE-Proton11-1.extract").mkdir()

        names = [p.name for p in proton_manager.list_installed()]
        assert "not-a-proton" not in names
        assert not any(n.startswith(".gameyfin-") for n in names)
        # Natural sort, newest first
        assert names.index("GE-Proton10-10") < names.index("GE-Proton10-9")
        assert "proton-cachyos-11.0" in names

    def test_symlinked_dirs_are_listed_once(self, compat_dirs, tmp_path, monkeypatch):
        native, _ = compat_dirs
        _make_build(native, "GE-Proton10-9")
        alias = tmp_path / "steam-root-compat"
        alias.symlink_to(native)
        monkeypatch.setattr(proton_manager, "COMPAT_TOOL_DIRS", [str(native), str(alias)])
        assert len(proton_manager.list_installed()) == 1

    def test_remove_deletes_build(self, compat_dirs):
        native, _ = compat_dirs
        path = _make_build(native, "GE-Proton10-9")
        proton_manager.remove(str(path))
        assert not path.exists()

    def test_remove_refuses_paths_outside_compat_dirs(self, compat_dirs, tmp_path):
        outside = _make_build(tmp_path / "elsewhere", "GE-Proton10-9")
        with pytest.raises(ValueError):
            proton_manager.remove(str(outside))
        assert outside.exists()

    def test_remove_refuses_non_proton_dirs(self, compat_dirs):
        native, _ = compat_dirs
        other = native / "something"
        other.mkdir()
        with pytest.raises(ValueError):
            proton_manager.remove(str(other))


class TestFetchReleases:
    def test_picks_x86_64_asset_and_checksum(self, monkeypatch):
        data = [{
            "tag_name": "GE-Proton11-7", "prerelease": False, "published_at": "2026-09-01T00:00:00Z",
            "assets": [
                {"name": "GE-Proton11-7-aarch64.tar.gz", "browser_download_url": "u/arm", "size": 1},
                {"name": "GE-Proton11-7-x86_64.sha512sum", "browser_download_url": "u/sum", "size": 1},
                {"name": "GE-Proton11-7-x86_64.tar.gz", "browser_download_url": "u/x86", "size": 42},
            ],
        }, {"tag_name": "draft", "draft": True, "assets": []},
           {"tag_name": "no-assets", "assets": []}]
        monkeypatch.setattr(proton_manager.requests, "get", lambda *a, **k: _response(json_data=data))

        releases = proton_manager.fetch_releases(ProtonSource("GE", "a/b", ""), prefer_v3=False)
        assert len(releases) == 1
        release = releases[0]
        assert release.url == "u/x86"
        assert release.checksum_url == "u/sum"
        assert release.stem == "GE-Proton11-7-x86_64"
        assert release.published == "2026-09-01"
        assert release.size == 42

    @pytest.mark.parametrize("prefer_v3, expected", [(True, "u/v3"), (False, "u/base")])
    def test_cachyos_microarch_choice(self, monkeypatch, prefer_v3, expected):
        data = [{"tag_name": "cachyos-11.0", "assets": [
            {"name": "proton-cachyos-11.0-slr-arm64.tar.xz", "browser_download_url": "u/arm"},
            {"name": "proton-cachyos-11.0-slr-x86_64.tar.xz", "browser_download_url": "u/base"},
            {"name": "proton-cachyos-11.0-slr-x86_64_v3.tar.xz", "browser_download_url": "u/v3"},
        ]}]
        monkeypatch.setattr(proton_manager.requests, "get", lambda *a, **k: _response(json_data=data))
        releases = proton_manager.fetch_releases(ProtonSource("C", "a/b", ""), prefer_v3=prefer_v3)
        assert releases[0].url == expected

    def test_is_installed_matches_stem_or_tag(self):
        release = _release()
        installed = [proton_manager.InstalledProton("GE-Proton11-7-x86_64", "/x")]
        assert proton_manager.is_installed(release, installed)
        assert proton_manager.is_installed(release, [proton_manager.InstalledProton("GE-Proton11-7", "/x")])
        assert not proton_manager.is_installed(release, [proton_manager.InstalledProton("GE-Proton11-70", "/x")])


class TestInstallRelease:
    def test_installs_and_verifies(self, compat_dirs, monkeypatch):
        native, _ = compat_dirs
        release = _release()
        archive = _tarball(release.stem)
        digest = hashlib.sha512(archive).hexdigest()
        monkeypatch.setattr(proton_manager.requests, "get", _fake_get({
            release.checksum_url: _response(text=f"{digest}  {release.stem}.tar.gz\n"),
            release.url: _response(content=archive),
        }))
        stages = []

        path = proton_manager.install_release(
            release, progress=lambda stage, done, total: stages.append(stage), chunk_size=64)

        assert path == str(native / release.stem)
        assert os.path.isfile(os.path.join(path, "proton"))
        assert os.path.islink(os.path.join(path, "files", "bin", "wine64"))
        assert "download" in stages and "extract" in stages
        # Only the build is left behind, no temp files
        assert os.listdir(native) == [release.stem]

    def test_checksum_mismatch_leaves_nothing(self, compat_dirs, monkeypatch):
        native, _ = compat_dirs
        release = _release()
        monkeypatch.setattr(proton_manager.requests, "get", _fake_get({
            release.checksum_url: _response(text=f"{'0' * 128}  {release.stem}.tar.gz\n"),
            release.url: _response(content=_tarball(release.stem)),
        }))
        with pytest.raises(ValueError, match="Checksum"):
            proton_manager.install_release(release)
        assert os.listdir(native) == []

    def test_archive_without_proton_is_rejected(self, compat_dirs, monkeypatch):
        native, _ = compat_dirs
        release = _release(checksum=False)
        monkeypatch.setattr(proton_manager.requests, "get", _fake_get({
            release.url: _response(content=_tarball(release.stem, with_proton=False)),
        }))
        with pytest.raises(ValueError, match="does not contain"):
            proton_manager.install_release(release)
        assert os.listdir(native) == []

    def test_cancel_cleans_up(self, compat_dirs, monkeypatch):
        native, _ = compat_dirs
        release = _release(checksum=False)
        monkeypatch.setattr(proton_manager.requests, "get", _fake_get({
            release.url: _response(content=_tarball(release.stem)),
        }))
        with pytest.raises(InstallCancelled):
            proton_manager.install_release(release, is_cancelled=lambda: True)
        assert os.listdir(native) == []


class TestParsing:
    def test_parse_checksum(self):
        digest = "a" * 128
        assert proton_manager.parse_checksum(f"{digest}  GE.tar.gz\n", "GE.tar.gz") == digest
        assert proton_manager.parse_checksum(f"{digest} *dir/GE.tar.gz", "GE.tar.gz") == digest
        assert proton_manager.parse_checksum(f"{digest}  other.tar.gz", "GE.tar.gz") is None

    def test_display_name(self):
        assert proton_manager.display_name("/a/b/GE-Proton10-9/") == "GE-Proton10-9"
        assert proton_manager.display_name("GE-Proton") == "GE-Proton"
        assert proton_manager.display_name("") == "GE-Proton"


class TestProtonComboBox:
    def test_lists_default_and_installed(self, qtbot, compat_dirs):
        from gameyfin_frontend.proton_combo import ProtonComboBox
        native, _ = compat_dirs
        build = _make_build(native, "GE-Proton10-9")

        combo = ProtonComboBox(value=str(build))
        qtbot.addWidget(combo)
        assert combo.itemData(0) == "GE-Proton"
        assert combo.value() == str(build)
        assert combo.currentText() == "GE-Proton10-9"

    def test_unknown_value_is_kept(self, qtbot, compat_dirs):
        from gameyfin_frontend.proton_combo import ProtonComboBox
        combo = ProtonComboBox(value="/opt/proton/Custom")
        qtbot.addWidget(combo)
        assert combo.value() == "/opt/proton/Custom"
        assert "not installed" in combo.currentText()

    def test_bare_name_selects_installed_build(self, qtbot, compat_dirs):
        from gameyfin_frontend.proton_combo import ProtonComboBox
        native, _ = compat_dirs
        build = _make_build(native, "GE-Proton10-9")
        combo = ProtonComboBox(value="GE-Proton10-9")
        qtbot.addWidget(combo)
        assert combo.value() == str(build)

    def test_refresh_keeps_selection(self, qtbot, compat_dirs):
        from gameyfin_frontend.proton_combo import ProtonComboBox
        native, _ = compat_dirs
        build = _make_build(native, "GE-Proton10-9")
        combo = ProtonComboBox(value=str(build))
        qtbot.addWidget(combo)
        _make_build(native, "GE-Proton10-10")
        combo.refresh()
        assert combo.value() == str(build)
        assert combo.count() == 3

    def test_empty_value_is_default(self, qtbot, compat_dirs):
        from gameyfin_frontend.proton_combo import ProtonComboBox
        combo = ProtonComboBox(value="")
        qtbot.addWidget(combo)
        assert combo.value() == "GE-Proton"


class TestProtonManagerWidget:
    @pytest.fixture()
    def section(self, qtbot, compat_dirs):
        from gameyfin_frontend.widgets.proton_manager import ProtonManagerWidget
        widget = ProtonManagerWidget()
        qtbot.addWidget(widget)
        return widget

    def test_lists_installed_builds(self, qtbot, compat_dirs):
        from gameyfin_frontend.widgets.proton_manager import ProtonManagerWidget
        native, _ = compat_dirs
        _make_build(native, "GE-Proton10-9")
        widget = ProtonManagerWidget()
        qtbot.addWidget(widget)
        assert widget.installed_list.count() == 1
        assert widget.installed_list.item(0).text() == "GE-Proton10-9"

    def test_releases_populate_versions(self, section, compat_dirs):
        native, _ = compat_dirs
        _make_build(native, "GE-Proton11-7-x86_64")
        section.refresh_installed()
        section._fetching_source = "GE-Proton"
        section._on_releases_fetched([_release(), _release("GE-Proton11-6-x86_64")], "")

        assert section.version_combo.count() == 2
        assert "installed" in section.version_combo.itemText(0)
        assert "installed" not in section.version_combo.itemText(1)
        assert section.install_button.isEnabled()

    def test_fetch_error_is_shown(self, section):
        section._fetching_source = "GE-Proton"
        section._on_releases_fetched(None, "rate limited")
        assert "rate limited" in section.status_label.text()
        assert not section.install_button.isEnabled()

    def test_remove_selected(self, qtbot, compat_dirs, monkeypatch):
        from PyQt6.QtWidgets import QMessageBox
        from gameyfin_frontend.widgets.proton_manager import ProtonManagerWidget
        native, _ = compat_dirs
        build = _make_build(native, "GE-Proton10-9")
        widget = ProtonManagerWidget()
        qtbot.addWidget(widget)
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)

        widget.installed_list.setCurrentRow(0)
        with qtbot.waitSignal(widget.installed_changed):
            widget._remove_selected()
        assert not build.exists()
        assert not widget.remove_button.isEnabled()

    def test_settings_default_combo_refreshes_and_saves(self, qtbot, compat_dirs, fresh_settings, monkeypatch):
        from gameyfin_frontend.settings_widget import SettingsWidget
        native, _ = compat_dirs
        widget = SettingsWidget(settings=fresh_settings)
        qtbot.addWidget(widget)
        assert widget.proton_combo.count() == 1

        build = _make_build(native, "GE-Proton10-9")
        widget.refresh_proton_versions()
        assert widget.proton_combo.count() == 2

        widget.proton_combo.set_value(str(build))
        from PyQt6.QtWidgets import QMessageBox
        monkeypatch.setattr(QMessageBox, "information", MagicMock())
        widget.save_settings()
        assert fresh_settings.get("PROTONPATH") == str(build)

    def test_install_runs_worker_and_refreshes(self, qtbot, section, compat_dirs, monkeypatch):
        native, _ = compat_dirs
        release = _release(checksum=False)

        def fake_install(rel, progress=None, is_cancelled=None, **_kwargs):
            progress("download", 50, 100)
            path = _make_build(native, rel.stem)
            return str(path)

        monkeypatch.setattr(proton_manager, "install_release", fake_install)
        section._fetching_source = "GE-Proton"
        section._on_releases_fetched([release], "")
        assert not section.status_label.isVisibleTo(section)

        with qtbot.waitSignal(section.installed_changed, timeout=5000):
            section._install_selected()
            assert not section.install_button.isEnabled()
        assert "Installed GE-Proton11-7-x86_64" in section.status_label.text()
        assert section.installed_list.item(0).text() == release.stem
        assert "installed" in section.version_combo.itemText(0)
        assert section.install_button.isEnabled()
        section.shutdown()
