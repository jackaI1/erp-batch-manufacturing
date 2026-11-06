"""Transparent screening, not a remaining-life model or safety diagnosis."""
from datetime import date
from .fleet import number


def vibration(samples, sample_rate, bearing_hz):
    import numpy as np
    from scipy.signal import butter, hilbert, sosfiltfilt
    sample_rate = number(sample_rate, 'sample_rate', 10001)
    bearing_hz = number(bearing_hz, 'bearing_hz', 1)
    x = np.asarray(samples, dtype=float)
    if x.ndim != 1 or len(x) < 2048 or not np.isfinite(x).all() or np.std(x) == 0:
        raise ValueError('Need at least 2048 finite, nonconstant accelerometer samples')
    x = x - x.mean()
    window = np.hanning(len(x))
    frequency = np.fft.rfftfreq(len(x), 1 / sample_rate)
    spectrum = np.abs(np.fft.rfft(x * window))
    band = sosfiltfilt(butter(4, [2000, 5000], btype='bandpass', fs=sample_rate, output='sos'), x)
    envelope = np.abs(hilbert(band))
    envelope -= envelope.mean()
    amplitude = np.abs(np.fft.rfft(envelope * window))
    low = (frequency >= 20) & (frequency <= 1000)
    target = (np.abs(frequency - bearing_hz) <= 5)
    total = float(np.sum(amplitude[low] ** 2))
    fraction = float(np.sum(amplitude[target] ** 2) / total) if total else 0
    return dict(samples=len(x), sample_rate_hz=sample_rate, fft_peak_hz=float(frequency[1 + np.argmax(spectrum[1:])]), envelope_peak_hz=float(frequency[low][np.argmax(amplitude[low])]), expected_inner_race_hz=bearing_hz, bearing_band_fraction=fraction, rms_g=float(np.sqrt(np.mean(x ** 2))), screening_flag=fraction > 0.05, method='Hann FFT; 2-5 kHz Butterworth bandpass; Hilbert envelope; +/-5 Hz target energy / 20-1000 Hz envelope energy')


def trends(readings):
    if len(readings) < 2:
        raise ValueError('Need at least two dated readings')
    dates = [date.fromisoformat(r['date']) for r in readings]
    if any(b <= a for a, b in zip(dates, dates[1:])):
        raise ValueError('Reading dates must increase')
    normalized = []
    for row in readings:
        normalized.append({key: number(row.get(key), key, -273.15 if key.endswith('_c') else 0) for key in ('bearing_c', 'ambient_c', 'iron_ppm', 'viscosity_cst')})
    first, last = normalized[0], normalized[-1]
    rise = last['bearing_c'] - last['ambient_c']
    viscosity_change = (last['viscosity_cst'] / first['viscosity_cst'] - 1) if first['viscosity_cst'] else None
    return dict(temperature_rise_c=rise, thermal_alert=rise > 40, iron_ppm_per_day=(last['iron_ppm'] - first['iron_ppm']) / (dates[-1] - dates[0]).days, oil_alert=last['iron_ppm'] > 50 or viscosity_change is None or abs(viscosity_change) > 0.2, viscosity_fraction_change=viscosity_change, limitation='Illustrative thresholds on synthetic thermal/oil readings; no remaining-life or vessel-safety prediction')
