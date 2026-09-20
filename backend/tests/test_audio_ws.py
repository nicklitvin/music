import json
import math
import struct

SAMPLE_RATE = 16000

# A few real note frequencies (Hz, 12-TET, A4=440Hz) to synthesize test tones.
C4 = 261.63
E4 = 329.63
G4 = 392.00
E5 = 659.26


def _sine_pcm16(freq_hz: float, num_samples: int, amplitude: int = 16000) -> bytes:
    samples = [
        int(amplitude * math.sin(2 * math.pi * freq_hz * i / SAMPLE_RATE)) for i in range(num_samples)
    ]
    return struct.pack(f"<{len(samples)}h", *samples)


def _chord_pcm16(freqs_hz: list[float], num_samples: int, amplitude: int = 16000) -> bytes:
    per_note_amplitude = amplitude // len(freqs_hz)
    samples = [
        sum(
            int(per_note_amplitude * math.sin(2 * math.pi * freq_hz * i / SAMPLE_RATE))
            for freq_hz in freqs_hz
        )
        for i in range(num_samples)
    ]
    return struct.pack(f"<{len(samples)}h", *samples)


def _harmonic_rich_pcm16(freq_hz: float, num_samples: int, amplitude: int = 12000) -> bytes:
    # A handful of overtones with decreasing amplitude, like a real
    # instrument tone (as opposed to a pure sine) -- this is what should
    # still collapse to a single detected note, not one note per overtone.
    samples = []
    for i in range(num_samples):
        t = i / SAMPLE_RATE
        value = sum((1 / h) * math.sin(2 * math.pi * freq_hz * h * t) for h in range(1, 7))
        samples.append(int(amplitude * value))
    return struct.pack(f"<{len(samples)}h", *samples)


def _silent_pcm16_chunk() -> bytes:
    return struct.pack("<400h", *([0] * 400))


def test_ws_detects_a_single_note(client):
    with client.websocket_connect("/ws/track-audio") as websocket:
        websocket.send_bytes(_sine_pcm16(C4, 4096))
        message = websocket.receive_json()

    assert message["type"] == "NOTE_DETECTION"
    assert message["notes"] == ["C4"]
    assert 0 < message["confidence"] <= 1
    assert message["rms"] > 0


def test_ws_detects_a_chord_as_multiple_notes(client):
    with client.websocket_connect("/ws/track-audio") as websocket:
        websocket.send_bytes(_chord_pcm16([C4, E4, G4], 4096))
        message = websocket.receive_json()

    assert sorted(message["notes"]) == ["C4", "E4", "G4"]


def test_ws_does_not_split_a_single_harmonic_rich_note_into_a_chord(client):
    with client.websocket_connect("/ws/track-audio") as websocket:
        websocket.send_bytes(_harmonic_rich_pcm16(C4, 4096))
        message = websocket.receive_json()

    assert message["notes"] == ["C4"]


def test_ws_reports_empty_notes_for_quiet_audio(client):
    with client.websocket_connect("/ws/track-audio") as websocket:
        websocket.send_bytes(_silent_pcm16_chunk())
        silent_message = websocket.receive_json()

        websocket.send_bytes(_sine_pcm16(C4, 4096))
        loud_message = websocket.receive_json()

    # Every chunk produces a message now, even silent ones, so the client
    # can tell audio is arriving instead of just seeing a stale detection.
    assert silent_message["notes"] == []
    assert silent_message["confidence"] == 0.0
    assert loud_message["notes"] == ["C4"]


def _box(pitch: str, x: float) -> dict:
    return {
        "x": x, "y": 0.0, "width": 10.0, "height": 10.0,
        "note": "quarter", "pitch": pitch, "measureIndex": 1, "pageIndex": 0,
    }


# A three-onset score whose middle onset is the only high note, so a
# sustained E5 is unambiguous evidence for position 1.
SCORE_NOTES = [_box("C4", 100.0), _box("E5", 150.0), _box("C4", 200.0)]


def test_ws_tracks_score_position_after_init(client):
    with client.websocket_connect("/ws/track-audio") as websocket:
        websocket.send_text(json.dumps({"type": "INIT", "notes": SCORE_NOTES}))

        message = None
        for _ in range(20):
            websocket.send_bytes(_harmonic_rich_pcm16(E5, 4096))
            message = websocket.receive_json()

    assert message["type"] == "POSITION"
    assert message["onsetIndex"] == 1
    assert 0.0 <= message["positionConfidence"] <= 1.0
    # POSITION frames still carry the raw detection fields the log uses.
    assert "notes" in message and "rms" in message


def test_ws_without_init_still_reports_plain_note_detection(client):
    with client.websocket_connect("/ws/track-audio") as websocket:
        websocket.send_bytes(_sine_pcm16(C4, 4096))
        message = websocket.receive_json()

    assert message["type"] == "NOTE_DETECTION"
    assert "onsetIndex" not in message


def test_ws_tracks_score_position_with_the_harmonic_front_end(client, monkeypatch):
    # USE_LEARNED_TRANSCRIPTION=false is the documented way back to the
    # pre-basic-pitch note detector, so it has to keep working.
    from app.config import settings

    monkeypatch.setattr(settings, "use_learned_transcription", False)

    with client.websocket_connect("/ws/track-audio") as websocket:
        websocket.send_text(json.dumps({"type": "INIT", "notes": SCORE_NOTES}))

        message = None
        for _ in range(20):
            websocket.send_bytes(_harmonic_rich_pcm16(E5, 4096))
            message = websocket.receive_json()

    assert message["type"] == "POSITION"
    assert message["onsetIndex"] == 1


def test_ws_accepts_a_position_hint(client):
    with client.websocket_connect("/ws/track-audio") as websocket:
        websocket.send_text(json.dumps({"type": "INIT", "notes": SCORE_NOTES}))
        websocket.send_text(json.dumps({"type": "HINT", "onsetIndex": 2}))

        websocket.send_bytes(_sine_pcm16(C4, 4096))
        message = websocket.receive_json()

    assert message["type"] == "POSITION"
    # The hint put the belief on the last C4 rather than the first.
    assert message["onsetIndex"] == 2
