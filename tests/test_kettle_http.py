"""Unit tests for the Fellow Stagg HTTP CLI parsers.

Sample bodies are based on live CLI output captured in docs/CLI_TESTING.md.
"""
from kettle_http import KettleHttpClient, _first_not_none

# Live-captured style bodies
STATE_BODY = (
    "scrname=wnd value=0 mode=S_Heat tempr=37.82 C temprT=40 C "
    "clock=22:21 units=1 nw=0"
)
SETTINGS_BODY = (
    "clockmode=1 hold=15 schedon=1 schtime=0:0 schtempr=176 offset_temp=-66879 "
    "bricky=0 Repeat_sched=0 boil=1 altitude=100 ft language=0"
)


class TestParseBoil:
    def test_boil_on(self):
        # Regression: the old regex (r"\boil") could never match "boil=1"
        assert KettleHttpClient._parse_boil("boil=1") is True

    def test_boil_off(self):
        assert KettleHttpClient._parse_boil("boil = 0") is False

    def test_boil_missing(self):
        assert KettleHttpClient._parse_boil("clockmode=1 hold=15") is None

    def test_boil_from_settings_body(self):
        assert KettleHttpClient._parse_boil(SETTINGS_BODY) is True


class TestParseTemps:
    def test_current_temp_celsius(self):
        client = KettleHttpClient("http://k")
        temp, unit = client._parse_temp(STATE_BODY)
        assert temp == 37.82
        assert unit == "C"

    def test_target_temp_celsius(self):
        client = KettleHttpClient("http://k")
        temp, unit = client._parse_target_temp(STATE_BODY)
        assert temp == 40.0
        assert unit == "C"

    def test_fahrenheit_converted_to_celsius(self):
        client = KettleHttpClient("http://k")
        temp, unit = client._parse_temp("tempr=212 F")
        assert unit == "F"
        assert round(temp, 1) == 100.0

    def test_nan_returns_none(self):
        client = KettleHttpClient("http://k")
        assert client._parse_temp("tempr=nan") == (None, None)


class TestParseMode:
    def test_simple_mode(self):
        assert KettleHttpClient._parse_mode(STATE_BODY) == "S_HEAT"

    def test_mode_with_timer_suffix(self):
        assert KettleHttpClient._parse_mode("mode=S_Heat+timer") == "S_HEAT+TIMER"

    def test_power(self):
        assert KettleHttpClient._parse_power("S_HEAT") is True
        assert KettleHttpClient._parse_power("S_OFF") is False
        assert KettleHttpClient._parse_power(None) is None

    def test_hold(self):
        assert KettleHttpClient._parse_hold("S_HOLD") is True
        assert KettleHttpClient._parse_hold("S_HEAT") is False
        assert KettleHttpClient._parse_hold("S_HOLD+timer") is True


class TestParseClock:
    def test_clock(self):
        assert KettleHttpClient._parse_clock(STATE_BODY) == "22:21"

    def test_clock_pads_and_wraps(self):
        assert KettleHttpClient._parse_clock("clock=7:5") == "07:05"

    def test_clock_mode(self):
        assert KettleHttpClient._parse_clock_mode(SETTINGS_BODY) == 1
        assert KettleHttpClient._parse_clock_mode("clockmode=9") is None


class TestParseSchedule:
    def test_schedule_time_colon_format(self):
        assert KettleHttpClient._parse_schedule_time("schtime=7:30") == {
            "hour": 7,
            "minute": 30,
        }

    def test_schedule_time_encoded(self):
        # (7 << 8) | 30 = 1822
        assert KettleHttpClient._parse_schedule_time("schtime=1822") == {
            "hour": 7,
            "minute": 30,
        }

    def test_schedule_temp_f_to_c(self):
        client = KettleHttpClient("http://k")
        # 176 F = 80 C
        assert round(client._parse_schedule_temp(SETTINGS_BODY), 1) == 80.0

    def test_schedule_temp_out_of_range(self):
        client = KettleHttpClient("http://k")
        assert client._parse_schedule_temp("schtempr=0") is None

    def test_schedon(self):
        assert KettleHttpClient._parse_schedon_value(SETTINGS_BODY) == 1
        assert KettleHttpClient._parse_schedule_enabled("schedon=0") is False
        assert KettleHttpClient._parse_schedule_repeat(SETTINGS_BODY) == 0


class TestParseTimers:
    def test_countdown_pre_start(self):
        minutes, phase = KettleHttpClient._parse_countdown("mode=S_Heat value=3")
        assert minutes == 3
        assert phase == "pre_start"

    def test_countdown_prefers_time_over_value(self):
        minutes, phase = KettleHttpClient._parse_countdown(
            "mode=S_Hold value=0 time 1:10"
        )
        assert minutes == 1
        assert phase == "hold"

    def test_countdown_off_when_standby(self):
        assert KettleHttpClient._parse_countdown("mode=S_Off value=3") == (None, None)

    def test_timer_time(self):
        display, total = KettleHttpClient._parse_timer_time(
            "mode=S_Heat+timer Main: time 3:45 temp 90"
        )
        assert display == "3:45"
        assert total == 225

    def test_timer_none_when_idle(self):
        assert KettleHttpClient._parse_timer_time("mode=S_Off") == (None, None)


class TestParseFlags:
    def test_units_flag(self):
        assert KettleHttpClient._parse_units_flag("units=1") == "C"
        assert KettleHttpClient._parse_units_flag("units=0") == "F"
        assert KettleHttpClient._parse_units_flag("") is None

    def test_lifted_only_on_nan(self):
        assert KettleHttpClient._parse_lifted("tempr=nan") is True
        assert KettleHttpClient._parse_lifted(STATE_BODY) is False

    def test_no_water(self):
        assert KettleHttpClient._parse_no_water("nw=1") is True
        assert KettleHttpClient._parse_no_water(STATE_BODY) is False

    def test_hold_setting(self):
        assert KettleHttpClient._parse_hold_setting(SETTINGS_BODY) == 15

    def test_fwinfo(self):
        body = "Current version: 1.2.5CL cli\nota_1 1.2.5CL"
        assert KettleHttpClient._parse_fwinfo(body) == "1.2.5CL"


class TestNewParsers:
    def test_altitude(self):
        assert KettleHttpClient._parse_altitude(SETTINGS_BODY) == 100.0
        assert KettleHttpClient._parse_altitude("clockmode=1") is None

    def test_language(self):
        assert KettleHttpClient._parse_language(SETTINGS_BODY) == 0
        assert KettleHttpClient._parse_language("language=6") == 6
        assert KettleHttpClient._parse_language("") is None


class TestHelpers:
    def test_first_not_none_prefers_zero_over_fallback(self):
        # Regression: "or" chains let 0 from prtsettings fall through to stale state
        assert _first_not_none(0, 15) == 0
        assert _first_not_none(None, 15) == 15
        assert _first_not_none(None, None) is None
        assert _first_not_none(False, True) is False

    def test_encode_cli_command(self):
        assert KettleHttpClient._encode_cli_command("setstate S_Heat") == "setstate+S_Heat"

    def test_base_url_normalization(self):
        client = KettleHttpClient("192.168.1.86")
        assert client._cli_url == "http://192.168.1.86/cli"
        client = KettleHttpClient("http://192.168.1.86/")
        assert client._cli_url == "http://192.168.1.86/cli"

    def test_screen_name(self):
        assert KettleHttpClient._parse_screen_name(STATE_BODY) == "wnd"
