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

    # How many pages to run OMR on at once. 0 picks a value from the core
    # count, capped low because oemer is memory-hungry rather than
    # core-hungry -- raise it only if there is RAM headroom to spare.
    omr_workers: int = 0

    # How many image tiles oemer feeds its neural nets at once. Peak memory
    # scales with it; output and speed don't (see oemer_engine.extract_page).
    # oemer's own default is 16, which doesn't fit a 4 GB box.
    omr_batch_size: int = 4

    # How far apart oemer's first (staff) net places its 256 px image tiles.
    # oemer's own 128 overlaps them 4x; 256 doesn't overlap them, for the
    # same notes 25% faster (see oemer_engine.extract_page).
    omr_staff_step_size: int = 256


settings = Settings()
