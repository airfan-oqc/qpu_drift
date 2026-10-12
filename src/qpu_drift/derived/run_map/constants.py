import re

ROUTINES = ("calibrate_1q", "calibrate_2q", "mixer_calibration", "benchmarking")

MORNING_HOURS = range(6, 12)
EVENING_HOURS = range(17, 24)

HW_PARAMS = {
    "calibrate_1q": {"freqs": "freq", "amps": "amp", "drags": "drag"},
    "calibrate_2q": {"amp": "amp", "cr_scale": "cr_scale", "cancel_scale": "cancel_scale"},
    "mixer_calibration": {
        "dc_bias_i": "dc_bias_i",
        "dc_bias_q": "dc_bias_q",
        "imbalance": "imbalance",
        "phase_offset": "phase_offset",
    },
}
COMPLEX_PARAMS = {"cr_scale", "cancel_scale"}

STEPS_1Q = {"freq": ("ramsey", "freq"), "amp": ("amp",), "drag": ("drag",)}  # writing steps (B2 section 2)

WARNINGS_1Q = [
    (re.compile(r"Fine-frequency calibration of Q(\d+) out of spec"), "freq"),
    (re.compile(r"Amplitude calibration out of spec for Q(\d+) with N="), "amp"),
    (re.compile(r"Amplitude calibration out of spec: Q(\d+) pulse amplitude"), "amp"),
    (re.compile(r"Q(\d+) pulse amp out of spec"), "amp"),
    (re.compile(r"Rabi calibration of Q(\d+) out of spec"), "amp"),
    (re.compile(r"DRAG calibration out of spec for Q(\d+) with N="), "drag"),
    (re.compile(r"Q(\d+) pulse DRAG out of spec"), "drag"),
]
WARNINGS_2Q = [
    re.compile(r"Reverting calibration for \((\d+), (\d+)\)"),
    re.compile(r"Amplitude too high on qubit pair \((\d+), (\d+)\)"),
]

DS2_TEMPLATES = (  # the DS2_PATHS params, plus measure frequency so channel_qubits sees readout channels
    "model.pulse_channels{Ch*.Q*.drive}.pulse_channel.frequency",
    "model.pulse_channels{Ch*.R*.measure}.pulse_channel.frequency",
    "model.quantum_devices{Q*}.pulse_hw_x_pi_2{amp}",
    "model.quantum_devices{Q*}.pulse_hw_x_pi_2{drag}",
    "model.quantum_devices{Q*}.pulse_hw_zx_pi_4{Q*}{amp}",
    "model.pulse_channels{Ch*.Q*.Q*.cross_resonance}.pulse_channel.scale",
    "model.pulse_channels{Ch*.Q*.Q*.cross_resonance_cancellation}.pulse_channel.scale",
    "model.physical_channels{Ch*}.dcbiaschannel_pair{I}._bias_value",
    "model.physical_channels{Ch*}.dcbiaschannel_pair{Q}._bias_value",
    "model.physical_channels{Ch*}.imbalance",
    "model.physical_channels{Ch*}.phase_offset",
)
DS2_PATHS = [
    (re.compile(r"model\.pulse_channels\{Ch\d+\.Q(?P<q>\d+)\.drive\}\.pulse_channel\.frequency"),
     "calibrate_1q", "freq"),
    (re.compile(r"model\.quantum_devices\{Q(?P<q>\d+)\}\.pulse_hw_x_pi_2\{amp\}"),
     "calibrate_1q", "amp"),
    (re.compile(r"model\.quantum_devices\{Q(?P<q>\d+)\}\.pulse_hw_x_pi_2\{drag\}"),
     "calibrate_1q", "drag"),
    (re.compile(r"model\.quantum_devices\{Q(?P<c>\d+)\}\.pulse_hw_zx_pi_4\{Q(?P<t>\d+)\}\{amp\}"),
     "calibrate_2q", "amp"),
    (re.compile(r"model\.pulse_channels\{Ch\d+\.Q(?P<c>\d+)\.Q(?P<t>\d+)\.cross_resonance\}"
                r"\.pulse_channel\.scale"),
     "calibrate_2q", "cr_scale"),
    (re.compile(r"model\.pulse_channels\{Ch\d+\.Q(?P<t>\d+)\.Q(?P<c>\d+)\.cross_resonance_cancellation\}"
                r"\.pulse_channel\.scale"),
     "calibrate_2q", "cancel_scale"),  # the cancellation channel sits on the target
    (re.compile(r"model\.physical_channels\{(?P<ch>Ch\d+)\}\.dcbiaschannel_pair\{I\}\._bias_value"),
     "mixer_calibration", "dc_bias_i"),
    (re.compile(r"model\.physical_channels\{(?P<ch>Ch\d+)\}\.dcbiaschannel_pair\{Q\}\._bias_value"),
     "mixer_calibration", "dc_bias_q"),
    (re.compile(r"model\.physical_channels\{(?P<ch>Ch\d+)\}\.imbalance"),
     "mixer_calibration", "imbalance"),
    (re.compile(r"model\.physical_channels\{(?P<ch>Ch\d+)\}\.phase_offset"),
     "mixer_calibration", "phase_offset"),
]
CHANNEL_OWNER = re.compile(r"(?P<ch>Ch\d+)\.[QR](?P<q>\d+)\.(drive|measure)")

RTOL = 1e-9
