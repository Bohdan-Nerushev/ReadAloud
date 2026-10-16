"""
Unit tests for PiperAudioGenerator.

All tests mock the Wyoming client and filesystem — no Docker or network required.
"""

import asyncio
import io
import os
import wave
from pathlib import Path
from typing import List, Optional
from unittest.mock import AsyncMock, MagicMock, Mock, patch, call

import pytest

from src.domain.exceptions import FatalGenerationException, PiperConnectionException
from src.domain.models import AudioChunk
from src.domain.piper_audio_generator import PiperAudioGenerator, PIPER_VOICE_MAPPING


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def output_dir(tmp_path: Path) -> str:
    return str(tmp_path)


@pytest.fixture
def generator() -> PiperAudioGenerator:
    gen = PiperAudioGenerator(host="127.0.0.1", port=10200)
    yield gen
    gen.close()


def make_chunk(number: int = 1, text: str = "Hello world") -> AudioChunk:
    return AudioChunk(chunk_number=number, text_content=text)


def make_valid_wav_bytes() -> bytes:
    """Creates a minimal valid WAV file in memory."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(22050)
        wf.writeframes(b"\x00\x00" * 22050)  # 1 second of silence
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Voice mapping tests
# ---------------------------------------------------------------------------

class TestVoiceMapping:
    def test_russian_male_voice(self):
        assert PIPER_VOICE_MAPPING["ru"]["male"] == "ru_RU-dmitri-medium"

    def test_russian_female_voice(self):
        assert "female" in PIPER_VOICE_MAPPING["ru"]

    def test_all_languages_have_both_genders(self):
        for lang, genders in PIPER_VOICE_MAPPING.items():
            assert "male" in genders, f"Language '{lang}' missing male voice"
            assert "female" in genders, f"Language '{lang}' missing female voice"


# ---------------------------------------------------------------------------
# generate_audio_batch — happy path
# ---------------------------------------------------------------------------

class TestGenerateAudioBatchHappyPath:
    def test_returns_mp3_path_and_duration(self, generator: PiperAudioGenerator, output_dir: str):
        """Successful synthesis returns (str path, float duration) per chunk."""
        chunk = make_chunk(1, "Тест синтезу мовлення")

        with (
            patch.object(generator, "_synthesise_to_wav", new_callable=AsyncMock) as mock_synth,
            patch.object(generator, "_convert_wav_to_mp3") as mock_convert,
            patch.object(generator, "_get_mp3_duration", return_value=1.5),
        ):
            # Simulate _synthesise_to_wav writing a wav file
            async def fake_synth(text, voice, wav_path):
                wav_path.write_bytes(b"WAV_DATA")
            mock_synth.side_effect = fake_synth

            # Simulate _convert_wav_to_mp3 writing the mp3 tmp file
            def fake_convert(wav_path, mp3_tmp_path):
                mp3_tmp_path.write_bytes(b"MP3_DATA")
            mock_convert.side_effect = fake_convert

            results = generator.generate_audio_batch([chunk], "ru", "male", output_dir)

        assert len(results) == 1
        path, duration = results[0]
        assert path.endswith(".mp3")
        assert duration == 1.5

    def test_callback_invoked_per_chunk(self, generator: PiperAudioGenerator, output_dir: str):
        """chunk_callback is called once per successfully generated chunk."""
        chunks = [make_chunk(i, f"Text {i}") for i in range(1, 4)]
        callback_calls = []

        with (
            patch.object(generator, "_synthesise_to_wav", new_callable=AsyncMock) as mock_synth,
            patch.object(generator, "_convert_wav_to_mp3") as mock_convert,
            patch.object(generator, "_get_mp3_duration", return_value=0.5),
        ):
            async def fake_synth(text, voice, wav_path):
                wav_path.write_bytes(b"WAV")
            mock_synth.side_effect = fake_synth

            def fake_convert(wav_path, mp3_tmp_path):
                mp3_tmp_path.write_bytes(b"MP3")
            mock_convert.side_effect = fake_convert

            generator.generate_audio_batch(
                chunks, "ru", "male", output_dir,
                chunk_callback=lambda n, p, d: callback_calls.append(n),
            )

        assert sorted(callback_calls) == [1, 2, 3]

    def test_empty_chunks_returns_empty_list(self, generator: PiperAudioGenerator, output_dir: str):
        result = generator.generate_audio_batch([], "ru", "male", output_dir)
        assert result == []

    def test_unsupported_language_raises_value_error(self, generator: PiperAudioGenerator, output_dir: str):
        with pytest.raises(ValueError, match="not supported by Piper"):
            generator.generate_audio_batch([make_chunk()], "zz", "male", output_dir)


# ---------------------------------------------------------------------------
# Fatal errors
# ---------------------------------------------------------------------------

class TestFatalErrors:
    def test_empty_text_raises_fatal(self, generator: PiperAudioGenerator, output_dir: str):
        """An empty text chunk raises FatalGenerationException without retrying."""
        chunk = AudioChunk(chunk_number=1, text_content="   ")

        with patch.object(generator, "_synthesise_to_wav", new_callable=AsyncMock) as mock_synth:
            results = generator.generate_audio_batch([chunk], "ru", "male", output_dir)

        # Empty text is caught before calling _synthesise_to_wav
        mock_synth.assert_not_called()
        # The failed chunk produces None in the results list
        assert results[0] is None

    def test_synthesis_raises_value_error_is_fatal(self, generator: PiperAudioGenerator, output_dir: str):
        """A ValueError from the Wyoming layer is treated as fatal (no retry)."""
        chunk = make_chunk(1, "Valid text")

        with patch.object(
            generator, "_synthesise_to_wav",
            side_effect=ValueError("Unsupported voice"),
            new_callable=AsyncMock,
        ):
            results = generator.generate_audio_batch([chunk], "ru", "male", output_dir, max_retries=3)

        assert results[0] is None

    def test_server_sends_no_audio_raises_piper_exception(self, generator: PiperAudioGenerator, output_dir: str):
        """If Piper sends no audio data (empty WAV), PiperConnectionException is raised."""
        chunk = make_chunk(1, "Test")

        async def fake_synth(text, voice, wav_path):
            # Do not write the file → _synthesise_to_wav raises PiperConnectionException
            pass

        with patch.object(generator, "_synthesise_to_wav", new_callable=AsyncMock) as mock_synth:
            mock_synth.side_effect = PiperConnectionException("No audio data")
            results = generator.generate_audio_batch([chunk], "ru", "male", output_dir, max_retries=1)

        assert results[0] is None


# ---------------------------------------------------------------------------
# Transient errors and retry
# ---------------------------------------------------------------------------

class TestTransientRetry:
    def test_connection_refused_retries(self, generator: PiperAudioGenerator, output_dir: str):
        """ConnectionRefusedError is transient → retried; succeeds on 2nd attempt."""
        chunk = make_chunk(1, "Hello")
        call_count = 0

        async def flaky_synth(text, voice, wav_path):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionRefusedError("Connection refused")
            wav_path.write_bytes(b"WAV")

        with (
            patch.object(generator, "_synthesise_to_wav", new_callable=AsyncMock) as mock_synth,
            patch.object(generator, "_convert_wav_to_mp3") as mock_convert,
            patch.object(generator, "_get_mp3_duration", return_value=0.1),
            patch("src.domain.piper_audio_generator.asyncio.sleep", new_callable=AsyncMock),
        ):
            mock_synth.side_effect = flaky_synth

            def fake_convert(wav_path, mp3_tmp_path):
                mp3_tmp_path.write_bytes(b"MP3")
            mock_convert.side_effect = fake_convert

            results = generator.generate_audio_batch([chunk], "ru", "male", output_dir, max_retries=3)

        assert results[0] is not None
        assert call_count == 2

    def test_max_retries_exhausted_returns_none(self, generator: PiperAudioGenerator, output_dir: str):
        """After max_retries failed attempts, the chunk result is None (not an exception)."""
        chunk = make_chunk(1, "Hello")

        with (
            patch.object(
                generator, "_synthesise_to_wav",
                side_effect=PiperConnectionException("Timeout"),
                new_callable=AsyncMock,
            ),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            results = generator.generate_audio_batch([chunk], "ru", "male", output_dir, max_retries=2)

        assert results[0] is None

    def test_piper_timeout_is_transient(self, generator: PiperAudioGenerator, output_dir: str):
        """asyncio.TimeoutError during synthesis is classified as transient."""
        chunk = make_chunk(1, "Hello")
        succeed_on = [False, True]

        async def flaky_synth(text, voice, wav_path):
            if not succeed_on[0]:
                succeed_on[0] = True
                raise asyncio.TimeoutError()
            wav_path.write_bytes(b"WAV")

        with (
            patch.object(generator, "_synthesise_to_wav", new_callable=AsyncMock) as mock_synth,
            patch.object(generator, "_convert_wav_to_mp3") as mock_convert,
            patch.object(generator, "_get_mp3_duration", return_value=0.2),
            patch("src.domain.piper_audio_generator.asyncio.sleep", new_callable=AsyncMock),
        ):
            mock_synth.side_effect = flaky_synth

            def fake_convert(wav_path, mp3_tmp_path):
                mp3_tmp_path.write_bytes(b"MP3")
            mock_convert.side_effect = fake_convert

            results = generator.generate_audio_batch([chunk], "ru", "male", output_dir, max_retries=3)

        assert results[0] is not None


# ---------------------------------------------------------------------------
# WAV → MP3 conversion
# ---------------------------------------------------------------------------

class TestWavToMp3Conversion:
    def test_convert_wav_to_mp3_uses_pydub(self, tmp_path: Path):
        """_convert_wav_to_mp3 calls AudioSegment.from_wav and exports to MP3."""
        gen = PiperAudioGenerator()
        wav_path = tmp_path / "test.wav.tmp"
        mp3_path = tmp_path / "test.mp3.tmp"

        # Write a real WAV file so pydub can read it
        wav_path.write_bytes(make_valid_wav_bytes())

        with patch("pydub.AudioSegment.from_wav") as mock_from_wav:
            mock_segment = MagicMock()
            mock_from_wav.return_value = mock_segment
            gen._convert_wav_to_mp3(wav_path, mp3_path)

        mock_from_wav.assert_called_once_with(str(wav_path))
        mock_segment.export.assert_called_once_with(str(mp3_path), format="mp3", bitrate="128k")

    def test_convert_wav_removes_tmp_file(self, tmp_path: Path):
        """Temporary WAV file is deleted after conversion, even on success."""
        gen = PiperAudioGenerator()
        wav_path = tmp_path / "test.wav.tmp"
        mp3_path = tmp_path / "test.mp3.tmp"
        wav_path.write_bytes(make_valid_wav_bytes())

        with patch("pydub.AudioSegment.from_wav") as mock_from_wav:
            mock_from_wav.return_value = MagicMock()
            gen._convert_wav_to_mp3(wav_path, mp3_path)

        assert not wav_path.exists(), "Temporary WAV file should be removed after conversion"


# ---------------------------------------------------------------------------
# Concurrency / Sequential processing
# ---------------------------------------------------------------------------

class TestSequentialProcessing:
    def test_batch_processes_sequentially(self, generator: PiperAudioGenerator, output_dir: str):
        """
        Piper generator processes chunks sequentially (semaphore=1).
        Verifies that _synthesise_to_wav is called N times, one after another.
        """
        chunks = [make_chunk(i, f"Chunk {i}") for i in range(1, 4)]
        order = []

        async def ordered_synth(text, voice, wav_path):
            order.append(text)
            wav_path.write_bytes(b"x")

        with (
            patch.object(generator, "_synthesise_to_wav", new_callable=AsyncMock) as mock_synth,
            patch.object(generator, "_convert_wav_to_mp3"),
            patch.object(generator, "_get_mp3_duration", return_value=0.1),
        ):
            mock_synth.side_effect = ordered_synth
            generator.generate_audio_batch(chunks, "ru", "male", output_dir)

        # All 3 chunks were processed
        assert len(order) == 3


# ---------------------------------------------------------------------------
# Loop lifecycle
# ---------------------------------------------------------------------------

class TestLoopLifecycle:
    def test_close_stops_loop(self):
        gen = PiperAudioGenerator()
        assert gen._loop is not None and gen._loop.is_running()
        gen.close()
        assert not gen._loop.is_running()

    def test_ensure_loop_running_restarts_dead_loop(self):
        gen = PiperAudioGenerator()
        gen.close()
        gen.ensure_loop_running()
        assert gen._loop.is_running()
        gen.close()

    def test_get_file_duration_fast_alias(self):
        gen = PiperAudioGenerator()
        with patch.object(gen, "_get_mp3_duration", return_value=5.0) as mock_get:
            res = gen._get_file_duration_fast("/tmp/test.mp3")
            assert res == 5.0
            mock_get.assert_called_once_with("/tmp/test.mp3")
        gen.close()


# ---------------------------------------------------------------------------
# Text sanitization and non-speech handling
# ---------------------------------------------------------------------------

class TestTextSanitizationAndSilence:
    def test_sanitize_text_for_piper_normalizes_ellipses_and_dashes(self):
        """_sanitize_text_for_piper collapses repeated dots and dialogue dash ellipses."""
        text = "Взошла луна…. Потом\n-… После того\nLine 1\n— — —\nLine 2\n(Гендо открывает кейс…)\nТест。Два！Три？"
        sanitized = PiperAudioGenerator._sanitize_text_for_piper(text)
        assert "луна..." in sanitized
        assert "— После того" in sanitized
        assert "— — —" not in sanitized
        assert "Line 1\nLine 2" in sanitized
        assert "(Гендо открывает кейс...)" in sanitized
        assert "Тест.Два!Три?" in sanitized

    def test_sanitize_text_for_piper_strips_leading_punctuation_on_lines(self):
        """_sanitize_text_for_piper strips leading punctuation marks at line and string starts."""
        text = "Line 1\n\n. С незаинтересованным видом\n\n! На это как будто\n\n? Кто здесь\n\n... Текст далі"
        sanitized = PiperAudioGenerator._sanitize_text_for_piper(text)
        assert ". С" not in sanitized
        assert "С незаинтересованным видом" in sanitized
        assert "! На" not in sanitized
        assert "На это как будто" in sanitized
        assert "? Кто" not in sanitized
        assert "Кто здесь" in sanitized
        assert "... Текст" not in sanitized
        assert "Текст далі" in sanitized

    def test_sanitize_text_for_piper_preserves_dialogue_dashes(self):
        """Dialogue dashes are preserved, but leading punctuation before them is stripped."""
        text = "— Привет\n\n. — Как дела?"
        sanitized = PiperAudioGenerator._sanitize_text_for_piper(text)
        assert "— Привет" in sanitized
        assert "— Как дела?" in sanitized
        assert ". —" not in sanitized

    def test_non_speech_chunk_returns_silent_mp3_without_synthesising(
        self, generator: PiperAudioGenerator, output_dir: str
    ):
        """A chunk with no alphanumeric characters returns short silence without calling Wyoming."""
        chunk = AudioChunk(chunk_number=42, text_content="— ... —")

        with patch.object(generator, "_synthesise_to_wav", new_callable=AsyncMock) as mock_synth:
            results = generator.generate_audio_batch([chunk], "ru", "male", output_dir)

        mock_synth.assert_not_called()
        assert results[0] is not None
        mp3_path, duration = results[0]
        assert Path(mp3_path).exists()
        assert duration >= 0.0

    def test_sanitized_text_is_passed_to_synthesise_to_wav(
        self, generator: PiperAudioGenerator, output_dir: str
    ):
        """Sanitized text is sent to _synthesise_to_wav rather than raw text with problematic tokens."""
        chunk = AudioChunk(chunk_number=1, text_content="Задумка:\n\n-… После того, как я возьму интервью")
        called_texts = []

        async def fake_synth(text, voice, wav_path):
            called_texts.append(text)
            wav_path.write_bytes(b"RIFF dummy wav")

        def fake_convert(wav_path, mp3_tmp_path):
            mp3_tmp_path.write_bytes(b"MP3_DATA")

        with (
            patch.object(generator, "_synthesise_to_wav", side_effect=fake_synth),
            patch.object(generator, "_convert_wav_to_mp3", side_effect=fake_convert),
            patch.object(generator, "_get_mp3_duration", return_value=1.5),
        ):
            results = generator.generate_audio_batch([chunk], "ru", "male", output_dir)

        assert results[0] is not None
        assert len(called_texts) == 1
        assert "— После того" in called_texts[0]
        assert "-…" not in called_texts[0]


