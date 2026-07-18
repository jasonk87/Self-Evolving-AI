import numpy as np
import pytest

def calculate_rms(data: bytes) -> int:
    audio_data = np.frombuffer(data, dtype=np.int16)
    return int(np.sqrt(np.mean(audio_data.astype(np.float64) ** 2))) if len(audio_data) > 0 else 0

def test_rms_calculation_empty():
    assert calculate_rms(b"") == 0

def test_rms_calculation_sine_wave():
    # Generate a simple 16-bit PCM sine wave
    amplitude = 10000
    frequency = 440
    sample_rate = 44100
    t = np.linspace(0, 0.1, int(sample_rate * 0.1), endpoint=False)
    sine_wave = (amplitude * np.sin(2 * np.pi * frequency * t)).astype(np.int16)
    data = sine_wave.tobytes()

    rms = calculate_rms(data)
    # The RMS of a sine wave with amplitude A is A / sqrt(2)
    expected_rms = int(amplitude / np.sqrt(2))

    # Allow small precision difference due to discretization
    assert abs(rms - expected_rms) < 5
