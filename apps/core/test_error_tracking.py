from config.error_tracking import init_error_tracking


def test_error_tracking_is_off_without_a_dsn():
    assert init_error_tracking("") is False


def test_error_tracking_never_raises_when_the_package_is_missing(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("sentry_sdk"):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert init_error_tracking("https://key@example.invalid/1") is False
