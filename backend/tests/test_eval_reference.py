"""`eval_asr_quality.reference_path()` 的常驻用例

为什么值一组测试：参考稿的选取**不会报错**。取错、或者取不到，只会让 WER/CER
悄悄消失（或悄悄算在一份过期基准上）—— 本文件防的就是这种静默。

实测来历：real/ 素材的定稿叫 `*.preproof.txt`（预校对 + 人工裁决的产物），
而旧实现只找 `<stem>.txt`，于是整轮评估被判「无参考稿」，CER 统计直接消失，
日志里只有一行「（无参考文本）」轻描淡写地过去。

约定：全部在 `tmp_path` 里构造文件，不读真实素材、不跑模型。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "eval_asr_quality.py"

pytestmark = pytest.mark.skipif(
    not SCRIPT.is_file(), reason="scripts/eval_asr_quality.py 不存在（仓库不完整？）"
)


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # 必须先注册进 sys.modules 再 exec（理由见 test_preproof_transcript._load）
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return mod


@pytest.fixture(scope="module")
def ev():
    return _load("eval_asr_quality_under_test", SCRIPT)


def _audio(tmp_path: Path) -> Path:
    wav = tmp_path / "clip.wav"
    wav.write_bytes(b"RIFF")          # 只占位，不参与解码
    return wav


def test_reference_prefers_preproof_over_bare_txt(ev, tmp_path):
    """定稿与裸 txt 同时存在时必须取定稿。

    反过来的话，基准就是一份**会静默过期的副本** —— 定稿更新了它不会跟着变。
    """
    wav = _audio(tmp_path)
    (tmp_path / "clip.txt").write_text("草稿版参考", encoding="utf-8")
    (tmp_path / "clip.preproof.txt").write_text("定稿版参考", encoding="utf-8")
    got = ev.reference_path(wav)
    assert got is not None and got.name == "clip.preproof.txt", got


def test_reference_falls_back_to_bare_txt(ev, tmp_path):
    """反向断言：只有裸 txt 时不能因为「没有定稿」就返回 None（那会让 CER 消失）。"""
    wav = _audio(tmp_path)
    (tmp_path / "clip.txt").write_text("草稿版参考", encoding="utf-8")
    got = ev.reference_path(wav)
    assert got is not None and got.name == "clip.txt", got


def test_reference_returns_none_when_absent(ev, tmp_path):
    """两份都没有 → None（调用侧据此跳过 WER/CER，而不是拿空串当参考）。"""
    assert ev.reference_path(_audio(tmp_path)) is None


def test_reference_does_not_match_other_stems(ev, tmp_path):
    """反向断言：不得串到同目录其它素材的参考稿上（多文件目录里最危险）。

    只断言 None 还不够 —— 要顺带证明「目录里确实有参考稿可串」，否则这条
    断言会被「目录本来就是空的」顺手满足。
    """
    wav = _audio(tmp_path)
    other = tmp_path / "other.preproof.txt"
    other.write_text("别的素材", encoding="utf-8")
    assert other.is_file(), "前提：目录里必须存在别的参考稿，否则本用例失去分辨力"
    assert ev.reference_path(wav) is None
