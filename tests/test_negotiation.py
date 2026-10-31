import unittest
from unittest.mock import patch

import numpy as np

from sonic_courier.audio import SAMPLE_RATE, ToneProfile
from sonic_courier.live import negotiate_receiver, negotiate_sender
from sonic_courier.negotiation import (
    CONTROL_CANDIDATES,
    CONTROL_PLAN,
    SOUNDING_FREQUENCIES,
    analyze_sounding_sweep,
    decode_control_message,
    encode_control_message,
    generate_sounding_sweep,
    profile_score,
    rank_candidate_profiles,
)
from sonic_courier.protocol import build_frame, demodulate, modulate


class NegotiationTests(unittest.TestCase):
    def test_sounding_selects_a_band_that_survives_noise_and_filtering(self):
        levels = np.where(
            (SOUNDING_FREQUENCIES >= 8_000) & (SOUNDING_FREQUENCIES <= 12_000),
            0.70,
            0.01,
        )
        sweep = generate_sounding_sweep(amplitudes=levels)
        leading_samples = round(SAMPLE_RATE * 0.45)
        rng = np.random.default_rng(2026)
        recording = rng.normal(0, 300, leading_samples + sweep.size).astype(np.int16)
        mixed = recording[leading_samples:].astype(np.int32) + sweep.astype(np.int32)
        recording[leading_samples:] = np.clip(mixed, -32768, 32767).astype(np.int16)

        result = analyze_sounding_sweep(recording)
        profiles = rank_candidate_profiles(result)

        self.assertGreaterEqual(profiles[0].base_frequency, 7_600)
        self.assertLessEqual(profiles[0].high_preamble_frequency, 12_400)
        self.assertGreater(profile_score(result, profiles[0]), 20.0)

    def test_control_message_survives_leading_noise(self):
        profiles = (
            ToneProfile("auto-8400", 8_400.0, 200.0),
            ToneProfile("auto-7600", 7_600.0, 200.0),
        )
        signal = encode_control_message(CONTROL_CANDIDATES, profiles)
        rng = np.random.default_rng(7)
        leading = rng.normal(0, 120, round(SAMPLE_RATE * 0.25)).astype(np.int16)
        noisy = signal.astype(np.int32) + rng.normal(0, 180, signal.size).astype(np.int32)
        recording = np.concatenate((leading, np.clip(noisy, -32768, 32767).astype(np.int16)))

        decoded = decode_control_message(recording)

        self.assertEqual(decoded.kind, CONTROL_CANDIDATES)
        self.assertEqual(
            [(profile.base_frequency, profile.step_frequency) for profile in decoded.profiles],
            [(8_400.0, 200.0), (7_600.0, 200.0)],
        )

    def test_negotiated_profile_round_trips_file_audio(self):
        profile = ToneProfile("auto-8400", 8_400.0, 200.0)
        frame = build_frame(b"adaptive payload", "adaptive.bin")

        decoded = demodulate(modulate(frame, profile=profile), profile=profile)

        self.assertEqual(decoded.filename, "adaptive.bin")
        self.assertEqual(decoded.data, b"adaptive payload")
        self.assertAlmostEqual(decoded.measured_low, 8_400.0, delta=2.0)

    def test_sender_handshake_returns_and_confirms_mutual_profile(self):
        forward = analyze_sounding_sweep(generate_sounding_sweep())
        candidates = rank_candidate_profiles(forward)
        response = np.concatenate(
            (
                encode_control_message(CONTROL_CANDIDATES, candidates),
                np.zeros(round(SAMPLE_RATE * 0.15), dtype=np.int16),
                generate_sounding_sweep(),
            )
        )
        played = []

        with (
            patch("sonic_courier.live.play", side_effect=played.append),
            patch("sonic_courier.live.record_audio", return_value=response),
        ):
            profile = negotiate_sender(timeout=1.0, status=lambda _message: None)

        self.assertIn(profile, candidates)
        self.assertEqual(len(played), 2)
        confirmation = decode_control_message(played[-1])
        self.assertEqual(confirmation.kind, CONTROL_PLAN)
        self.assertEqual(confirmation.profiles, (profile,))

    def test_receiver_handshake_proposes_and_accepts_plan(self):
        sounding = generate_sounding_sweep()
        candidates = rank_candidate_profiles(analyze_sounding_sweep(sounding))
        final = encode_control_message(CONTROL_PLAN, (candidates[0],))
        played = []

        with (
            patch("sonic_courier.live.play", side_effect=played.append),
            patch("sonic_courier.live.record_audio", side_effect=(sounding, final)),
        ):
            profile = negotiate_receiver(timeout=1.0, status=lambda _message: None)

        self.assertEqual(profile, candidates[0])
        self.assertEqual(len(played), 1)
        proposal = decode_control_message(played[0])
        self.assertEqual(proposal.kind, CONTROL_CANDIDATES)
        self.assertEqual(proposal.profiles, candidates)


if __name__ == "__main__":
    unittest.main()
