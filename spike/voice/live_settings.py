"""The Gemini Live settings the worker and the direct control share, so they can't drift apart.

Only google-genai types and the backend's prompt code are imported here, so direct.py and
summarize.py don't have to load the LiveKit agent framework to get them.
"""

import os
import sys
from pathlib import Path

from google.genai import types

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.actor.prompt import build_customer_prompt, prompt_problems  # noqa: E402
from app.content.loader import load_bundle  # noqa: E402

SCENARIO_ID = "scenario-101"
MOOD = "worried"
DEFAULT_VOICE = "Aoede"
TEMPERATURE = 0.8


def customer_prompt() -> str:
    bundle = load_bundle()
    prompt = build_customer_prompt(bundle.scenarios[SCENARIO_ID], bundle.frame, MOOD)
    problems = prompt_problems(prompt, bundle)
    if problems:
        raise SystemExit(f"prompt guard failed: {problems}")
    return prompt.text


def voice() -> str:
    return os.environ.get("SPIKE_VOICE", DEFAULT_VOICE)


def end_of_speech(silence_ms: int | None) -> types.RealtimeInputConfig | None:
    """Gemini's server-side end-of-speech detection. None leaves Google's defaults alone."""
    if silence_ms is None:
        return None
    return types.RealtimeInputConfig(
        automatic_activity_detection=types.AutomaticActivityDetection(
            end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_HIGH,
            silence_duration_ms=silence_ms,
        )
    )


def context_compression() -> types.ContextWindowCompressionConfig:
    return types.ContextWindowCompressionConfig(sliding_window=types.SlidingWindow())


def connect_config(silence_ms: int | None) -> types.LiveConnectConfig:
    """The connect config for a client that talks to Gemini Live itself (direct.py). The worker
    hands the same pieces to the LiveKit plugin, which builds its own config from them.

    What the plugin's config has that this one doesn't: it always asks for session resumption
    handles, and it puts the temperature in generation_config."""
    return types.LiveConnectConfig(
        response_modalities=[types.Modality.AUDIO],
        system_instruction=customer_prompt(),
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice())
            )
        ),
        temperature=TEMPERATURE,
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        realtime_input_config=end_of_speech(silence_ms),
        context_window_compression=context_compression(),
    )
