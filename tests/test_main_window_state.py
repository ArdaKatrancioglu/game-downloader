from types import SimpleNamespace

from game_downloader.ui.main_window import MainWindow


def test_clearing_selected_result_clears_list_and_metadata():
    events = []

    class Results:
        def clearSelection(self):
            events.append("selection-cleared")

        def setCurrentRow(self, row):
            events.append(("current-row", row))

    window = SimpleNamespace(
        current_release=object(),
        results=Results(),
        _reset_selected_metadata=lambda: events.append("metadata-cleared"),
    )

    MainWindow._clear_selected_result(window)

    assert window.current_release is None
    assert events == ["selection-cleared", ("current-row", -1), "metadata-cleared"]
