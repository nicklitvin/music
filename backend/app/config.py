from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    frontend_origin: str = "http://localhost:5173"
    render_dpi: int = 300

    # Which note-detection front end feeds position tracking.
    #
    # True (default): the vendored basic-pitch transcription model
    #   (app/services/streaming_transcription.py). Measured much better at
    #   finding where a reader is -- see "Note detection" in the README.
    # False: the hand-written harmonic-salience estimator
    #   (app/services/note_estimation.py), which is what shipped before.
    #
    # Set USE_LEARNED_TRANSCRIPTION=false to switch back; nothing else needs
    # changing, and both paths are exercised by the test suite.
    use_learned_transcription: bool = True


settings = Settings()
