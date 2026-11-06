"""Download two small public CWRU recordings on CI and retain only results.

No model training or fleet health claims: this compares a normal bench bearing
with an intentionally damaged inner race at zero motor load.
"""
import argparse
import hashlib
from io import BytesIO
import json
from pathlib import Path
from urllib.request import urlopen
from .condition import vibration, trends

SOURCE = 'https://engineering.case.edu/sites/default/files/'
MAX_BYTES = 8 * 1024 * 1024


def analyze_public():
    from scipy.io import loadmat
    from scipy.signal import resample_poly
    result = {}
    for file, label, rate in [('97.mat', 'normal', 48000), ('105.mat', 'inner_race_fault', 12000)]:
        with urlopen(SOURCE + file, timeout=90) as response:
            raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError('Public recording exceeded download limit')
        data = loadmat(BytesIO(raw))
        key = next(k for k in data if k.endswith('DE_time'))
        samples = data[key].reshape(-1)[:rate * 2]
        if rate == 48000:
            samples = resample_poly(samples, 1, 4)
        rpm_key = next((k for k in data if k.endswith('RPM')), None)
        rpm = float(data[rpm_key].reshape(-1)[0]) if rpm_key else 1797
        row = vibration(samples, 12000, rpm / 60 * 5.4152)
        row.update(source=SOURCE + file, source_sha256=hashlib.sha256(raw).hexdigest(), source_bytes=len(raw), label=label, rpm=rpm)
        result[label] = row
        del raw, data, samples
    result['comparison'] = dict(fault_fraction_greater=result['inner_race_fault']['bearing_band_fraction'] > result['normal']['bearing_band_fraction'], labelled_cases_match=result['inner_race_fault']['screening_flag'] and not result['normal']['screening_flag'], limitation='Two labelled bench recordings only, not a validated classifier or remaining-life prediction.')
    result['synthetic_thermal_oil'] = trends([
        dict(date='2026-09-01', bearing_c=52, ambient_c=22, iron_ppm=12, viscosity_cst=100),
        dict(date='2026-10-01', bearing_c=61, ambient_c=21, iron_ppm=28, viscosity_cst=108),
        dict(date='2026-11-01', bearing_c=83, ambient_c=20, iron_ppm=65, viscosity_cst=125)])
    if not result['comparison']['fault_fraction_greater'] or not result['comparison']['labelled_cases_match']:
        raise AssertionError('Reference envelope screening disagrees: normal fraction=' + str(result['normal']['bearing_band_fraction']) + ', fault fraction=' + str(result['inner_race_fault']['bearing_band_fraction']))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='artifacts/maintenance/vibration.json')
    args = parser.parse_args()
    result = analyze_public()
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: {'bearing_band_fraction': result[k]['bearing_band_fraction'], 'envelope_peak_hz': result[k]['envelope_peak_hz']} for k in ('normal', 'inner_race_fault')}))


if __name__ == '__main__':
    main()
