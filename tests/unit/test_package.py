import specter


def test_package_exposes_version_when_installed() -> None:
    assert specter.__version__
