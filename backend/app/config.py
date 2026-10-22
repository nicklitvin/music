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

    # How many 256 px tiles oemer's staff net is fed at once. Peak memory
    # scales with it; output and speed don't. oemer's own default of 16
    # doesn't fit a 4 GB box.
    omr_batch_size: int = 4

    # The symbol net's tiles: about this many px square, overlapping by
    # this much (see oemer_inference). On four test pages, 384/96 gave the
    # same notes as oemer's own tiling bar 14 of 2391 (64 px of overlap:
    # 21), in well under half the time. Bigger tiles are no faster, just
    # hungrier: on one page 640 px peaked at 1.8 GB, 384 px at 0.8 GB.
    omr_tile_size: int = 384
    omr_tile_overlap: int = 96

    # Threads for the neural nets. 0 uses every core the machine reports.
    omr_threads: int = 0

    # Run OMR on AWS Lambda instead of on this machine: the function's name
    # or ARN (see backend/lambda/README.md). Empty runs it here. Pages go
    # out all at once, up to omr_lambda_concurrency of them.
    omr_lambda_function: str = ""
    omr_lambda_region: str = ""
    omr_lambda_concurrency: int = 10


settings = Settings()
