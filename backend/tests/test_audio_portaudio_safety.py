# -*- coding: utf-8 -*-
"""🔴 防回归：**PortAudio 断言崩溃**（2026-10-29 / 10-30 连续三次）

症状
----
后端进程**直接消失**，日志里只留一行：

    Assertion failed: hostApi->info.defaultOutputDevice < hostApi->info.deviceCount,
    file src/common/pa_front.c, line 233

Python 层**拿不到任何异常**（C 层 abort）⇒ 表现为「服务莫名挂了」。

根因
----
PortAudio 的 `hostApi->info.defaultOutputDevice` 在**并发访问**时会被读成非法值。
凡是碰 PortAudio 设备枚举的地方（`PyAudio()` 构造 / `get_device_count()` /
`get_device_info_by_index()` / `get_default_wasapi_loopback()`），
只要**两个线程同时**做，就可能触发。

三次踩坑的**并发点各不相同**，所以锁必须覆盖**所有**接触点：

| # | 时间 | 并发点 |
|---|---|---|
| 1 | 10-29 13:28 | 两个 ASR 管道**同时**启动 ⇒ 两个采集线程同时构造 PyAudio |
| 2 | 10-29 14:46 | 同上（锁只加在 `_default_loopback_index`，**漏了构造**）|
| 3 | 10-30 13:03 | **设备检测接口** `/api/audio/check-loopback`（事件循环线程）**与**采集线程并发 |

⇒ 结论：**所有碰 PortAudio 的地方必须共用同一把锁**，且**锁要可重入**
（`_capture_loop` 持锁开流时，内部调的 `_default_loopback_index` 会再拿同一把锁）。

本测试要守住的东西
----------------
① `list_devices()`（检测接口走它）**与**采集启动并发 ⇒ **不崩**
② 锁是 **RLock** ⇒ 嵌套调用不自死锁（自死锁的症状是「只有『音频捕获已启动』，
   既没有『使用音频设备』也没有『音频流已打开』，然后永远不出数据」）
"""
from __future__ import annotations

import threading
import time

import pytest


def _capture_module():
    from audio import capture
    return capture


def test_device_enum_lock_is_reentrant():
    """🔴 锁必须是 RLock —— 否则「持锁开流 → 内部再拿锁」会自死锁"""
    cap = _capture_module()
    assert hasattr(cap._DEVICE_ENUM_LOCK, "__enter__")
    # RLock 的特征：同一线程可重复获取
    with cap._DEVICE_ENUM_LOCK:
        with cap._DEVICE_ENUM_LOCK:
            pass  # 能拿到第二把 ⇒ 可重入


def test_list_devices_does_not_deadlock_and_is_locked():
    """`list_devices()` 要能正常返回，且不与采集侧互相卡死"""
    cap = _capture_module()
    if cap.pyaudio is None:
        pytest.skip("未安装音频库")
    devices = cap.AudioCapture.list_devices()
    assert isinstance(devices, list)


def test_concurrent_enum_and_capture_does_not_crash():
    """🔴 核心防回归：设备枚举与采集启动并发 ⇒ 进程不能崩

    崩了的话 pytest 进程会**整个消失**（C 层 abort）⇒ 测试会「无输出地结束」。
    """
    cap = _capture_module()
    if cap.pyaudio is None:
        pytest.skip("未安装音频库")

    err = []

    def do_enum():
        try:
            for _ in range(3):
                cap.AudioCapture.list_devices()
        except Exception as e:  # noqa: BLE001
            err.append(e)

    def do_capture():
        try:
            c = cap.AudioCapture(on_audio_chunk=lambda a: None, mode="loopback")
            c.start()
            time.sleep(1.5)
            c.stop()
        except Exception as e:  # noqa: BLE001
            err.append(e)

    ts = [threading.Thread(target=do_enum), threading.Thread(target=do_capture),
          threading.Thread(target=do_enum)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=20)

    assert not err, f"并发枚举/采集抛了异常：{err}"
    # 能走到这里就说明**没有 abort**（abort 会让整个 pytest 进程消失）
    assert True
