"""Real-time microphone transcription to Deepgram Nova-3 (multilingual).

Requirements:
	pip install sounddevice websockets

Environment:
	DEEPGRAM_API_KEY=<your_deepgram_api_key>
	DEEPGRAM_INPUT_DEVICE=<optional input device index or exact device name>
	DEEPGRAM_SILENCE_RMS=<optional silence threshold, default 120>
	TRANSCRIPT_BRIDGE_PATH=<optional json path, default live_transcript.json>

Usage:
	python transcribe.py
	python transcribe.py --list-devices
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import sys
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import numpy as np
import sounddevice as sd
import websockets
from dotenv import load_dotenv
from websockets.exceptions import ConnectionClosed, ConnectionClosedOK, WebSocketException


load_dotenv()


def _as_bool(value: str | None, default: bool) -> bool:
	if value is None:
		return default
	return value.strip().lower() in {"1", "true", "yes", "on"}


def _normalize_text(value: str) -> str:
	return " ".join(value.strip().lower().split())


def _list_input_devices() -> None:
	devices = sd.query_devices()
	default_in = sd.default.device[0] if isinstance(sd.default.device, tuple | list) else None
	print("[system] Available input devices:")
	for idx, device in enumerate(devices):
		max_in = int(device.get("max_input_channels", 0))
		if max_in <= 0:
			continue
		default_tag = " (default)" if default_in == idx else ""
		print(
			f"  - {idx}: {device.get('name', 'unknown')}"
			f" | in={max_in} | sr={device.get('default_samplerate', 'n/a')}"
			f"{default_tag}"
		)


@dataclass
class DoctorResult:
	index: int
	name: str
	avg_rms: float
	p95_rms: float
	speech_ratio: float
	frames: int
	error: str = ""


def _resolve_device_index(value: Any) -> int | None:
	devices = sd.query_devices()
	if value is None:
		default_in = sd.default.device[0] if isinstance(sd.default.device, tuple | list) else None
		return int(default_in) if isinstance(default_in, int) else None

	if isinstance(value, int):
		return value

	needle = str(value).strip().lower()
	if not needle:
		return None

	for idx, device in enumerate(devices):
		name = str(device.get("name", "")).lower()
		if needle == name or needle in name:
			if int(device.get("max_input_channels", 0)) > 0:
				return idx
	return None


def _upsert_env_key(env_path: Path, key: str, value: str) -> None:
	lines: list[str] = []
	if env_path.exists():
		lines = env_path.read_text(encoding="utf-8").splitlines()

	prefix = f"{key}="
	replaced = False
	for idx, line in enumerate(lines):
		if line.strip().startswith(prefix):
			lines[idx] = f"{prefix}{value}"
			replaced = True
			break

	if not replaced:
		lines.append(f"{prefix}{value}")

	env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _maybe_apply_doctor_suggestion(suggested_index: int, apply_env: bool, prompt_env: bool) -> None:
	env_path = Path(".env")
	if apply_env:
		_upsert_env_key(env_path, "DEEPGRAM_INPUT_DEVICE", str(suggested_index))
		print(f"[doctor] Applied: DEEPGRAM_INPUT_DEVICE={suggested_index} in {env_path}")
		return

	if not prompt_env or not sys.stdin.isatty():
		return

	try:
		choice = input("[doctor] Update .env with this device now? [y/N]: ").strip().lower()
	except EOFError:
		return

	if choice in {"y", "yes"}:
		_upsert_env_key(env_path, "DEEPGRAM_INPUT_DEVICE", str(suggested_index))
		print(f"[doctor] Applied: DEEPGRAM_INPUT_DEVICE={suggested_index} in {env_path}")


def _capture_rms_for_device(index: int, seconds: float, silence_rms: int) -> DoctorResult:
	device = sd.query_devices(index)
	name = str(device.get("name", "unknown"))
	rms_values: list[int] = []
	requested_sr = int(os.getenv("DEEPGRAM_SAMPLE_RATE", "16000"))
	default_sr = int(float(device.get("default_samplerate", requested_sr) or requested_sr))

	def callback(indata, frames, time_info, status) -> None:
		del frames, time_info, status
		chunk = bytes(indata)
		if not chunk:
			return
		samples = np.frombuffer(chunk, dtype=np.int16)
		if samples.size == 0:
			return
		rms = int(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))
		rms_values.append(rms)

	last_error = ""
	for sr in [requested_sr, default_sr]:
		try:
			with sd.RawInputStream(
				samplerate=sr,
				channels=1,
				dtype="int16",
				blocksize=max(400, int(sr / 10)),
				device=index,
				callback=callback,
			):
				deadline = time.monotonic() + seconds
				while time.monotonic() < deadline:
					time.sleep(0.05)
			break
		except Exception as exc:
			last_error = str(exc)
			continue

	if not rms_values and last_error:
		return DoctorResult(
			index=index,
			name=name,
			avg_rms=0.0,
			p95_rms=0.0,
			speech_ratio=0.0,
			frames=0,
			error=last_error,
		)

	if not rms_values:
		return DoctorResult(index=index, name=name, avg_rms=0.0, p95_rms=0.0, speech_ratio=0.0, frames=0)

	arr = np.array(rms_values, dtype=np.float64)
	avg_rms = float(np.mean(arr))
	p95_rms = float(np.percentile(arr, 95))
	speech_ratio = float(np.mean(arr >= silence_rms))
	return DoctorResult(
		index=index,
		name=name,
		avg_rms=avg_rms,
		p95_rms=p95_rms,
		speech_ratio=speech_ratio,
		frames=len(rms_values),
	)


def _doctor_passed(result: DoctorResult, silence_rms: int) -> bool:
	if result.frames < 10:
		return False
	if result.p95_rms < silence_rms * 1.6:
		return False
	if result.speech_ratio < 0.15:
		return False
	return True


def _run_doctor(seconds: int, silence_rms: int, apply_env: bool, prompt_env: bool) -> None:
	print("[doctor] Microphone preflight started.")
	print(f"[doctor] Speak naturally for ~{seconds} seconds when prompted.")
	print(f"[doctor] Silence threshold RMS: {silence_rms}")

	configured_value = _resolve_input_device()
	configured_index = _resolve_device_index(configured_value)
	if configured_index is not None:
		configured_name = sd.query_devices(configured_index).get("name", "unknown")
		print(f"[doctor] Configured DEEPGRAM_INPUT_DEVICE -> {configured_index}: {configured_name}")
	else:
		print("[doctor] DEEPGRAM_INPUT_DEVICE is not set or could not be resolved. Using default input for primary test.")

	target_index = configured_index if configured_index is not None else _resolve_device_index(None)
	if target_index is None:
		print("[doctor] FAIL: Could not resolve any input device.")
		_list_input_devices()
		return

	print(f"[doctor] Testing primary device {target_index} for {seconds}s...")
	primary = _capture_rms_for_device(target_index, float(seconds), silence_rms)
	if primary.error:
		print(f"[doctor] FAIL: Primary device error -> {primary.error}")
	else:
		status = "PASS" if _doctor_passed(primary, silence_rms) else "FAIL"
		print(
			f"[doctor] Primary {status} | avg_rms={primary.avg_rms:.1f} | "
			f"p95_rms={primary.p95_rms:.1f} | speech_ratio={primary.speech_ratio:.2f} | frames={primary.frames}"
		)

	devices = sd.query_devices()
	candidates = [
		idx
		for idx, dev in enumerate(devices)
		if int(dev.get("max_input_channels", 0)) > 0
	]

	results: list[DoctorResult] = []
	quick_seconds = max(1.0, min(2.0, seconds / 3))
	print(f"[doctor] Running quick scan across {len(candidates)} input devices (~{quick_seconds:.1f}s each)...")
	for idx in candidates:
		result = _capture_rms_for_device(idx, quick_seconds, silence_rms)
		results.append(result)

	usable = [r for r in results if not r.error and r.frames > 0]
	if not usable:
		print("[doctor] No usable input device detected during quick scan.")
		_list_input_devices()
		return

	def score(r: DoctorResult) -> float:
		return (r.p95_rms * 0.7) + (r.avg_rms * 0.2) + (r.speech_ratio * 100.0 * 0.1)

	best = max(usable, key=score)
	if best.p95_rms < max(20.0, silence_rms * 0.5):
		print("[doctor] No clear speech activity detected during scan. Speak continuously and re-run --doctor.")
		_list_input_devices()
		return

	print(
		f"[doctor] Suggested device: {best.index} ({best.name}) | "
		f"avg_rms={best.avg_rms:.1f} | p95_rms={best.p95_rms:.1f} | speech_ratio={best.speech_ratio:.2f}"
	)

	if best.index != target_index:
		print(f"[doctor] Recommendation: set DEEPGRAM_INPUT_DEVICE={best.index} in .env")
		_maybe_apply_doctor_suggestion(best.index, apply_env=apply_env, prompt_env=prompt_env)
	else:
		print("[doctor] Current device is already the strongest candidate on this machine.")

	print("[doctor] Top device candidates:")
	for r in sorted(usable, key=score, reverse=True)[:5]:
		print(
			f"  - {r.index}: {r.name} | avg_rms={r.avg_rms:.1f} | "
			f"p95_rms={r.p95_rms:.1f} | speech_ratio={r.speech_ratio:.2f}"
		)


def _resolve_input_device() -> Any:
	device_env = os.getenv("DEEPGRAM_INPUT_DEVICE")
	if not device_env:
		return None
	try:
		return int(device_env)
	except ValueError:
		return device_env


class TranscriptBridge:
	def __init__(self, path: str) -> None:
		self.path = Path(path)
		self.path.parent.mkdir(parents=True, exist_ok=True)
		self.max_final_lines = int(os.getenv("TRANSCRIPT_MAX_LINES", "2000"))
		self.merge_window_seconds = float(os.getenv("TRANSCRIPT_MERGE_WINDOW_SECONDS", "2.0"))
		self.write_retries = int(os.getenv("TRANSCRIPT_WRITE_RETRIES", "3"))
		self.write_retry_delay = float(os.getenv("TRANSCRIPT_WRITE_RETRY_DELAY", "0.05"))
		self._last_final_at = 0.0
		self.state: dict[str, Any] = {
			"connected": False,
			"device": "",
			"interim": "",
			"final_lines": [],
			"updated_at": time.time(),
		}
		self._flush()

	def set_device(self, value: str) -> None:
		self.state["device"] = value
		self.state["updated_at"] = time.time()
		self._flush()

	def set_connected(self, value: bool) -> None:
		self.state["connected"] = value
		self.state["updated_at"] = time.time()
		self._flush()

	def set_interim(self, value: str) -> None:
		self.state["interim"] = value
		self.state["updated_at"] = time.time()
		self._flush()

	def clear_interim(self) -> None:
		self.state["interim"] = ""
		self.state["updated_at"] = time.time()
		self._flush()

	def append_final(self, line: str) -> None:
		line = line.strip()
		if not line:
			return

		final_lines = self.state["final_lines"]
		now = time.monotonic()

		if final_lines:
			last_line = str(final_lines[-1])
			if _normalize_text(last_line) == _normalize_text(line):
				return
			if (
				now - self._last_final_at <= self.merge_window_seconds
				and last_line
				and last_line[-1] not in ".!?"
				and line
				and line[0].islower()
			):
				final_lines[-1] = f"{last_line} {line}"
			else:
				final_lines.append(line)
		else:
			final_lines.append(line)

		if len(final_lines) > self.max_final_lines:
			self.state["final_lines"] = final_lines[-self.max_final_lines :]

		self._last_final_at = now
		self.state["updated_at"] = time.time()
		self._flush()

	def _flush(self) -> None:
		tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
		payload = json.dumps(self.state, ensure_ascii=False, indent=2)
		last_error: Exception | None = None
		for attempt in range(self.write_retries):
			try:
				tmp_path.write_text(payload, encoding="utf-8")
				tmp_path.replace(self.path)
				return
			except OSError as exc:
				last_error = exc
				with suppress(OSError):
					tmp_path.unlink()
				time.sleep(self.write_retry_delay * (attempt + 1))

		# Non-fatal fallback: keep the session running even if Windows briefly locks the file.
		if last_error is not None:
			with suppress(OSError):
				self.path.write_text(payload, encoding="utf-8")


def _build_deepgram_ws_url() -> str:
	# Nova-3 supports multilingual mode via language=multi.
	model = os.getenv("DEEPGRAM_MODEL", "nova-3")
	language = os.getenv("DEEPGRAM_LANGUAGE", "multi")
	interim_results = _as_bool(os.getenv("DEEPGRAM_INTERIM_RESULTS"), True)
	sample_rate = int(os.getenv("DEEPGRAM_SAMPLE_RATE", "16000"))
	channels = int(os.getenv("DEEPGRAM_CHANNELS", "1"))

	query = [
		f"model={model}",
		f"language={language}",
		f"interim_results={'true' if interim_results else 'false'}",
		"smart_format=true",
		"punctuate=true",
		"encoding=linear16",
		f"sample_rate={sample_rate}",
		f"channels={channels}",
	]

	return "wss://api.deepgram.com/v1/listen?" + "&".join(query)


@dataclass
class AudioConfig:
	sample_rate: int = int(os.getenv("DEEPGRAM_SAMPLE_RATE", "16000"))
	channels: int = int(os.getenv("DEEPGRAM_CHANNELS", "1"))
	dtype: str = "int16"
	blocksize: int = 1600  # ~100ms at 16kHz


class DeepgramRealtimeTranscriber:
	def __init__(self, api_key: str, audio_cfg: Optional[AudioConfig] = None) -> None:
		self.api_key = api_key
		self.audio_cfg = audio_cfg or AudioConfig()
		self.stop_event = asyncio.Event()
		self.audio_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=100)
		self.stream: Optional[sd.InputStream] = None
		self.current_device_index: int | None = None
		self.current_device_name: str = ""
		self.last_audio_chunk_at: Optional[float] = None
		self.last_non_silent_at: Optional[float] = None
		self.last_transcript_at: Optional[float] = None
		self.last_final_normalized = ""
		self.silence_rms = int(os.getenv("DEEPGRAM_SILENCE_RMS", "120"))
		self.input_gain = max(1.0, float(os.getenv("DEEPGRAM_INPUT_GAIN", "1.0")))
		self.auto_fallback_device = _as_bool(os.getenv("DEEPGRAM_AUTO_FALLBACK_DEVICE"), True)
		self.auto_switch_on_device_loss = _as_bool(os.getenv("DEEPGRAM_AUTO_SWITCH_ON_DEVICE_LOSS"), True)
		self.demo_quiet = _as_bool(os.getenv("DEEPGRAM_DEMO_QUIET"), True)
		self.show_interim = _as_bool(os.getenv("DEEPGRAM_SHOW_INTERIM"), not self.demo_quiet)
		self.ws_open_timeout = float(os.getenv("DEEPGRAM_WS_OPEN_TIMEOUT", "45"))
		self.max_retry = int(os.getenv("DEEPGRAM_MAX_RETRY_SECONDS", "20"))
		self.device_loss_grace_seconds = float(os.getenv("DEEPGRAM_DEVICE_LOSS_GRACE_SECONDS", "0.75"))
		self.last_device_switch_at: float = 0.0
		self.no_frame_warn_seconds = float(os.getenv("DEEPGRAM_NO_FRAME_WARN_SECONDS", "8" if self.demo_quiet else "3"))
		self.silent_warn_seconds = float(os.getenv("DEEPGRAM_SILENT_WARN_SECONDS", "20" if self.demo_quiet else "10"))
		self.no_transcript_warn_seconds = float(os.getenv("DEEPGRAM_NO_TRANSCRIPT_WARN_SECONDS", "30" if self.demo_quiet else "15"))
		bridge_path = os.getenv("TRANSCRIPT_BRIDGE_PATH", "live_transcript.json")
		self.bridge = TranscriptBridge(bridge_path)

	def _input_device_candidates(self) -> list[int]:
		candidates: list[int] = []
		configured = _resolve_device_index(_resolve_input_device())
		default_in = _resolve_device_index(None)
		if configured is not None:
			candidates.append(configured)
		if default_in is not None and default_in not in candidates:
			candidates.append(default_in)
		devices = sd.query_devices()
		for idx, dev in enumerate(devices):
			if int(dev.get("max_input_channels", 0)) <= 0:
				continue
			if idx not in candidates:
				candidates.append(idx)
		return candidates

	def _device_is_available(self, index: int | None) -> bool:
		if index is None:
			return False
		try:
			devices = sd.query_devices()
		except Exception:
			return False
		return 0 <= index < len(devices) and int(devices[index].get("max_input_channels", 0)) > 0

	def _restart_microphone(self, loop: asyncio.AbstractEventLoop, device_override: Any = None) -> None:
		self._stop_microphone()
		self._start_microphone(loop, device_override=device_override)
		self.last_device_switch_at = time.monotonic()

	def _switch_to_default_input(self, loop: asyncio.AbstractEventLoop) -> bool:
		default_index = _resolve_device_index(None)
		if default_index is None:
			return False
		try:
			self._restart_microphone(loop, device_override=default_index)
			print(
				f"[system] Auto-switched to system default input device {default_index}: {self.current_device_name}",
				file=sys.stderr,
			)
			return True
		except Exception as exc:
			print(f"[warn] Failed to auto-switch to system default input: {exc}", file=sys.stderr)
			return False

	def _enqueue_audio(self, chunk: bytes) -> None:
		if self.stop_event.is_set():
			return
		if self.audio_queue.full():
			# Drop the oldest frame to keep latency bounded under network hiccups.
			try:
				_ = self.audio_queue.get_nowait()
			except asyncio.QueueEmpty:
				pass
		try:
			self.audio_queue.put_nowait(chunk)
		except asyncio.QueueFull:
			# A race can still happen between full check and put_nowait.
			pass

	def _start_microphone(self, loop: asyncio.AbstractEventLoop, device_override: Any = None) -> None:
		cfg = self.audio_cfg
		device = device_override if device_override is not None else _resolve_input_device()

		try:
			selected_device = sd.query_devices(device=device, kind="input")
		except Exception:
			selected_device = sd.query_devices(kind="input")

		print(
			f"[system] Using input device: {selected_device.get('name', 'unknown')} "
			f"(default samplerate: {selected_device.get('default_samplerate', 'n/a')})"
		)
		self.current_device_index = _resolve_device_index(device)
		self.current_device_name = str(selected_device.get("name", "unknown"))
		self.last_audio_chunk_at = None
		self.last_non_silent_at = None
		self.bridge.set_device(str(selected_device.get("name", "unknown")))

		def callback(indata, frames, time_info, status) -> None:
			del frames, time_info
			if status:
				print(f"[audio] {status}", file=sys.stderr)
			raw_chunk = bytes(indata)
			chunk = raw_chunk
			if self.input_gain > 1.0 and raw_chunk:
				samples = np.frombuffer(raw_chunk, dtype=np.int16)
				if samples.size > 0:
					scaled = np.clip(samples.astype(np.float64) * self.input_gain, -32768, 32767).astype(np.int16)
					chunk = scaled.tobytes()
			now = time.monotonic()
			self.last_audio_chunk_at = now
			# audioop was removed in Python 3.13+, so compute RMS from int16 PCM bytes.
			rms = 0
			if chunk:
				samples = np.frombuffer(chunk, dtype=np.int16)
				if samples.size > 0:
					rms = int(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))
			if rms >= self.silence_rms:
				self.last_non_silent_at = now
			try:
				loop.call_soon_threadsafe(self._enqueue_audio, chunk)
			except RuntimeError:
				# Loop already closed during shutdown.
				pass

		requested_sr = cfg.sample_rate
		default_sr = int(float(selected_device.get("default_samplerate", requested_sr) or requested_sr))
		max_input_channels = int(selected_device.get("max_input_channels", 1) or 1)
		preferred_channels = int(cfg.channels or 1)
		candidate_channels: list[int] = []
		for ch in [preferred_channels, 1, 2, max_input_channels]:
			if ch < 1 or ch > max_input_channels:
				continue
			if ch not in candidate_channels:
				candidate_channels.append(ch)
		if not candidate_channels:
			candidate_channels = [1]
		stream = None
		last_error = ""
		attempt_errors: list[str] = []
		for sr in [requested_sr, default_sr]:
			for ch in candidate_channels:
				for stream_kind, stream_cls in [("raw", sd.RawInputStream), ("standard", sd.InputStream)]:
					try:
						stream = stream_cls(
							samplerate=sr,
							channels=ch,
							dtype=cfg.dtype,
							blocksize=max(400, int(sr / 10)),
							device=device,
							callback=callback,
						)
						cfg.sample_rate = sr
						cfg.channels = ch
						os.environ["DEEPGRAM_SAMPLE_RATE"] = str(sr)
						os.environ["DEEPGRAM_CHANNELS"] = str(ch)
						if sr != requested_sr:
							print(f"[warn] Device rejected {requested_sr} Hz. Falling back to {sr} Hz.")
						if ch != preferred_channels:
							print(f"[warn] Device rejected {preferred_channels} channel(s). Falling back to {ch} channel(s).")
						if stream_kind != "raw":
							print("[warn] Raw audio mode unsupported for this device/backend. Using standard stream mode.")
						break
					except Exception as exc:
						last_error = str(exc)
						attempt_errors.append(f"sr={sr},ch={ch},{stream_kind}: {exc}")
						stream = None
				if stream is not None:
					break
			if stream is not None:
				break

		if stream is None:
			detail = " | ".join(attempt_errors[-4:])
			raise RuntimeError(f"Could not start microphone stream: {last_error}. Recent attempts: {detail}")
		stream.start()
		self.stream = stream
		print("[system] Microphone started.")

	async def _wait_for_audio_frames(self, timeout_seconds: float) -> bool:
		deadline = time.monotonic() + timeout_seconds
		while time.monotonic() < deadline and not self.stop_event.is_set():
			if self.last_audio_chunk_at is not None:
				return True
			await asyncio.sleep(0.1)
		return self.last_audio_chunk_at is not None

	async def _ensure_audio_input(self, loop: asyncio.AbstractEventLoop) -> None:
		has_frames = await self._wait_for_audio_frames(timeout_seconds=1.5)
		if has_frames or not self.auto_fallback_device:
			return

		print(
			"[warn] Current device captured no audio frames. Trying fallback input devices...",
			file=sys.stderr,
		)

		for candidate in self._input_device_candidates():
			if self.current_device_index is not None and candidate == self.current_device_index:
				continue
			try:
				self._restart_microphone(loop, device_override=candidate)
			except Exception:
				continue

			has_frames = await self._wait_for_audio_frames(timeout_seconds=2.5)
			if has_frames:
				print(
					f"[system] Switched to fallback device {candidate}: {self.current_device_name}",
					file=sys.stderr,
				)
				return

		raise RuntimeError(
			"No audio frames captured from any input device. Run 'python transcribe.py --doctor --doctor-apply'."
		)

	async def _monitor_audio_health(self) -> None:
		while not self.stop_event.is_set():
			await asyncio.sleep(self.device_loss_grace_seconds)
			now = time.monotonic()
			device_missing = not self._device_is_available(self.current_device_index)
			if device_missing:
				print(
					"[warn] Active microphone device disappeared. Attempting immediate switch...",
					file=sys.stderr,
				)
				if self.auto_switch_on_device_loss and self.auto_fallback_device:
					if self._switch_to_default_input(asyncio.get_running_loop()):
						continue
					for candidate in self._input_device_candidates():
						if self.current_device_index is not None and candidate == self.current_device_index:
							continue
						try:
							self._restart_microphone(asyncio.get_running_loop(), device_override=candidate)
							print(
								f"[system] Auto-switched to fallback device {candidate}: {self.current_device_name}",
								file=sys.stderr,
							)
							break
						except Exception:
							continue
				continue
			if self.last_audio_chunk_at is None or now - self.last_audio_chunk_at > self.no_frame_warn_seconds:
				if not self.demo_quiet:
					print(
						"[warn] No audio frames captured from microphone. "
						"Check mic permissions or set DEEPGRAM_INPUT_DEVICE.",
						file=sys.stderr,
					)
				if (
					self.auto_switch_on_device_loss
					and self.auto_fallback_device
					and now - self.last_device_switch_at > 10
				):
					if self._switch_to_default_input(asyncio.get_running_loop()):
						continue
					for candidate in self._input_device_candidates():
						if self.current_device_index is not None and candidate == self.current_device_index:
							continue
						try:
							self._restart_microphone(asyncio.get_running_loop(), device_override=candidate)
							print(
								f"[system] Auto-switched to fallback device {candidate}: {self.current_device_name}",
								file=sys.stderr,
							)
							break
						except Exception:
							continue
				continue
			if not self.demo_quiet:
				if self.last_non_silent_at is None or now - self.last_non_silent_at > self.silent_warn_seconds:
					print(
						"[warn] Mic input looks silent. Speak closer or try another input device with DEEPGRAM_INPUT_DEVICE.",
						file=sys.stderr,
					)
				if self.last_non_silent_at is not None and now - self.last_non_silent_at <= self.silent_warn_seconds and (
					self.last_transcript_at is None or now - self.last_transcript_at > self.no_transcript_warn_seconds
				):
					print(
						"[warn] Audio is flowing but no transcript yet. Check DEEPGRAM_API_KEY/model/language settings.",
						file=sys.stderr,
					)

	def _stop_microphone(self) -> None:
		if self.stream is None:
			return
		try:
			self.stream.stop()
			self.stream.close()
		finally:
			self.stream = None
		print("[system] Microphone stopped.")

	async def _send_keepalive(self, ws: Any) -> None:
		while not self.stop_event.is_set():
			await asyncio.sleep(5)
			try:
				await ws.send(json.dumps({"type": "KeepAlive"}))
			except ConnectionClosed:
				# Socket closed during normal shutdown.
				break

	async def _send_audio(self, ws: Any) -> None:
		while not self.stop_event.is_set():
			chunk = await self.audio_queue.get()
			if not chunk:
				continue
			try:
				await ws.send(chunk)
			except ConnectionClosed:
				# Socket closed during normal shutdown.
				break

	async def _receive_transcripts(self, ws: Any) -> None:
		async for raw_message in ws:
			if not isinstance(raw_message, str):
				continue

			try:
				message = json.loads(raw_message)
			except json.JSONDecodeError:
				continue

			if message.get("type") != "Results":
				continue

			alternatives = message.get("channel", {}).get("alternatives", [])
			if not alternatives:
				continue

			transcript = alternatives[0].get("transcript", "").strip()
			if not transcript:
				continue

			is_final = message.get("is_final", False)
			self.last_transcript_at = time.monotonic()
			tag = "FINAL" if is_final else "INTERIM"
			if is_final:
				normalized = _normalize_text(transcript)
				if normalized == self.last_final_normalized:
					continue
				self.last_final_normalized = normalized
				self.bridge.append_final(transcript)
				self.bridge.clear_interim()
			else:
				self.bridge.set_interim(transcript)
			if is_final or self.show_interim:
				print(f"[{tag}] {transcript}")

	async def _run_session(self) -> None:
		ws_url = _build_deepgram_ws_url()
		headers = {"Authorization": f"Token {self.api_key}"}
		async with websockets.connect(
			ws_url,
			additional_headers=headers,
			open_timeout=self.ws_open_timeout,
			close_timeout=10,
			ping_interval=20,
			ping_timeout=20,
			max_size=None,
		) as ws:
			print("[system] Connected to Deepgram websocket.")
			self.bridge.set_connected(True)

			sender = asyncio.create_task(self._send_audio(ws))
			receiver = asyncio.create_task(self._receive_transcripts(ws))
			keepalive = asyncio.create_task(self._send_keepalive(ws))
			monitor = asyncio.create_task(self._monitor_audio_health())

			done, pending = await asyncio.wait(
				{sender, receiver, keepalive, monitor},
				return_when=asyncio.FIRST_EXCEPTION,
			)

			for task in pending:
				task.cancel()

			await asyncio.gather(*pending, return_exceptions=True)

			for task in done:
				exc = task.exception()
				if exc is not None:
					if isinstance(exc, ConnectionClosedOK):
						continue
					raise exc

			try:
				await ws.send(json.dumps({"type": "CloseStream"}))
			except Exception:
				pass
			finally:
				self.bridge.set_connected(False)

	async def run(self) -> None:
		loop = asyncio.get_running_loop()
		try:
			self._start_microphone(loop)
		except Exception as exc:
			if not self.auto_fallback_device:
				raise
			print(
				f"[warn] Failed to open configured input device: {exc}. Trying alternate devices...",
				file=sys.stderr,
			)
			opened = False
			last_error = str(exc)
			for candidate in self._input_device_candidates():
				try:
					self._start_microphone(loop, device_override=candidate)
					opened = True
					print(
						f"[system] Opened fallback input device {candidate}: {self.current_device_name}",
						file=sys.stderr,
					)
					break
				except Exception as open_exc:
					last_error = str(open_exc)
					continue

			if not opened:
				raise RuntimeError(
					"Could not open any microphone input device. "
					"Run 'python transcribe.py --doctor --doctor-apply'. "
					f"Last error: {last_error}"
				)
		await self._ensure_audio_input(loop)

		retry_seconds = 1
		max_retry = self.max_retry

		print("[system] Listening... Press Ctrl+C to stop.")

		try:
			while not self.stop_event.is_set():
				try:
					await self._run_session()
					retry_seconds = 1
				except (ConnectionClosed, WebSocketException, OSError, asyncio.TimeoutError) as exc:
					if self.stop_event.is_set():
						break
					if isinstance(exc, socket.gaierror):
						print(
							"[warn] DNS lookup failed for api.deepgram.com. Check internet/VPN/proxy and retry.",
							file=sys.stderr,
						)
					elif isinstance(exc, asyncio.TimeoutError):
						print(
							"[warn] WebSocket opening handshake timed out. Network is slow or blocked; retrying...",
							file=sys.stderr,
						)
					print(
						f"[warn] Connection dropped: {exc}. "
						f"Reconnecting in {retry_seconds}s...",
						file=sys.stderr,
					)
					try:
						await asyncio.wait_for(self.stop_event.wait(), timeout=retry_seconds)
					except asyncio.TimeoutError:
						pass
					retry_seconds = min(retry_seconds * 2, max_retry)
				except Exception as exc:
					if self.stop_event.is_set():
						break
					print(f"[error] Unexpected failure: {exc}", file=sys.stderr)
					try:
						await asyncio.wait_for(self.stop_event.wait(), timeout=retry_seconds)
					except asyncio.TimeoutError:
						pass
					retry_seconds = min(retry_seconds * 2, max_retry)
		finally:
			self.stop()
			self.bridge.set_connected(False)
			self._stop_microphone()

	def stop(self) -> None:
		if self.stop_event.is_set():
			return
		self.stop_event.set()
		# Nudge blocked sender task.
		try:
			self.audio_queue.put_nowait(b"")
		except asyncio.QueueFull:
			pass


async def _main() -> None:
	parser = argparse.ArgumentParser(description="Real-time microphone transcription with Deepgram.")
	parser.add_argument("--list-devices", action="store_true", help="List available input devices and exit.")
	parser.add_argument("--doctor", action="store_true", help="Run microphone preflight check and suggest best input device.")
	parser.add_argument("--doctor-seconds", type=int, default=5, help="Capture duration for primary doctor test (default: 5).")
	parser.add_argument("--doctor-apply", action="store_true", help="Automatically write suggested DEEPGRAM_INPUT_DEVICE to .env.")
	parser.add_argument("--doctor-no-prompt", action="store_true", help="Do not prompt to update .env after doctor recommendation.")
	args = parser.parse_args()

	if args.list_devices:
		_list_input_devices()
		return

	if args.doctor:
		silence_rms = int(os.getenv("DEEPGRAM_SILENCE_RMS", "120"))
		_run_doctor(
			seconds=max(2, args.doctor_seconds),
			silence_rms=silence_rms,
			apply_env=args.doctor_apply,
			prompt_env=not args.doctor_no_prompt,
		)
		return

	api_key = os.getenv("DEEPGRAM_API_KEY")
	if not api_key:
		raise RuntimeError("Set DEEPGRAM_API_KEY environment variable before running.")

	transcriber = DeepgramRealtimeTranscriber(api_key=api_key)
	try:
		await transcriber.run()
	except KeyboardInterrupt:
		print("\n[system] Stopping...")
		transcriber.stop()


if __name__ == "__main__":
	try:
		asyncio.run(_main())
	except KeyboardInterrupt:
		print("\n[system] Exited.")
