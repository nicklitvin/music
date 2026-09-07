"""Where in the score the player is, as a probability distribution.

Separate from note_estimation.py, which decides what is sounding without
any knowledge of the score. This module takes that per-pitch evidence and
answers only "where does it put us on the page".

The state is a full belief over every onset, updated with the forward
(Bayes filter) recursion of a hidden Markov model:

    belief(i) <- P(evidence | i) * SUM_j belief(j) * P(i | j)

Two things follow from keeping the whole distribution rather than a single
best guess, and both matter here:

* **Cold start.** Beginning from a uniform prior means no assumption about
  where the player started -- they can drop in at the middle of the page.
  A Viterbi decode pinned to onset 0 cannot do this, and a greedy nearest
  match has nothing to be near.
* **Calibrated confidence.** How concentrated the belief is says how sure
  the model is, so the caller can wait for the estimate to settle instead
  of acting on a guess made from one ambiguous frame. Individual frames
  *are* ambiguous -- neighbouring onsets share most of their sounding
  notes -- so confidence accumulating over several frames is the mechanism
  that resolves position at all.

The transition model is what encodes musical sense: mostly stay put for
about as long as the current onset lasts, sometimes advance, occasionally
skip a missed onset, and rarely jump anywhere at all (the player restarted,
turned back a page, or the model was simply wrong).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from app.services.note_estimation import (
    HarmonicSalienceEstimator,
    NUM_PITCHES,
    MIN_MIDI,
    MAX_MIDI,
    pitch_to_midi,
)
from app.services.score_timeline import TimelineOnset

# How quickly a struck piano note's energy decays, in seconds -- a note
# struck this long ago contributes ~1/e of its original weight to what is
# heard now.
NOTE_DECAY_SECONDS = 1.0


def sounding_weights(timeline: list[TimelineOnset], index: int) -> dict[int, float]:
    """What is audible at onset `index`, as {midi: weight}.

    Not the same as the notes that *start* here: a note struck earlier
    keeps ringing (a held bass note under several melody notes), so the
    audio is a decaying mixture of everything still sounding. A template
    built only from the notes starting at an onset mispredicts the spectrum
    as soon as the score sustains anything, and every later onset then
    matches the observed audio better than the correct one.
    """
    now = timeline[index].start_seconds
    weights: dict[int, float] = {}

    for onset in timeline[: index + 1]:
        age = now - onset.start_seconds
        for note, duration in zip(onset.notes, onset.note_durations):
            if onset.start_seconds + duration <= now:
                continue  # already released
            midi = pitch_to_midi(note.pitch)
            if midi is None or not (MIN_MIDI <= midi <= MAX_MIDI):
                continue
            weights[midi] = max(weights.get(midi, 0.0), math.exp(-age / NOTE_DECAY_SECONDS))

    return weights


def build_templates(timeline: list[TimelineOnset]) -> np.ndarray:
    """Expected salience at each onset, unit-norm, ready to cosine-match."""
    templates = np.zeros((len(timeline), NUM_PITCHES))
    for index in range(len(timeline)):
        for midi, weight in sounding_weights(timeline, index).items():
            templates[index, midi - MIN_MIDI] = weight
        norm = np.linalg.norm(templates[index])
        if norm > 0:
            templates[index] /= norm
    return templates


@dataclass
class MarkovConfig:
    # Sharpens cosine similarity into a log-likelihood. Swept, not guessed.
    temperature: float = 0.06
    # Onsets a single transition may advance past, covering onsets too
    # quiet or too brief to register.
    max_skip: int = 2
    # Multiplicative cost per extra onset skipped: skipping stays possible
    # but never free.
    skip_decay: float = 0.02
    # Probability mass spread uniformly over the whole score each step.
    # This is what lets the model recover from a wrong lock-in and what
    # makes a mid-piece restart findable; without it, belief that has
    # collapsed onto the wrong onset can never climb back.
    jump_probability: float = 1e-4
    # Belief within +/- this many onsets of the best guess counts toward
    # confidence. Neighbours are legitimately ambiguous -- they share most
    # of their sounding notes -- so demanding all the mass on one onset
    # would understate how well the position is actually known.
    confidence_radius: int = 2


@dataclass
class PositionEstimate:
    index: int
    confidence: float
    # (index, probability) for the strongest few candidates, most likely
    # first -- useful for showing why the model is unsure.
    candidates: list[tuple[int, float]]


class MarkovPositionTracker:
    """Bayes-filter tracking of score position from per-pitch salience.

    Call `observe` with consecutive audio frames from one performance; it
    returns the current best position plus how confident it is. Starts
    from a uniform prior, so the performance may begin anywhere in the
    score.
    """

    def __init__(
        self,
        timeline: list[TimelineOnset],
        config: MarkovConfig | None = None,
        estimator: HarmonicSalienceEstimator | None = None,
    ):
        self.timeline = timeline
        self.config = config or MarkovConfig()
        self.estimator = estimator or HarmonicSalienceEstimator()
        self.templates = build_templates(timeline)

        count = len(timeline)
        # Uniform prior: no assumption about the starting position.
        self._log_belief = np.full(count, -math.log(count) if count else 0.0)
        self._expected_frames: np.ndarray | None = None

    def _transition_log_probs(self, frame_seconds: float) -> None:
        durations = np.array([onset.advance_seconds for onset in self.timeline])
        self._expected_frames = np.maximum(1.0, durations / max(frame_seconds, 1e-6))

    def _advance(self) -> np.ndarray:
        """Applies the transition model to the current belief."""
        assert self._expected_frames is not None
        belief = np.exp(self._log_belief - self._log_belief.max())
        belief /= belief.sum()

        advance_prob = 1.0 / self._expected_frames
        moved = belief * (1.0 - advance_prob)  # stayed put

        remaining = belief * advance_prob
        weights = np.array([self.config.skip_decay**step for step in range(self.config.max_skip)])
        weights /= weights.sum()
        for step in range(1, self.config.max_skip + 1):
            shifted = np.zeros_like(moved)
            shifted[step:] = remaining[:-step] * weights[step - 1]
            # Mass advancing off the end piles up on the final onset rather
            # than vanishing, so finishing the page is a stable state.
            shifted[-1] += remaining[-step:].sum() * weights[step - 1]
            moved += shifted

        jump = self.config.jump_probability
        moved = (1.0 - jump) * moved + jump / len(moved)
        return moved / moved.sum()

    def observe(self, frame: np.ndarray) -> PositionEstimate:
        salience = self.estimator.estimate(frame)
        if self._expected_frames is None:
            self._transition_log_probs(len(frame) / self.estimator.sample_rate)

        if salience.any():
            prior = self._advance()
            log_likelihood = (self.templates @ salience) / self.config.temperature
            log_posterior = np.log(np.maximum(prior, 1e-300)) + log_likelihood
            log_posterior -= log_posterior.max()
            posterior = np.exp(log_posterior)
            posterior /= posterior.sum()
            self._log_belief = np.log(np.maximum(posterior, 1e-300))

        return self.estimate()

    def estimate(self) -> PositionEstimate:
        belief = np.exp(self._log_belief - self._log_belief.max())
        belief /= belief.sum()

        best = int(np.argmax(belief))
        radius = self.config.confidence_radius
        low, high = max(0, best - radius), min(len(belief), best + radius + 1)
        confidence = float(belief[low:high].sum())

        top = np.argsort(-belief)[:5]
        return PositionEstimate(
            index=best,
            confidence=confidence,
            candidates=[(int(i), float(belief[i])) for i in top],
        )
