import struct


def _loud_pcm16_chunk() -> bytes:
    # A burst of a full-scale square wave, guaranteed to be above the
    # placeholder silence-RMS threshold.
    samples = [32000 if i % 2 == 0 else -32000 for i in range(400)]
    return struct.pack(f"<{len(samples)}h", *samples)


def _silent_pcm16_chunk() -> bytes:
    return struct.pack("<400h", *([0] * 400))


def test_ws_emits_note_detection_for_loud_audio(client):
    with client.websocket_connect("/ws/track-audio") as websocket:
        websocket.send_bytes(_loud_pcm16_chunk())
        message = websocket.receive_json()

    assert message["type"] == "NOTE_DETECTION"
    assert message["notes"] == ["C4"]
    assert 0 < message["confidence"] <= 1
    assert message["rms"] > 0


def test_ws_reports_empty_notes_for_quiet_audio(client):
    with client.websocket_connect("/ws/track-audio") as websocket:
        websocket.send_bytes(_silent_pcm16_chunk())
        silent_message = websocket.receive_json()

        websocket.send_bytes(_loud_pcm16_chunk())
        loud_message = websocket.receive_json()

    # Every chunk produces a message now, even silent ones, so the client
    # can tell audio is arriving instead of just seeing a stale detection.
    assert silent_message["notes"] == []
    assert silent_message["confidence"] == 0.0
    assert loud_message["notes"] == ["C4"]
