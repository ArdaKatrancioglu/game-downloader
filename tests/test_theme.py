import json

from game_downloader.ui.theme import DEFAULT_THEME, load_theme


def test_theme_file_is_created_and_can_override_colors(tmp_path):
    path = tmp_path / "theme.json"

    initial = load_theme(path)
    configured = json.loads(path.read_text(encoding="utf-8"))
    assert configured == DEFAULT_THEME
    assert DEFAULT_THEME["background"] in initial

    configured["background"] = "#112233"
    path.write_text(json.dumps(configured), encoding="utf-8")
    customized = load_theme(path)

    assert "#112233" in customized
    assert "@background@" not in customized


def test_invalid_theme_values_fall_back_to_defaults(tmp_path):
    path = tmp_path / "theme.json"
    path.write_text('{"accent": "red"}', encoding="utf-8")

    stylesheet = load_theme(path)

    assert DEFAULT_THEME["accent"] in stylesheet
