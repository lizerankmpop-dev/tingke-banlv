# -*- coding: utf-8 -*-
"""
中文课堂音频测试集生成器（**代理集，不是真实素材**）

为什么需要它：P3 唯一缺口是「中文真实网课音频 + 逐字稿」，而真实素材必须由
用户下载/录制并人工校对（版权 + 音质）。在素材到位之前，这个脚本用神经 TTS
合成一段 **12 分钟的中文专业课讲稿**，先把两个问题答出来：

  ① 中文场景下，长术语提示词还会不会触发 Whisper 的重复循环？
     （这是解码器 + 提示词的性质，与录音质量基本无关 → 代理集结论可信）
  ② 术语热词能不能降低中文专业词的识别错误率？
     （与发音、音质强相关 → 代理集只能给**上限参考**，真实结论仍以真实素材为准）

代理集**不能**回答或代表的事（必须在报告里写明，不许混淆）：
  · 真实课堂的底噪、混响、口音、语速变化、口误与自我纠正
  · 远场收音的低电平问题（英文合成集已证明 −30dBFS 比背景噪声危险得多）
  · 老师真实的板书停顿、学生提问插话

生成物：
  testset/chinese_lecture/long/long_zhlecture_cnn.wav   16kHz 单声道
  testset/chinese_lecture/long/long_zhlecture_cnn.txt   逐字稿（= 合成文本，天然精确）
  testset/chinese_lecture/metadata.json                 采样率/时长/信噪比/来源说明

依赖：edge-tts（神经 TTS）、soundfile、numpy。项目 venv 里没有 edge-tts，
需要在装了它的解释器里跑；缺依赖时会给出可直接复制的安装命令。

  "<venv>/Scripts/python.exe" scripts/make_chinese_testset.py --out testset/chinese_lecture
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import math
import re
import sys
import time
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000
# 24kHz 是 edge-tts 的输出采样率；重采样到 16k 与 Silero VAD / Whisper 对齐
TTS_RATE = 24000
VOICE = "zh-CN-YunyangNeural"  # 男声、播音腔，最接近 MOOC 讲师

# 段落之间留白：真实课堂必有换气停顿，全连起来会让 VAD 切出一个超长片段，
# 反而测不出「片段数 vs 字幕数」这个关键信号
PARAGRAPH_GAP_S = 0.45
SENTENCE_GAP_S = 0.12

# ============ 讲稿 ============
#
# 写作原则（直接决定这份代理集有没有诊断价值）：
#   1. 主题选「卷积神经网络 + 反向传播 + 优化」—— 内置术语表里 计算机 / 人工智能
#      两个领域的词几乎全落在这个范围，A/B 才有对照意义
#   2. 专业词同时以「中文说法 + 英文原词」出现，这符合国内理工科课堂的真实讲法
#      （老师会说「卷积，就是 convolution」），也正好能测出术语表（英文词）对
#      中文课到底有没有帮助
#   3. 刻意混入几个 Whisper 中文识别的高频易错词：卷积 / 池化 / 残差 / 稀疏 /
#      迭代 / 梯度消失 —— 这些是判断术语注入收益的抓手
#   4. 不使用数字与缩写（TTS 读法与逐字稿容易对不上，会污染 WER 基准）
LECTURE = [
    "同学们好，今天这节课我们来讲卷积神经网络，以及它背后最重要的训练算法，反向传播。"
    "这部分内容是深度学习的核心，也是后面所有模型的基础，希望大家认真听。",

    "我们先回顾一个概念，什么叫神经网络。简单说，它是由很多层神经元堆叠起来的函数逼近器，"
    "每一层做的事情都是对上一层的输出做一次线性变换，再接一个非线性的激活函数。"
    "常用的激活函数有 sigmoid、tanh 和 relu，其中 relu 计算最简单，收敛也最快。",

    "那么问题来了，如果只做线性变换，多层网络和单层网络其实是等价的。"
    "正因为有了非线性激活函数，网络才具备了拟合复杂函数的能力。这一点非常关键，"
    "很多同学在这里会绕不过弯，一定要想清楚。",

    "接下来讲卷积。卷积这个概念来自信号处理，它的本质是用一个小的卷积核，"
    "在输入特征图上滑动，每一步做一次加权求和。这个操作我们称为 convolution。"
    "卷积核的权重是共享的，也就是说同一组参数在整张图上反复使用，"
    "这就是所谓参数共享，它让模型的参数量大幅下降。",

    "滑动的步长我们称为 stride，边缘补零的圈数称为 padding。"
    "步长大，输出特征图就小；补零多，输出就保持原来的尺寸。"
    "这两个超参数决定了每一层输出张量的形状，写代码的时候一定要算清楚。",

    "卷积之后通常会接一个池化层，也就是 pooling。最常见的做法是最大池化，"
    "在一个小窗口里取最大值。池化的作用是降低空间分辨率，扩大感受野，"
    "同时让模型对微小的平移不那么敏感，也就是具备一定的平移不变性。",

    "现在我们把卷积层、池化层交替堆叠起来，最后再接几个全连接层做分类，"
    "这就是一个典型的卷积神经网络，英文叫 convolutional neural network。"
    "它的每一层输出的都是特征，浅层学到的是边缘和纹理，深层学到的是语义部件。",

    "网络结构定下来之后，下一个问题是参数怎么学。我们定义一个损失函数，"
    "用来度量预测值和真实标签之间的差距。分类任务最常用的是交叉熵损失，"
    "回归任务常用的是均方误差。有了损失函数，训练就变成了一个优化问题。",

    "优化的基本方法是梯度下降，gradient descent。我们先计算损失对每个参数的偏导数，"
    "也就是梯度，然后沿着梯度的反方向走一小步。这个步长由学习率控制，"
    "学习率太大容易震荡甚至发散，太小收敛又特别慢，需要仔细调。",

    "那么梯度怎么算出来呢？这就是反向传播要解决的问题。它的核心是链式法则，"
    "从损失出发，一层一层往前回传误差，每一层只需要用到它后面的梯度。"
    "所以一次前向传播加一次反向传播，就能算出所有参数的梯度，效率非常高。",

    "在实际实现里，我们不会一次只喂一个样本，而是把样本分成小批次，称为 batch。"
    "每个批次算一次平均梯度再更新参数，这样既利用了矩阵运算的并行性，"
    "又让梯度估计更稳定。随机梯度下降，也就是 sgd，说的就是这个做法。",

    "训练过程中还有两个绕不开的问题，过拟合和欠拟合，英文是 overfitting 和 underfitting。"
    "欠拟合说明模型能力不够，需要加宽或者加深网络。过拟合则是模型把训练集背了下来，"
    "在验证集上表现很差，这时候需要正则化。",

    "正则化最常用的手段是权重衰减和 dropout。权重衰减是在损失里加上参数范数的惩罚项，"
    "限制参数不要变得太大。dropout 是在训练时随机丢掉一部分神经元的输出，"
    "相当于在训练很多个子网络并做集成，能明显缓解过拟合。",

    "还有两个工程上很常见的技巧，批归一化和残差连接。批归一化把每一层的输入"
    "重新标准化到均值为零、方差为一，可以加快收敛，也让网络对初始化不那么敏感。"
    "残差连接则是把输入直接加到输出上，让梯度有一条更短的回传路径，"
    "这是训练非常深的网络的关键，也是解决梯度消失问题的主要手段。",

    "最后说一句学习方法。这门课的概念很多，但它们是连在一起的："
    "卷积解决的是特征提取，池化解决的是降维，反向传播解决的是参数更新，"
    "正则化解决的是泛化。把这条主线理清楚，比单独背名词有用得多。",

    "接下来我们讲一讲训练过程中怎么判断模型好不好。最直接的办法是看损失曲线，"
    "训练损失和验证损失一起画出来。如果两条线都还在下降，说明还没收敛，"
    "可以继续训练；如果训练损失继续降而验证损失开始上升，那就是典型的过拟合信号。",

    "除了损失，分类任务还要看准确率、精确率和召回率，也就是 precision 和 recall。"
    "这两个指标经常此消彼长，所以我们会用一个综合指标，f1 score，"
    "它是精确率和召回率的调和平均。类别不平衡的时候，只看准确率是会骗人的。",

    "更细致的分析工具是混淆矩阵，confusion matrix。它能告诉你模型到底把哪一类"
    "错判成了哪一类。很多时候准确率看着还行，但混淆矩阵一画就发现，"
    "某个小类别几乎全被预测成了多数类，这种问题只看总分是发现不了的。",

    "还有一个概念叫感受野。深层网络里一个神经元能看到的原始输入区域，"
    "就是它的感受野。卷积核越大、层数越深，感受野越大。"
    "理解感受野很重要，因为它决定了模型能不能捕捉到足够大的结构信息。",

    "说到结构，我们再补充两种常见的卷积变体。一种是把卷积拆成深度卷积和逐点卷积，"
    "也就是深度可分离卷积，参数量和计算量都能大幅下降，适合部署到移动端。"
    "另一种是空洞卷积，在卷积核里插入空洞，不增加参数就能扩大感受野。",

    "数据方面也值得强调。很多同学把精力全放在调网络上，其实数据质量往往更关键。"
    "常见的做法是数据增强，比如随机裁剪、翻转、加噪声，用有限的样本造出更多变化，"
    "本质上是在给模型加入先验，也是一种正则化。",

    "还有一点是数据的划分。训练集、验证集、测试集必须严格分开，"
    "验证集用来调超参数，测试集只在最后用一次。如果反复用测试集调参，"
    "那测试集就变成了验证集，报出来的指标是虚高的，这个错误在比赛里非常常见。",

    "超参数的调节我们称为 hyperparameter tuning。常见的超参数包括学习率、"
    "批大小、网络深度、正则化系数等等。实践中最有效的往往是学习率调度："
    "前期用大一点的学习率快速下降，后期变小做精细调整。",

    "优化器也在不断演进。除了最基础的随机梯度下降，还有带动量的版本，"
    "它把历史梯度累积起来，能抑制震荡、加速收敛。再往后有自适应学习率的方法，"
    "比如 adam，它给每个参数单独维护学习率，收敛快，但泛化有时不如调好的 sgd。",

    "到这里，卷积神经网络这条线基本讲完了。我们最后把它和全连接网络对比一下。"
    "全连接层参数量随输入尺寸平方增长，而卷积层靠参数共享和局部连接，"
    "参数量与图像尺寸无关，这就是卷积在图像任务上成功的关键原因。",

    "顺便说一个实践中的常见现象，梯度消失和梯度爆炸。"
    "在很深的网络里，反向传播是连乘的，如果每一层的梯度都小于一，"
    "传到底层就趋近于零了，底层参数几乎学不动；反过来如果都大于一，"
    "就会指数级放大，训练直接发散。前面讲的残差连接和批归一化，"
    "很大程度上就是为了缓解这个问题。",

    "和它相关的还有权重的初始化。如果初始化把方差设得太大或者太小，"
    "都会放大上面说的连乘效应。常见的做法是保持每一层输出的方差大致不变，"
    "这样信号和梯度都能稳定地往前传、往回传，训练才容易起来。",

    "再讲一个容易混淆的点，推理和训练的区别。训练时需要保存中间激活值，"
    "因为反向传播要用；推理时只需要前向计算，内存占用小得多。"
    "所以同一份模型，推理阶段可以启用算子融合和量化，把速度提上去，"
    "这在部署的时候很重要。",

    "最后强调一下评估的严谨性。所有报出来的数字都要说清楚是在哪个数据集、"
    "什么划分下得到的，也要说明是否做了多次随机种子取平均。"
    "只报一次最好结果，或者换一套划分再报一次，都不是负责任的做法。"
    "科研里这种问题很致命，希望大家从这门课开始就养成习惯。",

    "下节课我们会进入序列建模，讲循环网络和注意力机制，"
    "也就是 attention。注意力解决了长距离依赖的问题，"
    "也是现在大模型的基础。今天的内容比较多，请大家课后把反向传播的推导自己写一遍，"
    "不要只看不动手，这是唯一能真正掌握的方法。谢谢大家。",
]

# 备用语音。合成失败时按顺序回退 —— Microsoft 的语音服务偶尔会对单个 voice
# 返回 403/超时，整批素材因为一个 voice 挂掉太浪费
FALLBACK_VOICES = ["zh-CN-YunxiNeural", "zh-CN-XiaoxiaoNeural", "zh-CN-YunjianNeural"]


def require_deps():
    try:
        import edge_tts  # noqa: F401
        import soundfile  # noqa: F401
    except ImportError as exc:
        sys.stderr.write(
            f"缺少依赖：{exc.name}\n"
            "本机项目 venv 里没有 edge-tts，需要单独装一份（不要装进项目 venv，避免污染打包依赖）：\n"
            '  <python> -m venv D:/tts-venv\n'
            '  D:/tts-venv/Scripts/python.exe -m pip install edge-tts soundfile numpy\n'
            "然后用那个解释器运行本脚本。\n"
        )
        raise SystemExit(2)


async def synth(text: str, voice: str, attempts: int = 3) -> bytes:
    """合成一段文本为 mp3 字节；带重试（网络抖动很常见）"""
    import edge_tts

    last: Exception | None = None
    for i in range(attempts):
        try:
            buf = bytearray()
            async for chunk in edge_tts.Communicate(text, voice).stream():
                if chunk["type"] == "audio":
                    buf.extend(chunk["data"])
            if buf:
                return bytes(buf)
            last = RuntimeError("返回空音频")
        except Exception as exc:  # noqa: BLE001 - 网络层什么都可能抛
            last = exc
        await asyncio.sleep(0.8 * (i + 1))
    raise RuntimeError(f"合成失败（{attempts} 次重试后）: {last}")


def decode_mp3(data: bytes) -> np.ndarray:
    """mp3 → float32 单声道（libsndfile 1.1+ 直接支持 mp3，不必依赖 ffmpeg）"""
    import soundfile as sf

    audio, sr = sf.read(io.BytesIO(data), dtype="float32", always_2d=True)
    mono = audio.mean(axis=1)
    if sr != TTS_RATE:
        raise SystemExit(f"TTS 采样率异常：期望 {TTS_RATE}，实际 {sr}")
    return mono


def resample_to_16k(x: np.ndarray, src_rate: int) -> np.ndarray:
    """
    线性插值重采样到 16kHz

    刻意不引 scipy / librosa：这里是 24k→16k 的整数比降采样，线性插值对
    Whisper 的影响远小于 VAD 分段误差，没必要为它加依赖。
    """
    if src_rate == SAMPLE_RATE:
        return x
    n_out = int(round(len(x) * SAMPLE_RATE / src_rate))
    if n_out <= 0:
        return np.zeros(0, dtype=np.float32)
    src_idx = np.arange(n_out, dtype=np.float64) * (src_rate / SAMPLE_RATE)
    i0 = np.floor(src_idx).astype(np.int64)
    i1 = np.minimum(i0 + 1, len(x) - 1)
    frac = (src_idx - i0).astype(np.float32)
    return (x[i0] * (1.0 - frac) + x[i1] * frac).astype(np.float32)


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(round(seconds * SAMPLE_RATE)), dtype=np.float32)


def rms_dbfs(x: np.ndarray) -> float:
    if x.size == 0:
        return -120.0
    rms = float(np.sqrt(np.mean(np.square(x.astype(np.float64)))))
    return round(20.0 * math.log10(max(rms, 1e-9)), 1)


def estimate_snr_db(x: np.ndarray) -> float | None:
    """
    粗估信噪比：把逐帧能量最低的 10% 当作底噪，全段能量当作信号

    只用于给 metadata 一个可比的数字。TTS 合成音频的底噪几乎为零，
    这个值会非常高 —— 恰恰是代理集「比真实课堂容易」的直接证据，必须如实记录。
    """
    frame = int(0.025 * SAMPLE_RATE)
    if x.size < frame * 8:
        return None
    n = x.size // frame
    frames = x[: n * frame].reshape(n, frame)
    energies = np.sqrt(np.mean(np.square(frames.astype(np.float64)), axis=1))
    energies = energies[energies > 1e-9]
    if energies.size < 8:
        return None
    noise = float(np.percentile(energies, 10))
    signal = float(np.percentile(energies, 90))
    if noise <= 0:
        return None
    return round(20.0 * math.log10(signal / noise), 1)


def clean_text(text: str) -> str:
    """逐字稿用的最终文本：合并换行、压缩空白（标点保留，评估时会归一化掉）"""
    return re.sub(r"\s+", "", text)


def main() -> int:
    parser = argparse.ArgumentParser(description="生成中文课堂音频代理测试集")
    parser.add_argument("--out", default="testset/chinese_lecture", help="输出目录")
    parser.add_argument("--voice", default=VOICE)
    parser.add_argument("--gap", type=float, default=PARAGRAPH_GAP_S, help="段落间隔秒")
    args = parser.parse_args()

    require_deps()

    out_dir = Path(args.out).resolve()
    long_dir = out_dir / "long"
    long_dir.mkdir(parents=True, exist_ok=True)

    voices = [args.voice] + [v for v in FALLBACK_VOICES if v != args.voice]

    async def build() -> tuple[np.ndarray, str, str]:
        for voice in voices:
            try:
                print(f"[tts] 使用语音 {voice}，共 {len(LECTURE)} 段")
                pieces: list[np.ndarray] = []
                for idx, para in enumerate(LECTURE, 1):
                    # 逐段合成：整段长文本一次性请求容易超时，且失败就要从头再来
                    mp3 = await synth(para, voice)
                    audio = resample_to_16k(decode_mp3(mp3), TTS_RATE)
                    pieces.append(audio)
                    pieces.append(silence(args.gap))
                    print(
                        f"  [{idx:>2}/{len(LECTURE)}] {len(audio)/SAMPLE_RATE:6.1f}s "
                        f"（累计 {sum(len(p) for p in pieces)/SAMPLE_RATE/60:4.1f} min）"
                    )
                return np.concatenate(pieces), voice, ""
            except Exception as exc:  # noqa: BLE001
                print(f"[tts] {voice} 失败：{exc}")
                if voice is voices[-1]:
                    return np.zeros(0, dtype=np.float32), voice, str(exc)
        return np.zeros(0, dtype=np.float32), voices[-1], "未知"

    audio, used_voice, err = asyncio.run(build())
    if audio.size == 0:
        print(f"合成失败，未产出文件：{err}")
        return 1

    audio = np.clip(audio, -1.0, 1.0)
    wav_path = long_dir / "long_zhlecture_cnn.wav"
    ref_path = long_dir / "long_zhlecture_cnn.txt"

    import soundfile as sf

    sf.write(wav_path, audio, SAMPLE_RATE, subtype="PCM_16")

    reference = clean_text("".join(LECTURE))
    ref_path.write_text(reference, encoding="utf-8")

    duration = len(audio) / SAMPLE_RATE
    meta = {
        "kind": "PROXY",  # 关键标记：这不是真实课堂录音
        "warning": (
            "代理集：神经 TTS 合成，无背景噪声、无口音、无口误、无混响。"
            "只能用于回答「长术语表是否触发重复循环」这类与录音质量无关的问题；"
            "WER 绝对值显著偏乐观，不得作为中文场景的最终结论。"
        ),
        "source": "Microsoft Edge 神经 TTS（edge-tts）",
        "voice": used_voice,
        "textSource": "scripts/make_chinese_testset.py 内置讲稿（卷积神经网络基础）",
        "sampleRate": SAMPLE_RATE,
        "channels": 1,
        "subtype": "PCM_16",
        "durationSeconds": round(duration, 2),
        "durationMinutes": round(duration / 60, 2),
        "paragraphs": len(LECTURE),
        "referenceChars": len(reference),
        "rmsDbfs": rms_dbfs(audio),
        "estimatedSnrDb": estimate_snr_db(audio),
        "peakDbfs": round(20.0 * math.log10(max(float(np.max(np.abs(audio))), 1e-9)), 1),
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "files": {"audio": str(wav_path.name), "reference": str(ref_path.name)},
    }
    (out_dir / "metadata.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(f"\n音频   : {wav_path}")
    print(f"逐字稿 : {ref_path}（{len(reference)} 字）")
    print(f"元数据 : {out_dir / 'metadata.json'}")
    print(
        f"时长   : {duration/60:.2f} min｜RMS {meta['rmsDbfs']} dBFS｜"
        f"峰值 {meta['peakDbfs']} dBFS｜估算 SNR {meta['estimatedSnrDb']} dB"
    )
    print(
        "\n⚠ 这是代理集（TTS 合成），WER 会明显偏乐观。"
        "真实课堂素材到位后请放进同一目录并重新跑 A/B，报告里不要混用两者结论。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
