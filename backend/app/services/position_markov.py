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
    # Sharpens cosine similarity into a log-likelihood. Swept against the
    # degraded-performance set, not just clean audio: too low and a single
    # corrupted frame can outweigh all the evidence before it.
    temperature: float = 0.10
    # Onsets a single transition may advance past, covering onsets too
    # quiet or too brief to register.
    max_skip: int = 2
    # Multiplicative cost per extra onset skipped: skipping stays possible
    # but never free.
    skip_decay: float = 0.02
    # Probability mass spread uniformly over the whole score each step --
    # what lets the model recover from a wrong lock-in and makes a
    # mid-piece restart findable. Without it, belief that has collapsed
    # onto the wrong onset can never climb back.
    #
    # It is gated on confidence because the two situations need opposite
    # things. While the model does not know where it is, it must be free
    # to consider anywhere. Once it does, that same freedom is a liability:
    # on a flawed performance a single badly-matching frame would otherwise
    # teleport a confident tracker to an unrelated part of the page. So
    # searching is cheap while lost and expensive once settled.
    jump_probability: float = 1e-4
    jump_probability_confident: float = 1e-10
    # Confidence above which the model counts as settled.
    jump_confidence_gate: float = 0.9
    # Belief within +/- this many onsets of the best guess counts toward
    # confidence. Neighbours are legitimately ambiguous -- they share most
    # of their sounding notes -- so demanding all the mass on one onset
    # would understate how well the position is actually known.
    confidence_radius: int = 2

    # How much a frame's own salience -- unit-norm, so this reads shape, not
    # loudness -- is trusted to steer the belief. A real struck note (or
    # chord) concentrates energy into a few harmonic peaks; background
    # noise, room sound, or audio the harmonic model can't resolve spreads
    # it thinly, so its peak is measurably lower even at the same volume.
    # Below evidence_floor a frame is treated as too weak to discriminate
    # between onsets at all and steers nothing; the influence ramps linearly
    # up to full weight at evidence_ceiling. Without this, a run of weak,
    # ambiguous frames (someone settling in before actually playing) can
    # still scatter belief across the whole search window -- nothing anchors
    # it near the current position -- and argmax ends up picking essentially
    # at random among near-equal noise-driven candidates, which can be
    # anywhere the window reaches. Calibrated against
    # HarmonicSalienceEstimator output: clean single notes and chords peak
    # around 0.28-0.41; broadband noise peaks around 0.16-0.20 regardless of
    # its RMS.
    evidence_floor: float = 0.20
    evidence_ceiling: float = 0.32

    # Locality window. When set, each frame only updates belief for onsets
    # within this many ahead / behind the current estimate; everything
    # outside gets no probability that frame. None (the benchmark default)
    # considers the whole score, which is what lets the model lock on from
    # a cold start anywhere. A live page-turner instead knows where it
    # started, so a small window makes a jump to a repeated passage
    # elsewhere on the page structurally impossible rather than merely
    # unlikely -- forward is generous (catching up a lag), backward tight.
    search_ahead: int | None = None
    search_behind: int = 8

    # --- tempo adaptation ---
    # The dwell model is built from the score's marked tempo, but nobody
    # plays at exactly that, and a performer who is 20% fast makes the
    # model expect every onset to last longer than it does, so tracking
    # lags further behind with each onset. These let the expected dwell
    # follow the tempo actually being played.
    adapt_tempo: bool = True
    # How many frames of history the tempo estimate is measured over.
    # Long enough to average out per-onset noise, short enough to follow a
    # performer who is speeding up.
    tempo_window_frames: int = 60
    # EMA weight for each new tempo measurement. Deliberately small: the
    # estimate feeds back into the tracking that produces it, so reacting
    # fast risks a tracker that lags, infers "slow", and lags further.
    tempo_smoothing: float = 0.1
    # Hard limits on the inferred ratio, so a spell of bad tracking cannot
    # drive the dwell model somewhere absurd it cannot return from.
    min_tempo_ratio: float = 0.5
    max_tempo_ratio: float = 2.5
    # Only learn tempo while the position is trusted; measuring it from a
    # lost tracker is what turns a wrong guess into a stuck one.
    tempo_min_confidence: float = 0.6


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

        # Ratio of the tempo actually being played to the score's marked
        # tempo. 1.0 until there is evidence otherwise.
        self.tempo_ratio = 1.0
        self._frame_seconds = 0.0
        self._recent: list[tuple[int, int]] = []  # (frame number, position)
        self._frame_number = 0

    def _transition_log_probs(self, frame_seconds: float) -> None:
        durations = np.array([onset.advance_seconds for onset in self.timeline])
        self._frame_seconds = frame_seconds
        self._expected_frames = np.maximum(1.0, durations / max(frame_seconds, 1e-6))

    def _update_tempo(self, position: int, confidence: float) -> None:
        """Infers the played tempo from how fast the position is advancing.

        Compares onsets actually covered over the recent window against how
        many the score says should have been covered in that time. Only
        runs while the position is trusted, and only forwards -- a backward
        jump is a correction or a repeat, not evidence about tempo.
        """
        assert self._expected_frames is not None
        self._recent.append((self._frame_number, position))
        cutoff = self._frame_number - self.config.tempo_window_frames
        self._recent = [entry for entry in self._recent if entry[0] >= cutoff]

        if confidence < self.config.tempo_min_confidence or len(self._recent) < 2:
            return

        (first_frame, first_position), (last_frame, last_position) = self._recent[0], self._recent[-1]
        onsets_advanced = last_position - first_position
        frames_elapsed = last_frame - first_frame
        if onsets_advanced <= 0 or frames_elapsed <= 0:
            return

        # Frames the score expects those same onsets to have taken.
        expected = float(self._expected_frames[first_position:last_position].sum())
        if expected <= 0:
            return

        measured = expected / frames_elapsed
        blended = (1 - self.config.tempo_smoothing) * self.tempo_ratio + self.config.tempo_smoothing * measured
        self.tempo_ratio = float(np.clip(blended, self.config.min_tempo_ratio, self.config.max_tempo_ratio))

    def _advance(self) -> np.ndarray:
        """Applies the transition model to the current belief."""
        assert self._expected_frames is not None
        belief = np.exp(self._log_belief - self._log_belief.max())
        belief /= belief.sum()

        # Scale the expected dwell by the tempo actually being played, so a
        # performer running fast advances the belief at their rate.
        effective_frames = np.maximum(1.0, self._expected_frames / self.tempo_ratio)
        advance_prob = 1.0 / effective_frames
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

        settled = self.estimate().confidence >= self.config.jump_confidence_gate
        jump = self.config.jump_probability_confident if settled else self.config.jump_probability
        moved = (1.0 - jump) * moved + jump / len(moved)
        return moved / moved.sum()

    def observe(self, frame: np.ndarray) -> PositionEstimate:
        salience = self.estimator.estimate(frame)
        if self._expected_frames is None:
            self._transition_log_probs(len(frame) / self.estimator.sample_rate)

        # Below evidence_floor the salience is too weak/undiscriminating to
        # be evidence of anything -- background noise or room sound, not a
        # struck note. Treated exactly like silence: no positional update at
        # all, including no transition-model advance. Without this, elapsed
        # time alone (someone settling in before actually playing) creeps
        # the belief forward at the score's nominal tempo with nothing to
        # anchor it, which reads as "the model thinks I've started" when
        # nothing has been played yet.
        if salience.any() and salience.max() >= self.config.evidence_floor:
            self._frame_number += 1
            prior = self._advance()
            log_likelihood = (self.templates @ salience) / self.config.temperature
            # Above the floor but below evidence_ceiling, still only partly
            # trust it -- ramps the observation's influence in linearly, so
            # a merely-plausible frame nudges belief without letting it
            # override the prior's own locality outright.
            span = max(self.config.evidence_ceiling - self.config.evidence_floor, 1e-9)
            evidence_weight = float(np.clip((salience.max() - self.config.evidence_floor) / span, 0.0, 1.0))
            log_posterior = np.log(np.maximum(prior, 1e-300)) + evidence_weight * log_likelihood
            if self.config.search_ahead is not None:
                # Keep the update local: nothing outside a window around the
                # current estimate can gain probability this frame.
                center = int(np.argmax(self._log_belief))
                lo = max(0, center - self.config.search_behind)
                hi = center + self.config.search_ahead + 1
                log_posterior[:lo] = -np.inf
                log_posterior[hi:] = -np.inf
            log_posterior -= log_posterior.max()
            posterior = np.exp(log_posterior)
            posterior /= posterior.sum()
            self._log_belief = np.log(np.maximum(posterior, 1e-300))

            if self.config.adapt_tempo:
                estimate = self.estimate()
                self._update_tempo(estimate.index, estimate.confidence)
                return estimate

        return self.estimate()

    def apply_hint(self, index: int, strength: float = 0.95, width: float = 3.0) -> PositionEstimate:
        """Fold in an outside claim about where the player is.

        Scrolling the page by hand is a statement -- the reader is looking
        here -- and it is usually a correction, made precisely because the
        display was in the wrong place. So this is mixed into the belief
        rather than multiplied through it: a confidently wrong tracker has
        assigned the correct region a probability near zero, and anything
        multiplicative would scale that to approximately zero again. The
        mixture puts real mass back on the hinted region regardless of how
        certain the model previously was.

        The hint is a bump, not a spike, because a scroll says roughly
        where, not exactly which onset -- `width` onsets of slack either
        side. Audio evidence then sharpens it over the next few frames.

        `strength` is how much of the belief the hint claims, and defaults
        high deliberately: the residual left to a *confidently* wrong
        tracker is concentrated on one onset, while the hint's is spread
        over `width` of them, so a merely moderate strength loses to the
        very mistake the reader was correcting.
        """
        if not len(self._log_belief):
            return self.estimate()

        index = int(np.clip(index, 0, len(self._log_belief) - 1))
        strength = float(np.clip(strength, 0.0, 1.0))

        belief = np.exp(self._log_belief - self._log_belief.max())
        belief /= belief.sum()

        positions = np.arange(len(belief))
        bump = np.exp(-0.5 * ((positions - index) / max(width, 1e-6)) ** 2)
        bump /= bump.sum()

        blended = (1.0 - strength) * belief + strength * bump
        blended /= blended.sum()
        self._log_belief = np.log(np.maximum(blended, 1e-300))

        # A manual correction invalidates the tempo history, which was
        # measured from positions now believed to be wrong.
        self._recent.clear()

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
