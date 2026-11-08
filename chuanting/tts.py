"""朗读：edge-tts（微软神经网络语音，免费、不用 Key）合成句子，按文本缓存。
连不上微软时退回系统自带的离线语音：macOS 用 say，Windows 用 SAPI（System.Speech），都直接输出 WAV。

导出听力包：用 miniaudio 把各段语音解码成 PCM（Mac、Windows 通用），中间插入“嘀”声和停顿，再用 lameenc 编成 MP3。
"""
from __future__ import annotations

import asyncio
import hashlib
import math
import struct
import os
import subprocess
import sys
from pathlib import Path

from .store import AUDIO_DIR

RATE = 24000  # edge-tts 输出 24kHz 单声道
SPEAKABLE = __import__("re").compile(r"[A-Za-z0-9\u4e00-\u9fff]")


class TTSError(RuntimeError):
    pass


def speakable(text: str) -> bool:
    """只有标点（挖空后剩下的“.”）时，微软朗读会返回空音频，离线朗读也转不出来"""
    return bool(SPEAKABLE.search(text or ""))
_sem = asyncio.Semaphore(6)
_bg_sem = asyncio.Semaphore(2)   # 后台预合成最多占 2 路
_bg_tasks: set = set()


def prefetch(texts: list[str], voice: str, rate: str) -> None:
    """在后台把这些句子先合成好（已缓存的跳过），之后点朗读就是秒开。自测时设 CHUANTING_NO_PREFETCH 关掉"""
    import os

    if os.environ.get("CHUANTING_NO_PREFETCH"):
        return
    async def one(t: str) -> None:
        async with _bg_sem:
            try:
                await synth(t, voice, rate)
            except Exception:
                pass

    async def run() -> None:
        await asyncio.gather(*(one(t) for t in texts if t.strip()))

    task = asyncio.get_running_loop().create_task(run())
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


def _cache_path(text: str, voice: str, rate: str, ext: str) -> Path:
    h = hashlib.sha1(f"{voice}|{rate}|{text}".encode()).hexdigest()[:20]
    return AUDIO_DIR / h[:2] / f"{h}.{ext}"


async def synth(text: str, voice: str, rate: str = "+0%") -> Path:
    """返回音频文件路径（mp3；离线兜底时是 wav）"""
    text = text.strip()
    mp3 = _cache_path(text, voice, rate, "mp3")
    if mp3.exists() and mp3.stat().st_size > 0:
        return mp3
    wav = _cache_path(text, "offline:" + voice, rate, "wav")
    if wav.exists() and wav.stat().st_size > 44:
        return wav
    mp3.parent.mkdir(parents=True, exist_ok=True)
    async with _sem:
        try:
            import edge_tts

            tmp = mp3.with_suffix(".part")
            await asyncio.wait_for(edge_tts.Communicate(text, voice, rate=rate).save(str(tmp)), timeout=30)
            if tmp.stat().st_size == 0:
                raise RuntimeError("empty audio")
            tmp.rename(mp3)
            return mp3
        except Exception:
            try:
                return await asyncio.to_thread(offline_tts, text, voice, wav)
            except Exception as e:
                raise TTSError("语音合成失败：连不上微软朗读，本机离线朗读也没成功") from e


# Windows：用 PowerShell 调系统自带的 System.Speech。文字和路径走环境变量，不拼进命令，避免引号和注入问题
_SAPI_PS = r"""
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$want = $env:CHUANTING_CULTURE
$v = $s.GetInstalledVoices() | Where-Object { $_.Enabled -and $_.VoiceInfo.Culture.Name -eq $want } | Select-Object -First 1
if (-not $v) { $v = $s.GetInstalledVoices() | Where-Object { $_.Enabled -and $_.VoiceInfo.Culture.Name.StartsWith($want.Substring(0, 2)) } | Select-Object -First 1 }
if ($v) { $s.SelectVoice($v.VoiceInfo.Name) }
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(24000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
$s.SetOutputToWaveFile($env:CHUANTING_OUT, $fmt)
$s.Speak($env:CHUANTING_TEXT)
$s.Dispose()
"""


def offline_tts(text: str, voice: str, out: Path) -> Path:
    """系统自带的离线语音，输出 24kHz 单声道 WAV（浏览器能直接播放，miniaudio 能解码）"""
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".part.wav")
    if sys.platform == "darwin":
        mac_voice = "Ting-Ting" if voice.startswith("zh") else ("Daniel" if voice.startswith("en-GB") else "Samantha")
        subprocess.run(["say", "-v", mac_voice, "--file-format=WAVE", f"--data-format=LEI16@{RATE}", "-o", str(tmp), text],
                       check=True, capture_output=True, timeout=60)
    elif sys.platform == "win32":
        culture = "zh-CN" if voice.startswith("zh") else ("en-GB" if voice.startswith("en-GB") else "en-US")
        env = {**os.environ, "CHUANTING_TEXT": text, "CHUANTING_OUT": str(tmp), "CHUANTING_CULTURE": culture}
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", _SAPI_PS],
                       check=True, capture_output=True, timeout=60, env=env,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    else:
        raise RuntimeError("这个系统没有可用的离线语音")
    if not tmp.exists() or tmp.stat().st_size <= 44:
        raise RuntimeError("离线语音没有生成音频")
    tmp.replace(out)
    return out


# ---------- PCM 拼接 ----------

def to_pcm(path: Path) -> bytes:
    """mp3 / wav → 24kHz 单声道 16 位 PCM（miniaudio，Mac、Windows 都能用）"""
    import miniaudio

    d = miniaudio.decode_file(str(path), output_format=miniaudio.SampleFormat.SIGNED16, nchannels=1, sample_rate=RATE)
    return d.samples.tobytes()


def silence(ms: int) -> bytes:
    return b"\x00\x00" * int(RATE * ms / 1000)


def beep(ms: int = 280, freq: float = 880.0) -> bytes:
    n = int(RATE * ms / 1000)
    fade = int(RATE * 0.02)
    out = bytearray()
    for i in range(n):
        env = min(1.0, i / fade, (n - i) / fade)
        out += struct.pack("<h", int(9000 * env * math.sin(2 * math.pi * freq * i / RATE)))
    return bytes(out)


def spell_text(pieces: list[str]) -> str:
    words = " ".join(pieces).split()
    return ". ".join(", ".join(ch.upper() for ch in w if ch.isalpha()) for w in words) + "."


async def card_audio(card: dict, cfg: dict, pause_ms: int) -> bytes:
    """一张卡的听力：挖空句（嘀）→ 停顿让你想 → 答案并拼读 → 完整句 → 中文意思。
    朗读方式是“读全”时，开头读完整句（不嘀），停顿后照样公布答案并拼读。"""
    voice, rate, zh = cfg["tts"]["voice"], cfg["tts"]["rate"], cfg["tts"]["zh_voice"]
    if card.get("kind") == "sentence":
        # 整句：读一遍 → 停顿让你在心里复述 → 慢速再读 → 中文翻译
        parts = [synth(card["sentence"], voice, rate), silence(pause_ms), synth(card["sentence"], voice, "-15%"), silence(400)]
        if card.get("meaning"):
            parts.append(synth(card["meaning"], zh, "+0%"))
        parts.append(silence(1500))
        paths = await asyncio.gather(*[p for p in parts if not isinstance(p, bytes)])
        it = iter(paths)
        pcm = bytearray()
        for p in parts:
            pcm += p if isinstance(p, bytes) else await asyncio.to_thread(to_pcm, next(it))
        return bytes(pcm)
    full = cfg.get("study", {}).get("read_mode") == "full"
    parts: list = []
    if full:
        parts.append(synth(card["sentence"], voice, rate))
    else:
        for seg in card["segments"]:
            if "blank" in seg:
                parts.append(beep())
            elif speakable(seg["t"]):
                parts.append(synth(seg["t"], voice, rate))
    parts.append(silence(pause_ms))
    answer = " ".join(card["answer"])
    parts += [synth(answer, voice, "-10%"), silence(300)]
    if len(answer.replace(" ", "")) <= 16:
        parts += [synth(spell_text(card["answer"]), voice, "-15%"), silence(400)]
    parts += [synth(card["sentence"], voice, rate), silence(400)]
    if card.get("meaning"):
        parts.append(synth(f"{card['spoken_key']}，{card['meaning']}", zh, "+0%"))
    parts.append(silence(1500))
    paths = await asyncio.gather(*[p for p in parts if not isinstance(p, bytes)])
    it = iter(paths)
    pcm = bytearray()
    for p in parts:
        pcm += p if isinstance(p, bytes) else await asyncio.to_thread(to_pcm, next(it))
    return bytes(pcm)


async def build_pack(cards: list[dict], cfg: dict, out: Path, pause_ms: int = 3000) -> Path:
    import lameenc

    pcm = bytearray(silence(500))
    for card in cards:
        pcm += await card_audio(card, cfg, pause_ms)
    enc = lameenc.Encoder()
    enc.set_bit_rate(64)
    enc.set_in_sample_rate(RATE)
    enc.set_channels(1)
    enc.set_quality(2)
    data = enc.encode(bytes(pcm)) + enc.flush()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    return out
