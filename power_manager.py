#!/usr/bin/env python3
"""
Enhanced Power Manager for Fedora/Nobara Linux
An intelligent power management system that optimizes display refresh rates,
GPU performance, and system power consumption based on battery status.

Features:
- Dynamic refresh rate switching (24Hz to 240Hz+)
- Intelligent battery management
- GPU power state optimization
- OLED panel optimizations
- Real-time performance monitoring
"""

import os
import sys
import time
import logging
import subprocess
import re
import glob
from typing import List, Dict, Optional, Tuple, Any
from dataclasses import dataclass
from enum import Enum, auto
import psutil
import configparser

# Set up logging
LOG_FILE = "/var/log/power-manager.log"
METRICS_FILE = "/var/log/power-manager-metrics.log"

class LogLevel(Enum):
    DEBUG = logging.DEBUG
    INFO = logging.INFO
    WARNING = logging.WARNING
    ERROR = logging.ERROR

class Logger:
    def __init__(self, name: str = "PowerManager"):
        self.logger = logging.getLogger(name)
        self.logger.setLevel(logging.DEBUG)
        
        # File handler
        file_handler = logging.FileHandler(LOG_FILE)
        file_handler.setLevel(logging.DEBUG)
        
        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        
        # Formatter
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        file_handler.setFormatter(formatter)
        console_handler.setFormatter(formatter)
        
        # Add handlers
        self.logger.addHandler(file_handler)
        self.logger.addHandler(console_handler)
    
    def log(self, message: str, level: LogLevel = LogLevel.INFO):
        self.logger.log(level.value, message)

logger = Logger()

class PowerMode(Enum):
    PERFORMANCE = auto()
    BALANCED = auto()
    POWER_SAVER = auto()
    ULTRA_POWER_SAVER = auto()

class PanelType(Enum):
    UNKNOWN = "unknown"
    LCD = "lcd"
    LED = "led"
    OLED = "oled"
    IPS = "ips"
    TN = "tn"
    VA = "va"
    QLED = "qled"

@dataclass
class DisplayInfo:
    available_rates: List[float]
    low_power_rates: List[float]
    max_rate: float
    min_rate: float
    vrr_supported: bool
    panel_type: PanelType
    variable_backlight: bool
    power_saving_features: List[str]
    cabc_supported: bool

@dataclass
class BatteryStatus:
    level: int
    charging_state: str
    time_remaining: str
    power_consumption: float

@dataclass
class SystemConfig:
    force_refresh_rate: Optional[int] = None
    disable_vrr: bool = False
    ultra_battery_threshold: int = 15
    enable_oled_optimizations: bool = False
    debug_mode: bool = False

class PowerManager:
    def __init__(self):
        self.config = self._load_config()
        self.display_info = DisplayInfo(
            available_rates=[],
            low_power_rates=[],
            max_rate=60.0,
            min_rate=60.0,
            vrr_supported=False,
            panel_type=PanelType.UNKNOWN,
            variable_backlight=False,
            power_saving_features=[],
            cabc_supported=False
        )
        self.battery_status = BatteryStatus(
            level=100,
            charging_state="unknown",
            time_remaining="",
            power_consumption=0.0
        )
        
        # Initialize system
        self._initialize_system()
    
    def _load_config(self) -> SystemConfig:
        """Load configuration from environment variables and config file"""
        config = SystemConfig()
        
        # Load from environment variables
        if "FORCE_REFRESH_RATE" in os.environ:
            config.force_refresh_rate = int(os.environ["FORCE_REFRESH_RATE"])
        if "DISABLE_VRR" in os.environ:
            config.disable_vrr = bool(os.environ["DISABLE_VRR"])
        if "ULTRA_BATTERY_THRESHOLD" in os.environ:
            config.ultra_battery_threshold = int(os.environ["ULTRA_BATTERY_THRESHOLD"])
        if "ENABLE_OLED_OPTIMIZATIONS" in os.environ:
            config.enable_oled_optimizations = bool(os.environ["ENABLE_OLED_OPTIMIZATIONS"])
        if "DEBUG" in os.environ:
            config.debug_mode = bool(os.environ["DEBUG"])
        
        # Load from config file
        config_file = "/etc/power-manager.conf"
        if os.path.exists(config_file):
            parser = configparser.ConfigParser()
            parser.read(config_file)
            
            if "Settings" in parser:
                if "force_refresh_rate" in parser["Settings"]:
                    config.force_refresh_rate = int(parser["Settings"]["force_refresh_rate"])
                if "disable_vrr" in parser["Settings"]:
                    config.disable_vrr = parser["Settings"].getboolean("disable_vrr")
                if "ultra_battery_threshold" in parser["Settings"]:
                    config.ultra_battery_threshold = int(parser["Settings"]["ultra_battery_threshold"])
                if "enable_oled_optimizations" in parser["Settings"]:
                    config.enable_oled_optimizations = parser["Settings"].getboolean("enable_oled_optimizations")
                if "debug_mode" in parser["Settings"]:
                    config.debug_mode = parser["Settings"].getboolean("debug_mode")
        
        return config
    
    def _initialize_system(self):
        """Initialize the system and detect capabilities"""
        logger.log("Starting enhanced power management system...")
        
        # Create log directory if it doesn't exist
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        os.makedirs(os.path.dirname(METRICS_FILE), exist_ok=True)
        
        # Detect display capabilities
        self.detect_display_capabilities()
        
        # Get optimal refresh rates
        self.get_optimal_refresh_rates()
        
        logger.log("System initialization completed")
    
    def detect_display_capabilities(self):
        """Detect display capabilities using multiple methods"""
        logger.log("Detecting enhanced display capabilities...")
        
        display_info = DisplayInfo(
            available_rates=[],
            low_power_rates=[],
            max_rate=60.0,
            min_rate=60.0,
            vrr_supported=False,
            panel_type=PanelType.UNKNOWN,
            variable_backlight=False,
            power_saving_features=[],
            cabc_supported=False
        )
        
        # Method 1: Try gnome-randr
        display_info = self._detect_with_gnome_randr(display_info)
        
        # Method 2: Try xrandr
        display_info = self._detect_with_xrandr(display_info)
        
        # Method 3: Try DRM/sysfs
        display_info = self._detect_with_drm(display_info)
        
        # Detect low refresh rates
        display_info = self._detect_low_refresh_rates(display_info)
        
        # Detect power saving modes
        display_info = self._detect_power_saving_modes(display_info)
        
        self.display_info = display_info
        
        logger.log(f"Display detection results:")
        logger.log(f"- Refresh rate range: {display_info.min_rate}Hz - {display_info.max_rate}Hz")
        logger.log(f"- VRR supported: {display_info.vrr_supported}")
        logger.log(f"- Panel type: {display_info.panel_type.value}")
        logger.log(f"- Variable backlight: {display_info.variable_backlight}")
        logger.log(f"- Low power rates: {display_info.low_power_rates}")
        logger.log(f"- Power saving features: {display_info.power_saving_features}")
    
    def _detect_with_gnome_randr(self, display_info: DisplayInfo) -> DisplayInfo:
        """Detect display capabilities using gnome-randr"""
        try:
            result = subprocess.run(["gnome-randr", "--verbose"], 
                                  capture_output=True, text=True, timeout=5)
            if result.returncode == 0 and result.stdout:
                output = result.stdout
                
                # Extract refresh rates
                rates = re.findall(r'(d+.?d*)Hz', output)
                if rates:
                    display_info.available_rates = sorted([float(r) for r in rates])
                    display_info.min_rate = min(display_info.available_rates)
                    display_info.max_rate = max(display_info.available_rates)
                
                # Check VRR support
                if re.search(r'vrr|variable.*refresh|adaptive.*sync|freesync|g-sync', 
                           output, re.IGNORECASE):
                    display_info.vrr_supported = True
                
                # Determine panel type
                panel_match = re.search(r'(oled|lcd|led|ips|tn|va|qled)', output, re.IGNORECASE)
                if panel_match:
                    panel_type = panel_match.group(1).lower()
                    display_info.panel_type = PanelType(panel_type)
                
                logger.log("gnome-randr detection successful")
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, FileNotFoundError) as e:
            logger.log(f"gnome-randr detection failed: {e}", LogLevel.DEBUG)
        
        return display_info
    
    def _detect_with_xrandr(self, display_info: DisplayInfo) -> DisplayInfo:
        """Detect display capabilities using xrandr"""
        try:
            result = subprocess.run(["xrandr", "--verbose"], 
                                  capture_output=True, text=True, timeout=5)
            if result.returncode == 0 and result.stdout:
                output = result.stdout
                
                # Extract refresh rates (look for preferred/active rates)
                rates = re.findall(r'(d+.?d*)(?:s*(*|+|preferred))', output)
                if rates:
                    display_info.available_rates = sorted([float(r[0]) for r in rates])
                    display_info.min_rate = min(display_info.available_rates)
                    display_info.max_rate = max(display_info.available_rates)
                
                # Check variable brightness support
                if re.search(r'brightness|backlight', output, re.IGNORECASE):
                    display_info.variable_backlight = True
                
                logger.log("xrandr detection successful")
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, FileNotFoundError) as e:
            logger.log(f"xrandr detection failed: {e}", LogLevel.DEBUG)
        
        return display_info
    
    def _detect_with_drm(self, display_info: DisplayInfo) -> DisplayInfo:
        """Detect display capabilities using DRM/sysfs"""
        try:
            # Look for DRM card devices
            drm_cards = glob.glob("/sys/class/drm/card*/")
            
            for card in drm_cards:
                connectors = glob.glob(f"{card}/*-*/")
                
                for connector in connectors:
                    modes_file = f"{connector}/modes"
                    edid_file = f"{connector}/edid"
                    
                    if os.path.exists(modes_file) and os.path.exists(edid_file):
                        with open(modes_file, 'r') as f:
                            modes = f.read()
                        
                        if modes:
                            # Parse modelines to get exact frequencies
                            rates = []
                            for line in modes.split('
'):
                                match = re.search(r'(d+)x(d+)@(d+.?d*)', line)
                                if match:
                                    rates.append(float(match.group(3)))
                            
                            if rates:
                                display_info.available_rates = sorted(rates)
                                display_info.min_rate = min(rates)
                                display_info.max_rate = max(rates)
                        
                        # Analyze EDID for additional information
                        if os.path.exists(edid_file) and os.path.getsize(edid_file) > 0:
                            display_info = self._analyze_edid_capabilities(edid_file, display_info)
                        
                        break
        except Exception as e:
            logger.log(f"DRM detection failed: {e}", LogLevel.DEBUG)
        
        return display_info
    
    def _analyze_edid_capabilities(self, edid_file: str, display_info: DisplayInfo) -> DisplayInfo:
        """Analyze EDID data for display capabilities"""
        try:
            if not os.path.exists("/usr/bin/edid-decode"):
                return display_info
            
            result = subprocess.run(["edid-decode", edid_file], 
                                  capture_output=True, text=True, timeout=5)
            if result.returncode == 0 and result.stdout:
                edid_info = result.stdout
                
                # Look for low frequency support
                min_rate_match = re.search(r'minimum.*?(d+).*?hz', edid_info, re.IGNORECASE)
                if min_rate_match:
                    min_rate = int(min_rate_match.group(1))
                    logger.log(f"EDID minimum refresh rate: {min_rate}Hz")
                
                # Check panel type
                if re.search(r'oled', edid_info, re.IGNORECASE):
                    display_info.panel_type = PanelType.OLED
                    logger.log("OLED panel detected - optimizing for burn-in prevention")
                elif re.search(r'lcd.*led', edid_info, re.IGNORECASE):
                    display_info.panel_type = PanelType.LED
                
                # Detect CABC support
                if re.search(r'cabc|adaptive.*backlight', edid_info, re.IGNORECASE):
                    display_info.cabc_supported = True
                    logger.log("CABC (Content Adaptive Backlight Control) supported")
        except Exception as e:
            logger.log(f"EDID analysis failed: {e}", LogLevel.DEBUG)
        
        return display_info
    
    def _detect_low_refresh_rates(self, display_info: DisplayInfo) -> DisplayInfo:
        """Test support for low refresh rates"""
        test_rates = [24, 25, 30, 48, 50]
        supported_low_rates = []
        
        logger.log("Testing support for low refresh rates...")
        
        # Get active display
        display = self._get_active_display()
        if not display:
            logger.log("Warning: Could not detect active display for low rate testing")
            return display_info
        
        # Test each low frequency
        for rate in test_rates:
            try:
                result = subprocess.run([
                    "xrandr", "--output", display, 
                    "--mode", "auto", "--rate", str(rate), "--dryrun"
                ], capture_output=True, text=True, timeout=3)
                
                if result.returncode == 0:
                    supported_low_rates.append(rate)
                    logger.log(f"Low refresh rate {rate} Hz: SUPPORTED")
                else:
                    logger.log(f"Low refresh rate {rate} Hz: not supported")
            except Exception as e:
                logger.log(f"Failed to test rate {rate}Hz: {e}", LogLevel.DEBUG)
        
        # Check VRR minimum rate
        try:
            vrr_min_file = "/sys/class/drm/card0/card0-eDP-1/vrr_range"
            if os.path.exists(vrr_min_file):
                with open(vrr_min_file, 'r') as f:
                    vrr_range = f.read()
                    vrr_min = int(vrr_range.split('-')[0])
                    if vrr_min < 60:
                        logger.log(f"VRR minimum rate detected: {vrr_min}Hz")
                        supported_low_rates.append(vrr_min)
        except Exception as e:
            logger.log(f"VRR range detection failed: {e}", LogLevel.DEBUG)
        
        display_info.low_power_rates = sorted(supported_low_rates)
        return display_info
    
    def _detect_power_saving_modes(self, display_info: DisplayInfo) -> DisplayInfo:
        """Detect available power saving features"""
        power_saving_features = []
        
        # Check Panel Self Refresh (PSR)
        psr_status_file = "/sys/kernel/debug/dri/0/i915_edp_psr_status"
        if os.path.exists(psr_status_file):
            try:
                with open(psr_status_file, 'r') as f:
                    if "enabled" in f.read():
                        power_saving_features.append("PSR")
                        logger.log("Panel Self Refresh (PSR) available")
            except Exception as e:
                logger.log(f"PSR detection failed: {e}", LogLevel.DEBUG)
        
        # Check Frame Buffer Compression (FBC)
        fbc_status_file = "/sys/kernel/debug/dri/0/i915_fbc_status"
        if os.path.exists(fbc_status_file):
            try:
                with open(fbc_status_file, 'r') as f:
                    if "enabled" in f.read() or "supported" in f.read():
                        power_saving_features.append("FBC")
                        logger.log("Frame Buffer Compression (FBC) available")
            except Exception as e:
                logger.log(f"FBC detection failed: {e}", LogLevel.DEBUG)
        
        # Check DPMS
        try:
            result = subprocess.run(["xset", "q"], capture_output=True, text=True, timeout=3)
            if result.returncode == 0 and "DPMS is Enabled" in result.stdout:
                power_saving_features.append("DPMS")
                logger.log("DPMS power management available")
        except Exception as e:
            logger.log(f"DPMS detection failed: {e}", LogLevel.DEBUG)
        
        display_info.power_saving_features = power_saving_features
        return display_info
    
    def get_optimal_refresh_rates(self):
        """Determine optimal refresh rates for different power modes"""
        max_rate = self.display_info.max_rate
        min_rate = self.display_info.min_rate
        battery_rate = 60
        ac_rate = max_rate
        ultra_battery_rate = 30
        
        logger.log("Selecting optimal refresh rates...")
        
        if self.display_info.available_rates:
            rates = self.display_info.available_rates
            low_rates = self.display_info.low_power_rates
            
            # Ultra power-saving frequency selection
            if low_rates:
                if 30 in low_rates:
                    ultra_battery_rate = 30
                elif 24 in low_rates:
                    ultra_battery_rate = 24
                elif 48 in low_rates:
                    ultra_battery_rate = 48
                else:
                    ultra_battery_rate = min(low_rates)
            
            # Regular battery frequency selection
            if 60 in rates:
                battery_rate = 60
            elif 50 in rates:
                battery_rate = 50
            else:
                # Find closest frequency to 60Hz
                closest_to_60 = min(rates, key=lambda x: abs(x - 60))
                battery_rate = closest_to_60 if closest_to_60 <= 120 else 60
            
            # For AC: use maximum available frequency
            ac_rate = max(rates)
        
        # Panel type adaptation
        if self.display_info.panel_type == PanelType.OLED:
            # For OLED prefer variable frequencies for burn-in prevention
            if self.display_info.vrr_supported:
                logger.log("OLED detected: enabling VRR for burn-in protection")
            # For OLED battery mode can be even lower
            if 24 in self.display_info.low_power_rates:
                ultra_battery_rate = 24
        
        # Store optimal rates in config
        self.config.optimal_battery_rate = battery_rate
        self.config.optimal_ac_rate = ac_rate
        self.config.optimal_ultra_battery_rate = ultra_battery_rate
        
        logger.log("Optimal rates selected:")
        logger.log(f"- Standard battery: {battery_rate}Hz")
        logger.log(f"- Ultra battery saver: {ultra_battery_rate}Hz")
        logger.log(f"- AC power: {ac_rate}Hz")
    
    def _get_active_display(self) -> Optional[str]:
        """Get the active display name"""
        try:
            result = subprocess.run(["xrandr"], capture_output=True, text=True, timeout=3)
            if result.returncode == 0:
                # Look for connected displays, prefer primary
                for line in result.stdout.split('
'):
                    if ' connected primary' in line:
                        return line.split()[0]
                    elif ' connected' in line and not ' disconnected' in line:
                        return line.split()[0]
        except Exception as e:
            logger.log(f"Failed to get active display: {e}", LogLevel.DEBUG)
        return None
    
    def get_enhanced_battery_status(self) -> BatteryStatus:
        """Get detailed battery information"""
        battery_level = 100
        charging_state = "unknown"
        time_remaining = ""
        power_consumption = 0.0
        
        try:
            # Use acpi for battery info
            result = subprocess.run(["acpi", "-b"], capture_output=True, text=True, timeout=3)
            if result.returncode == 0 and result.stdout:
                battery_info = result.stdout.strip().split('
')[0]
                
                # Extract battery level
                level_match = re.search(r'(d+)(?=%)', battery_info)
                if level_match:
                    battery_level = int(level_match.group(1))
                
                # Determine charging state
                if "Charging" in battery_info:
                    charging_state = "charging"
                elif "Discharging" in battery_info:
                    charging_state = "discharging"
                elif "Full" in battery_info:
                    charging_state = "full"
                
                # Extract time remaining
                time_match = re.search(r'(d+:d+:d+)', battery_info)
                if time_match:
                    time_remaining = time_match.group(1)
        except Exception as e:
            logger.log(f"ACPI battery detection failed: {e}", LogLevel.DEBUG)
        
        try:
            # Get power consumption from sysfs
            power_files = glob.glob("/sys/class/power_supply/BAT*/power_now")
            if power_files:
                with open(power_files[0], 'r') as f:
                    power_microwatts = int(f.read().strip())
                    power_consumption = power_microwatts / 1000000  # Convert to watts
        except Exception as e:
            logger.log(f"Power consumption detection failed: {e}", LogLevel.DEBUG)
        
        self.battery_status = BatteryStatus(
            level=battery_level,
            charging_state=charging_state,
            time_remaining=time_remaining,
            power_consumption=power_consumption
        )
        
        return self.battery_status
    
    def intelligent_battery_mode_selection(self) -> str:
        """Intelligently select battery mode based on current status"""
        self.get_enhanced_battery_status()
        
        selected_mode = "standard"
        battery_level = self.battery_status.level
        power_consumption = self.battery_status.power_consumption
        time_remaining = self.battery_status.time_remaining
        
        # Mode selection logic
        if battery_level < self.config.ultra_battery_threshold:
            selected_mode = "ultra"
            logger.log(f"Critical battery level ({battery_level}%), using ultra power saving")
        elif battery_level < 50:
            # Check power consumption
            if power_consumption > 15:
                selected_mode = "ultra"
                logger.log(f"High power consumption ({power_consumption}W) at {battery_level}%, using ultra mode")
        
        # Consider remaining time
        if time_remaining:
            hours = int(time_remaining.split(':')[0])
            if hours < 2:
                selected_mode = "ultra"
                logger.log(f"Low remaining time ({time_remaining}), switching to ultra mode")
        
        return selected_mode
    
    def get_power_status(self) -> bool:
        """Check if system is on AC power (True) or battery (False)"""
        try:
            # Check AC power supply
            ac_files = glob.glob("/sys/class/power_supply/AC*/online")
            if ac_files:
                with open(ac_files[0], 'r') as f:
                    status = f.read().strip()
                    return status == "1"
            
            # Check ADP (some systems use this)
            adp_files = glob.glob("/sys/class/power_supply/ADP*/online")
            if adp_files:
                with open(adp_files[0], 'r') as f:
                    status = f.read().strip()
                    return status == "1"
        except Exception as e:
            logger.log(f"Power status detection failed: {e}", LogLevel.DEBUG)
        
        # Default to battery mode if uncertain
        return False
    
    def set_performance_mode(self, mode: str):
        """Set system performance mode"""
        try:
            if mode == "performance":
                power_mode = "performance"
            elif mode == "balanced":
                power_mode = "balanced"
            else:  # power-saver or ultra
                power_mode = "power-saver"
            
            subprocess.run(["powerprofilesctl", "set", power_mode], 
                         capture_output=True, timeout=3)
            logger.log(f"Performance mode set to: {power_mode}")
        except Exception as e:
            logger.log(f"Failed to set performance mode: {e}", LogLevel.DEBUG)
    
    def set_display_refresh_rate(self, rate: float, vrr_mode: str = "off"):
        """Set display refresh rate"""
        display = self._get_active_display()
        if not display:
            logger.log("No active display found", LogLevel.WARNING)
            return
        
        try:
            # Set refresh rate
            result = subprocess.run([
                "xrandr", "--output", display, "--rate", str(rate)
            ], capture_output=True, text=True, timeout=3)
            
            if result.returncode == 0:
                logger.log(f"Display refresh rate set to: {rate}Hz")
            else:
                logger.log(f"Failed to set refresh rate: {result.stderr}", LogLevel.WARNING)
            
            # Configure VRR if supported
            if self.display_info.vrr_supported and vrr_mode in ["on", "off"]:
                vrr_enabled = 1 if vrr_mode == "on" else 0
                vrr_file = "/sys/class/drm/card0/card0-eDP-1/vrr_enabled"
                if os.path.exists(vrr_file):
                    try:
                        with open(vrr_file, 'w') as f:
                            f.write(str(vrr_enabled))
                        logger.log(f"VRR mode set to: {vrr_mode}")
                    except Exception as e:
                        logger.log(f"Failed to set VRR mode: {e}", LogLevel.DEBUG)
        except Exception as e:
            logger.log(f"Failed to set display refresh rate: {e}", LogLevel.WARNING)
    
    def apply_enhanced_battery_settings(self, mode: str):
        """Apply enhanced battery power settings"""
        logger.log(f"Applying enhanced battery power settings (mode: {mode})")
        
        # Basic settings
        self.set_performance_mode("power-saver")
        
        # Select refresh rate based on mode
        target_rate = self.config.optimal_battery_rate
        if mode == "ultra":
            target_rate = self.config.optimal_ultra_battery_rate
            logger.log(f"Ultra battery mode: using {target_rate}Hz")
        
        self.set_display_refresh_rate(target_rate, "off")
        
        # Aggressive brightness management
        self._set_brightness(40 if mode == "standard" else 20)
        
        # Enable CABC if available
        if self.display_info.cabc_supported:
            self.enable_cabc_mode("aggressive" if mode == "ultra" else "normal")
        
        # Configure DPMS
        self._configure_dpms(mode)
        
        # GPU configuration
        self.configure_gpu_power_saving(mode)
        
        # Panel type optimization
        if self.display_info.panel_type == PanelType.OLED:
            self.apply_oled_optimizations("battery")
        
        # Enable Panel Self Refresh if available
        if "PSR" in self.display_info.power_saving_features:
            self._enable_psr()
        
        # Disable non-essential services in ultra mode
        if mode == "ultra":
            self._disable_non_essential_services()
    
    def _set_brightness(self, level: int):
        """Set display brightness"""
        try:
            subprocess.run(["brightnessctl", "set", f"{level}%"], 
                         capture_output=True, timeout=3)
            logger.log(f"Brightness set to {level}%")
        except Exception as e:
            logger.log(f"Failed to set brightness: {e}", LogLevel.DEBUG)
    
    def enable_cabc_mode(self, mode: str):
        """Enable Content Adaptive Backlight Control"""
        try:
            backlight_dirs = glob.glob("/sys/class/backlight/*/")
            for backlight_dir in backlight_dirs:
                cabc_modes_file = f"{backlight_dir}cabc_available_modes"
                cabc_mode_file = f"{backlight_dir}cabc_mode"
                
                if os.path.exists(cabc_modes_file) and os.path.exists(cabc_mode_file):
                    with open(cabc_modes_file, 'r') as f:
                        available_modes = f.read().strip()
                    
                    if mode in available_modes:
                        with open(cabc_mode_file, 'w') as f:
                            f.write(mode)
                        logger.log(f"CABC mode set to: {mode}")
                        return
            
            logger.log(f"CABC mode {mode} not available on this system")
        except Exception as e:
            logger.log(f"Failed to set CABC mode: {e}", LogLevel.DEBUG)
    
    def _configure_dpms(self, mode: str):
        """Configure DPMS power management"""
        try:
            if mode == "ultra":
                # Very fast shutdown: 60s standby, 120s suspend, 180s off
                subprocess.run(["xset", "dpms", "60", "120", "180"], 
                             capture_output=True, timeout=3)
            else:
                # Standard shutdown: 120s standby, 240s suspend, 300s off
                subprocess.run(["xset", "dpms", "120", "240", "300"], 
                             capture_output=True, timeout=3)
            logger.log(f"DPMS power management configured for {mode} mode")
        except Exception as e:
            logger.log(f"Failed to configure DPMS: {e}", LogLevel.DEBUG)
    
    def configure_gpu_power_saving(self, mode: str):
        """Configure GPU power saving settings"""
        try:
            # Intel GPU settings
            intel_boost_file = "/sys/class/drm/card0/gt_boost_freq_mhz"
            if os.path.exists(intel_boost_file):
                with open(intel_boost_file, 'r') as f:
                    current_boost = int(f.read().strip())
                
                if mode == "ultra":
                    # Reduce GPU boost frequency by 20%
                    new_boost = int(current_boost * 0.8)
                    with open(intel_boost_file, 'w') as f:
                        f.write(str(new_boost))
                    logger.log(f"GPU boost frequency reduced to {new_boost}MHz")
            
            # AMD GPU settings
            amd_power_file = "/sys/class/drm/card0/device/power_dpm_force_performance_level"
            if os.path.exists(amd_power_file):
                if mode == "ultra":
                    with open(amd_power_file, 'w') as f:
                        f.write("low")
                    logger.log("AMD GPU forced to low performance level")
        except Exception as e:
            logger.log(f"GPU power saving configuration failed: {e}", LogLevel.DEBUG)
    
    def apply_oled_optimizations(self, power_mode: str):
        """Apply OLED-specific optimizations"""
        logger.log(f"Applying OLED-specific optimizations for {power_mode} mode")
        
        try:
            # Enable pixel shifting for burn-in prevention
            content_protection_file = "/sys/class/drm/card0/card0-eDP-1/content_protection"
            if os.path.exists(content_protection_file):
                with open(content_protection_file, 'w') as f:
                    f.write("Desired")
            
            # Configure GNOME for OLED
            if power_mode == "battery":
                subprocess.run([
                    "gsettings", "set", 
                    "org.gnome.desktop.interface", "gtk-theme", "Adwaita-Dark"
                ], capture_output=True, timeout=3)
                subprocess.run([
                    "gsettings", "set", 
                    "org.gnome.desktop.interface", "color-scheme", "prefer-dark"
                ], capture_output=True, timeout=3)
                
                # Reduce screensaver timeout for OLED
                subprocess.run([
                    "gsettings", "set", 
                    "org.gnome.desktop.screensaver", "idle-activation-enabled", "true"
                ], capture_output=True, timeout=3)
                subprocess.run([
                    "gsettings", "set", 
                    "org.gnome.desktop.session", "idle-delay", "180"
                ], capture_output=True, timeout=3)
                
            logger.log("OLED optimizations applied")
        except Exception as e:
            logger.log(f"OLED optimizations failed: {e}", LogLevel.DEBUG)
    
    def _enable_psr(self):
        """Enable Panel Self Refresh"""
        try:
            psr_debug_file = "/sys/kernel/debug/dri/0/i915_edp_psr_debug"
            if os.path.exists(psr_debug_file):
                with open(psr_debug_file, 'w') as f:
                    f.write("1")
                logger.log("Panel Self Refresh enabled")
        except Exception as e:
            logger.log(f"Failed to enable PSR: {e}", LogLevel.DEBUG)
    
    def _disable_non_essential_services(self):
        """Disable non-essential services in ultra power saving mode"""
        try:
            services = ["evolution-data-server", "tracker-miner-fs"]
            for service in services:
                subprocess.run(["systemctl", "--user", "stop", service], 
                             capture_output=True, timeout=3)
            logger.log("Non-essential services disabled")
        except Exception as e:
            logger.log(f"Failed to disable services: {e}", LogLevel.DEBUG)
    
    def apply_ac_settings(self):
        """Apply AC power settings"""
        logger.log("Applying AC power settings")
        
        # Set high performance mode
        self.set_performance_mode("performance")
        
        # Use maximum refresh rate
        self.set_display_refresh_rate(self.config.optimal_ac_rate, "on")
        
        # Reset brightness to maximum
        self._set_brightness(100)
        
        # Disable aggressive DPMS
        try:
            subprocess.run(["xset", "dpms", "600", "900", "1200"], 
                         capture_output=True, timeout=3)
            logger.log("DPMS configured for AC power")
        except Exception as e:
            logger.log(f"Failed to configure DPMS for AC: {e}", LogLevel.DEBUG)
        
        # Reset GPU performance
        try:
            amd_power_file = "/sys/class/drm/card0/device/power_dpm_force_performance_level"
            if os.path.exists(amd_power_file):
                with open(amd_power_file, 'w') as f:
                    f.write("auto")
                logger.log("GPU performance reset to auto")
        except Exception as e:
            logger.log(f"Failed to reset GPU performance: {e}", LogLevel.DEBUG)
    
    def monitor_performance_metrics(self):
        """Monitor performance metrics continuously"""
        def monitor_loop():
            while True:
                try:
                    timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
                    battery_level = self.battery_status.level
                    power_consumption = self.battery_status.power_consumption
                    current_refresh_rate = self._get_current_refresh_rate()
                    
                    # Record metrics
                    with open(METRICS_FILE, 'a') as f:
                        f.write(f"{timestamp},battery:{battery_level}%,power:{power_consumption}W,refresh:{current_refresh_rate}Hz
")
                    
                    time.sleep(30)
                except Exception as e:
                    logger.log(f"Metrics monitoring error: {e}", LogLevel.DEBUG)
                    time.sleep(30)
        
        # Start monitoring in background
        import threading
        monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
        monitor_thread.start()
        logger.log("Performance monitoring started")
    
    def _get_current_refresh_rate(self) -> Optional[float]:
        """Get current refresh rate"""
        try:
            result = subprocess.run(["xrandr"], capture_output=True, text=True, timeout=3)
            if result.returncode == 0:
                match = re.search(r'*(d+.?d*)', result.stdout)
                if match:
                    return float(match.group(1))
        except Exception as e:
            logger.log(f"Failed to get current refresh rate: {e}", LogLevel.DEBUG)
        return None
    
    def run(self):
        """Main execution method"""
        try:
            # Start performance monitoring
            self.monitor_performance_metrics()
            
            # Determine power status
            power_status = self.get_power_status()
            
            if power_status:
                self.apply_ac_settings()
            else:
                mode = self.intelligent_battery_mode_selection()
                self.apply_enhanced_battery_settings(mode)
            
            logger.log("Enhanced power management initialization complete")
            
            # Keep running for service mode
            while True:
                time.sleep(60)
                
                # Re-evaluate power status periodically
                new_power_status = self.get_power_status()
                if new_power_status != power_status:
                    power_status = new_power_status
                    if power_status:
                        self.apply_ac_settings()
                    else:
                        mode = self.intelligent_battery_mode_selection()
                        self.apply_enhanced_battery_settings(mode)
        
        except KeyboardInterrupt:
            logger.log("Power manager stopped by user")
        except Exception as e:
            logger.log(f"Power manager error: {e}", LogLevel.ERROR)

if __name__ == "__main__":
    # Check for required dependencies
    required_commands = ["xrandr", "acpi", "brightnessctl"]
    missing_commands = []
    
    for cmd in required_commands:
        if not subprocess.run(["which", cmd], capture_output=True).returncode == 0:
            missing_commands.append(cmd)
    
    if missing_commands:
        print(f"Error: Missing required commands: {', '.join(missing_commands)}")
        print("Please install them using:")
        print("sudo dnf install -y xorg-x11-server-utils acpi brightnessctl")
        sys.exit(1)
    
    # Run the power manager
    manager = PowerManager()
    manager.run()
