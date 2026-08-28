from __future__ import annotations

import pytest

from lyria_auto.cli import main


def test_resume_cli_reports_clear_message_when_nothing_to_resume(project_config, capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["--root", str(project_config.root), "resume"])

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "找不到可續跑的工作" in captured.out


def test_resume_rejects_channel_flag(project_config):
    with pytest.raises(SystemExit) as exc_info:
        main(["--root", str(project_config.root), "resume", "--channel", "main"])
    assert exc_info.value.code == 2


def test_run_cli_reports_clean_message_on_generation_failure(project_config, fake_lyria, monkeypatch, capsys):
    # 分支自己引進的復原提示（GenerationError「可用 lyria-auto resume 續跑」）在 run 這條路徑上
    # 過去沒有 try/except 接住，只有 resume 有——GenerationError 會一路往外傳，main() 完全不會
    # 轉成 SystemExit(1)，而是讓 pytest.raises(SystemExit) 這個 context 本身就失敗（改丟
    # GenerationError）。這裡的 pytest.raises(SystemExit) 就是關鍵判別點：修好之前這個 with
    # 區塊會直接因為例外型別不符而炸掉。
    #
    # 注意：run_one 內部本來就有 logger.exception 會把 traceback 寫進 log／stderr 供除錯用，
    # 那是既有行為、與這個修復無關，所以這裡不斷言 stderr 乾淨，只驗證使用者看到的 stdout。
    monkeypatch.setattr("lyria_auto.pipeline.LyriaClient", fake_lyria.client_factory())
    fake_lyria.fail_targets["track_02_raw.mp3"] = 99  # 永久失敗，耗盡重試

    with pytest.raises(SystemExit) as exc_info:
        main(["--root", str(project_config.root), "run", "--videos", "1"])

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "可用 lyria-auto resume 續跑" in captured.out
    assert "Traceback" not in captured.out
