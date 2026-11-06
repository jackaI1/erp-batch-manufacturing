"""Known tones exercise FFT/envelope maths; fleet readings remain synthetic."""
import importlib.util
import math
import unittest


@unittest.skipUnless(importlib.util.find_spec('scipy'), 'install maintenance/requirements.txt for signal analysis')
class ConditionTests(unittest.TestCase):
    def test_envelope_finds_repeating_bearing_impacts(self):
        import numpy as np
        from maintenance.condition import vibration
        fs = 12000
        t = np.arange(fs) / fs
        signal = (1 + 0.8 * np.cos(2 * np.pi * 160 * t)) * np.sin(2 * np.pi * 3000 * t)
        result = vibration(signal, fs, 160)
        self.assertAlmostEqual(result['fft_peak_hz'], 3000, delta=2)
        self.assertAlmostEqual(result['envelope_peak_hz'], 160, delta=2)
        self.assertGreater(result['bearing_band_fraction'], 0.8)

    def test_rejects_unusable_or_nonfinite_waveforms(self):
        from maintenance.condition import vibration
        for waveform in ([1, 2], [float('nan')] * 2000, [0] * 2000):
            with self.assertRaises(ValueError):
                vibration(waveform, 12000, 160)

    def test_thermography_and_oil_trends_flag_thresholds_and_slope(self):
        from maintenance.condition import trends
        result = trends([
            dict(date='2026-01-01', bearing_c=50, ambient_c=20, iron_ppm=10, viscosity_cst=100),
            dict(date='2026-02-01', bearing_c=80, ambient_c=20, iron_ppm=70, viscosity_cst=130)])
        self.assertTrue(result['thermal_alert'])
        self.assertTrue(result['oil_alert'])
        self.assertGreater(result['iron_ppm_per_day'], 0)
        self.assertEqual(result['temperature_rise_c'], 60)

    def test_trends_require_chronology_and_finite_data(self):
        from maintenance.condition import trends
        with self.assertRaises(ValueError):
            trends([dict(date='2026-02-01'), dict(date='2026-01-01')])
