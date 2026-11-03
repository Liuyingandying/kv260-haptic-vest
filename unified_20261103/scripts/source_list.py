"""Explicit build allowlist: legacy references must not enter the new design."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RTL_NAMES = [
    'perception_axi_adapter.sv', 'matrix_buffer.v', 'matrix_buffer_axi.v',
    'matrix_map_rom.v', 'duty_lut_rom.v', 'scheduler_fsm.v',
    'dirty_detector.v', 'block_refresh_buffer.v',
    'pca9685_burst_builder.v', 'tca9548_inject.v', 'failsafe_timer.v',
    'tactile_v12_ctrl.v', 'pca_init_sequencer.v', 'i2c_fault_monitor.v',
    'fault_recovery_ctrl.v', 'axi_status_regs.v',
    'i2c_master_100k_candidate.v', 'kv260_haptic_pipeline.v',
    'unified_perception_haptic.sv',
]
RTL_FILES = [ROOT / 'rtl' / name for name in RTL_NAMES]
