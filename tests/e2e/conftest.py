import pytest

from tests.e2e.rig import AppRig


@pytest.fixture
def make_rig(tmp_path, monkeypatch):
    rigs = []

    def factory(**kwargs):
        workdir = kwargs.pop("workdir", tmp_path / f"rig{len(rigs)}")
        rig = AppRig(workdir, monkeypatch, **kwargs)
        rigs.append(rig)
        return rig

    yield factory
    for rig in reversed(rigs):
        rig.close()


@pytest.fixture
def rig(make_rig):
    return make_rig()
